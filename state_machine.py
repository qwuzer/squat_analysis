import time


class State:
    EMPTY          = 'EMPTY'
    ADJUSTING      = 'ADJUSTING'
    STABLE_STANDING = 'STABLE_STANDING'
    SQUATTING      = 'SQUATTING'


class SquatStateMachine:

    def __init__(self, stats):
        from config import (STAND_THRESH_STD_FACTOR, STAND_THRESH_MIN_PCT,
                            PERSON_ON_HOLD_TIME, STABILITY_CV_THRESH,
                            STABILITY_HOLD_TIME, SQUAT_THRESH_STD_FACTOR,
                            SQUAT_THRESH_MIN_PCT, MIN_SQUAT_DURATION)
        self.stats = stats
        self.state = State.EMPTY

        # Config
        self._stand_std_fac   = STAND_THRESH_STD_FACTOR
        self._stand_min_pct   = STAND_THRESH_MIN_PCT
        self._person_hold     = PERSON_ON_HOLD_TIME
        self._stab_cv         = STABILITY_CV_THRESH
        self._stab_hold       = STABILITY_HOLD_TIME
        self._squat_std_fac   = SQUAT_THRESH_STD_FACTOR
        self._squat_min_pct   = SQUAT_THRESH_MIN_PCT
        self._min_squat_dur   = MIN_SQUAT_DURATION

        # Baselines
        self.B0        = None           # [ch0..ch3] environment (per-channel means)
        self.B0_agg    = 0.0
        self.B0_std    = 0.0
        self.B1        = None           # [ch0..ch3] body-weight baseline
        self.B1_net    = None           # B1 - B0 (drift-corrected)
        self.B1_agg    = 0.0
        self.B1_std    = 0.0
        self.CoP_B1    = (0.0, 0.0)

        # Diagnostic (read by status bar)
        self._last_margin      = 0.0

        # Timers
        self._entry_ts         = time.monotonic()
        self._above_thresh_ts  = None
        self._stable_ts        = None
        self._squat_start_ts   = None

        # Rep tracking
        self.reps              = []
        self._squat_cop_buf    = []     # (ts, cop, agg) during current squat

        # Callbacks
        self.on_state_change   = None   # fn(new_state)
        self.on_rep_complete   = None   # fn(rep_dict)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def update(self, ts, readings):
        s = self.stats
        agg  = s.rolling_agg_mean
        std  = s.rolling_agg_std
        cop  = s.rolling_cop

        if self.state == State.EMPTY:
            self._do_empty(ts, agg, std)

        elif self.state == State.ADJUSTING:
            self._do_adjusting(ts, agg, std)

        elif self.state == State.STABLE_STANDING:
            self._do_stable(ts, agg, std, cop)

        elif self.state == State.SQUATTING:
            self._do_squatting(ts, agg, std, cop)

    def cop_relative(self):
        """Smoothed CoP offset from the B1 standing centre (for the UI dot)."""
        cx, cy = self.stats.rolling_cop
        return (cx - self.CoP_B1[0], cy - self.CoP_B1[1])

    # ------------------------------------------------------------------
    # Per-state logic
    # ------------------------------------------------------------------

    def _do_empty(self, ts, agg, std):
        # Continuously refresh B0 from rolling means
        ch = self.stats.channel_means
        self.B0     = ch[:]
        self.B0_agg = sum(ch)
        self.B0_std = self.stats.rolling_agg_std

        # Compare instantaneous aggregate against rolling B0.
        # Using the rolling mean here would always give delta≈0 because B0 is
        # derived from the same window — the instant reading leads the mean by
        # the full window duration when someone steps on.
        instant_agg = sum(self.stats.last_readings)
        margin = self._stand_margin(self.B0_agg, self.B0_std)
        self._last_margin = margin  # expose for status bar
        if abs(instant_agg - self.B0_agg) > margin:
            if self._above_thresh_ts is None:
                self._above_thresh_ts = ts
            elif ts - self._above_thresh_ts >= self._person_hold:
                self._transition(State.ADJUSTING, ts)
        else:
            self._above_thresh_ts = None

    def _do_adjusting(self, ts, agg, std):
        # If aggregate returns to B0 level → EMPTY
        margin = self._stand_margin(self.B0_agg, self.B0_std)
        if abs(agg - self.B0_agg) <= margin:
            self._transition(State.EMPTY, ts)
            return

        # Stability check: CV (std/mean) below threshold
        cv = std / max(agg, 1.0)
        if cv < self._stab_cv:
            if self._stable_ts is None:
                self._stable_ts = ts
            elif ts - self._stable_ts >= self._stab_hold:
                self._transition(State.STABLE_STANDING, ts)
        else:
            self._stable_ts = None

    def _do_stable(self, ts, agg, std, cop):
        # Capture / refresh B1 during first 0.3 s in this state
        if ts - self._entry_ts < 0.3:
            self._capture_B1()

        # If person steps off → EMPTY
        margin = self._stand_margin(self.B0_agg, self.B0_std)
        if abs(agg - self.B0_agg) <= margin:
            self._transition(State.EMPTY, ts)
            return

        # Squat start: detect any significant deviation from B1 (up OR down)
        squat_margin = self._squat_margin(self.B1_std)
        if abs(agg - self.B1_agg) > squat_margin:
            self._transition(State.SQUATTING, ts)

    def _do_squatting(self, ts, agg, std, cop):
        self._squat_cop_buf.append((ts, cop, agg))

        squat_margin = self._squat_margin(self.B1_std)
        if abs(agg - self.B1_agg) <= squat_margin:
            duration = ts - self._squat_start_ts
            if duration >= self._min_squat_dur:
                self._complete_rep(ts)
            self._transition(State.STABLE_STANDING, ts)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _stand_margin(self, b0_agg, b0_std):
        """Returns the deviation margin (not an absolute threshold)."""
        std_part = self._stand_std_fac * max(b0_std, 1.0)
        pct_part = self._stand_min_pct * max(b0_agg, 1.0)
        return max(std_part, pct_part)

    def _squat_margin(self, b1_std):
        """Deviation from B1 required to enter/exit SQUATTING state."""
        std_part = self._squat_std_fac * max(b1_std, 1.0)
        pct_part = self._squat_min_pct * max(self.B1_agg, 1.0)
        return max(std_part, pct_part)

    def _capture_B1(self):
        ch = self.stats.channel_means
        self.B1     = ch[:]
        self.B1_agg = sum(ch)
        self.B1_std = self.stats.rolling_agg_std
        self.B1_net = [b1 - b0 for b1, b0 in zip(self.B1, self.B0 or [0]*4)]
        total = max(self.B1_agg, 1.0)
        b = self.B1
        self.CoP_B1 = (
            0.5 * (b[3] + b[2] - b[0] - b[1]) / total,
            0.5 * (b[0] + b[3] - b[1] - b[2]) / total,
        )

    def _complete_rep(self, ts):
        if not self._squat_cop_buf:
            return
        # Peak = point of maximum absolute deviation from B1 (up or down)
        peak   = max(self._squat_cop_buf, key=lambda e: abs(e[2] - self.B1_agg))
        _, peak_cop, peak_agg = peak
        dev = (peak_cop[0] - self.CoP_B1[0], peak_cop[1] - self.CoP_B1[1])
        rep = {
            'duration':      ts - self._squat_start_ts,
            'peak_magnitude': peak_agg - self.B1_agg,   # positive = above B1, negative = dip
            'cop_at_peak':   peak_cop,
            'cop_deviation': dev,
        }
        self.reps.append(rep)
        if self.on_rep_complete:
            self.on_rep_complete(rep)

    def _transition(self, new_state, ts):
        self.state     = new_state
        self._entry_ts = ts
        # Reset per-state timers
        self._above_thresh_ts = None
        self._stable_ts       = None
        if new_state == State.SQUATTING:
            self._squat_start_ts = ts
            self._squat_cop_buf  = []
        if self.on_state_change:
            self.on_state_change(new_state)
