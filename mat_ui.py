"""
mat_ui.py  —  Yoga mat pressure monitor
3 mats side-by-side, each with 4 channels in a 2×2 grid.
COM5 → ch 0-3   (mat 2)
COM6 → ch 4-7   (mat 1)
COM7 → ch 8-11  (mat 3)
Run: python mat_ui.py
"""

import collections
import math
import random
import threading
import time
import tkinter as tk

try:
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

# ── config ────────────────────────────────────────────────────────────────────
PORTS      = ["COM7", "COM6", "COM5"]
BAUD       = 1_500_000
UPDATE_MS  = 33
MAX_VAL    = 16383   # sensor is ~14-bit; resting baseline is ~6000

# The hardware occasionally emits spurious spikes (seen in the vendor's own logs:
# values like 2e7 / 5e7 amid a ~6000 baseline). Any reading outside this window
# is treated as corrupt and dropped, so one bad sample can't surge the display
# or latch the press state. Keep the last good value instead.
RAW_MIN    = 0
RAW_MAX    = 16383

# Each $SILINO sentence ends in a *HH NMEA checksum (XOR of the chars between
# $ and *). At 1.5 Mbaud the OS buffer can overflow and hand us truncated/merged
# frames; a truncated number (e.g. 6037→60) still looks in-range, so only the
# checksum catches it. The vendor app validates it too — that's why its log is
# clean. Set False only if frames are being rejected wholesale (wrong algorithm).
VALIDATE_CHECKSUM = True

# (top-left, top-right, bottom-left, bottom-right)
MAT_CHANNELS = [
    [3, 0, 2, 1],
    [4, 6, 5, 7],
    [11, 8, 10, 9],
]
MAT_LABELS = ["Mat 1", "Mat 2", "Mat 3"]

# ── palette ───────────────────────────────────────────────────────────────────
BG       = '#0f0f1a'
BG_MAT   = '#1a1a2a'
BG_EMPTY = '#121220'
BORDER   = '#2a2a3a'
MUTED    = '#55556a'
FG       = '#dddde8'

C_LOW    = '#1D9E75'
C_MID    = '#BA7517'
C_HIGH   = '#D85A30'
C_EMPTY  = '#1e1e2e'

# ── 3-state colors (net = reading − this channel's zero baseline) ──────────────
S_EMPTY  = '#E8E8F0'   # white — no pressure (net ≈ 0)
S_PRESS  = '#FF3B30'   # red   — pressure applied
S_OTHER  = '#2D7DFF'   # blue  — reserved / in-between (unused for now)

NET_MAX        = 1000  # relative value that fills the bar fully

# ── empty/pressed state machine ───────────────────────────────────────────────
# Each channel starts EMPTY. A sudden jump UP flips it to PRESSED; a sudden drop
# flips it back to EMPTY and re-seeds the baseline to the new resting floor — so
# a release that settles higher than before never reads as phantom pressure.
# While EMPTY the baseline slowly tracks drift; while PRESSED it is frozen.
# Detection is edge-based (slope), so the absolute level doesn't matter.
SLOPE_WINDOW  = 5      # samples back used to measure slope (catches gradual presses)
ONSET_SLOPE   = 200    # raw rising ≥ this over the window  → EMPTY → PRESSED
RELEASE_SLOPE = -200   # raw dropping ≤ this over the window → PRESSED → EMPTY
RELEASE_LEVEL = 80     # net back within this of the frozen floor → PRESSED → EMPTY
DRIFT_ALPHA   = 0.30   # baseline drift-tracking speed while EMPTY

# ── per-mat balance / contact detection ───────────────────────────────────────
# State machine per mat:  EMPTY → ADJUSTING → HOLDING
#
# A jump finds the *moment* someone steps on; levels decide *which* bands they
# are standing on. Ten seconds into a still hold every band has slope ≈ 0 —
# loaded and unloaded look identical — so contact must be read from the level
# above the empty-mat baseline (B0), latched once the aggregate stops moving.
#
# Onset compares the INSTANTANEOUS aggregate against the rolling B0_agg.
# Rolling-vs-rolling gives Δ≈0 and the transition never fires.
B0_WINDOW     = 60     # samples of empty-mat rolling baseline (~2 s at 30 fps)
SETTLE_WINDOW = 30     # samples of aggregate history for the settle test (~1 s)
ONSET_AGG     = 300    # instantaneous aggregate this far above B0_agg → stepped on
OFFSET_AGG    = 150    # ...and back below this → stepped off (hysteresis gap)
SETTLE_STD    = 60     # aggregate std below this → settled, safe to snapshot B1
MIN_SETTLE_S  = 1.0    # minimum time in ADJUSTING before B1 can be captured
CONTACT_FRAC  = 0.15   # band is "in contact" at ≥ this fraction of the peak band

# Balance colours: how far from their own settled neutral before it's worth flagging
BAL_OK, BAL_WARN = 0.08, 0.20


# ── band canvas ───────────────────────────────────────────────────────────────

class BandCanvas(tk.Canvas):
    # natural size — also the minimum before text/bar would overlap. The widget
    # grows past this to fill its grid cell; all geometry is recomputed from the
    # live canvas size in _layout() so it stays proportional when the window resizes.
    W, H = 106, 84

    def __init__(self, parent, ch_num):
        super().__init__(parent, width=self.W, height=self.H,
                         bg=BG_MAT, highlightthickness=1,
                         highlightbackground=BORDER)
        self._ch  = ch_num
        self._val = 0
        self._contact = False

        # items are created once; positions/fonts are (re)set in _layout()
        self._ch_id  = self.create_text(0, 0, anchor='w', text=f'ch {ch_num}',
                                         fill=MUTED, font=('Courier', 9))
        self._val_id = self.create_text(0, 0, anchor='w', text='—',
                                         fill=FG, font=('Courier', 17, 'bold'))
        self._track  = self.create_rectangle(0, 0, 0, 0, fill=BORDER, outline='')
        self._bar    = self.create_rectangle(0, 0, 0, 0, fill=C_LOW, outline='')

        self._geom = (10, 70, 74, self.W)   # (pad, bar_y1, bar_y2, w) until first Configure
        self.bind('<Configure>', lambda e: self._layout(e.width, e.height))

    def _layout(self, w, h):
        """Recompute item positions and font sizes for the current canvas size."""
        pad    = max(6, int(w * 0.09))
        v_font = max(11, int(h * 0.22))
        c_font = max(8, int(h * 0.13))
        self.itemconfig(self._val_id, font=('Courier', v_font, 'bold'))
        self.itemconfig(self._ch_id,  font=('Courier', c_font))
        self.coords(self._ch_id,  pad, int(h * 0.17))
        self.coords(self._val_id, pad, int(h * 0.52))
        bar_y1, bar_y2 = int(h * 0.82), int(h * 0.88)
        self.coords(self._track, pad, bar_y1, w - pad, bar_y2)
        self._geom = (pad, bar_y1, bar_y2, w)
        self._draw_bar()

    def _draw_bar(self):
        pad, y1, y2, w = self._geom
        pct = max(0.0, min(1.0, self._val / MAX_VAL))
        x2 = pad + int((w - pad * 2) * pct)
        self.coords(self._bar, pad, y1, x2, y2)

    def set_value(self, val):
        # pure raw display — no baseline, no zeroing, no press/empty logic.
        # show exactly what the mat reports, with a bar scaled to full range.
        self._val = val
        self.itemconfig(self._val_id, text=str(val), fill=FG)
        self._draw_bar()

    def rezero(self):
        """No-op in raw mode (kept so the 'r' key binding still works)."""
        pass

    def set_contact(self, on):
        """Outline the band while it is carrying load (part of the contact set)."""
        if on == self._contact:
            return
        self._contact = on
        self.config(highlightbackground=S_OTHER if on else BORDER)
        self.itemconfig(self._ch_id, fill=S_OTHER if on else MUTED)


# ── balance strip ─────────────────────────────────────────────────────────────

class BalanceStrip(tk.Canvas):
    """Left/right balance readout, −1…+1, zeroed on the pose's settled neutral.

    Bands integrate over their whole area, so this is a direction, not a
    position: it says which way weight moved since they settled into the pose.
    """

    H   = 54
    PAD = 16

    def __init__(self, parent):
        super().__init__(parent, height=self.H, bg=BG_MAT,
                         highlightthickness=1, highlightbackground=BORDER)
        self._msg   = self.create_text(0, 0, anchor='w', text='', fill=MUTED,
                                       font=('Arial', 8))
        self._num   = self.create_text(0, 0, anchor='e', text='', fill=MUTED,
                                       font=('Courier', 9, 'bold'))
        self._track = self.create_line(0, 0, 0, 0, fill=BORDER, width=2)
        self._zero  = self.create_line(0, 0, 0, 0, fill=MUTED, width=1)
        self._dot   = self.create_oval(0, 0, 0, 0, fill=MUTED, outline='')
        self.itemconfigure(self._dot, state='hidden')
        self._x     = None
        self._geom  = (self.PAD, self.PAD + 80, 38)
        self.bind('<Configure>', lambda e: self._layout(e.width, e.height))

    def _layout(self, w, h):
        pad, ty = self.PAD, int(h * 0.70)
        self.coords(self._msg, pad, int(h * 0.28))
        self.coords(self._num, w - pad, int(h * 0.28))
        self.coords(self._track, pad, ty, w - pad, ty)
        cx = w / 2
        self.coords(self._zero, cx, ty - 7, cx, ty + 7)   # their own neutral
        self._geom = (pad, w - pad, ty)
        self._place()

    def _place(self):
        if self._x is None:
            self.itemconfigure(self._dot, state='hidden')
            return
        x0, x1, ty = self._geom
        cx = (x0 + x1) / 2
        px = cx + (cx - x0) * max(-1.0, min(1.0, self._x))
        r  = 6
        self.coords(self._dot, px - r, ty - r, px + r, ty + r)
        mag = abs(self._x)
        col = C_LOW if mag < BAL_OK else (C_MID if mag < BAL_WARN else C_HIGH)
        self.itemconfigure(self._dot, state='normal', fill=col)

    def show(self, msg, x, msg_color=MUTED):
        """x is the signed balance, or None to hide the dot and show msg only."""
        self._x = x
        self.itemconfig(self._msg, text=msg, fill=msg_color)
        if x is None:
            self.itemconfig(self._num, text='')
        else:
            side = 'centred' if abs(x) < BAL_OK else ('right' if x > 0 else 'left')
            self.itemconfig(self._num, text=f'{abs(x):.2f} {side}',
                            fill=FG if abs(x) >= BAL_OK else MUTED)
        self._place()



def _hex_shade(hex_col, factor):
    """Darken hex_col by scaling each channel toward black by `factor` (0..1)."""
    r = int(int(hex_col[1:3], 16) * factor)
    g = int(int(hex_col[3:5], 16) * factor)
    b = int(int(hex_col[5:7], 16) * factor)
    return f'#{r:02x}{g:02x}{b:02x}'


# ── mat widget ────────────────────────────────────────────────────────────────

class MatWidget(tk.Frame):
    GAP = 6

    def __init__(self, parent, label, channels, empty=False):
        super().__init__(parent, bg=BG)
        self._bands = {}

        tk.Label(self, text=label, bg=BG, fg=MUTED,
                 font=('Arial', 9)).pack(anchor='w', pady=(0, 4))

        shell = tk.Frame(self, bg=BORDER, padx=1, pady=1)
        shell.pack(fill='both', expand=True)

        inner = tk.Frame(shell, bg=BG_EMPTY if empty else BG_MAT,
                         padx=self.GAP, pady=self.GAP)
        inner.pack(fill='both', expand=True)

        # the 2×2 grid shares space evenly, so every band grows with the window
        for i in range(2):
            inner.rowconfigure(i, weight=1, uniform='band')
            inner.columnconfigure(i, weight=1, uniform='band')

        for row in range(2):
            for col in range(2):
                ch = channels[row * 2 + col]
                pad = (0 if col == 0 else self.GAP, 0), (0 if row == 0 else self.GAP, 0)
                if empty:
                    ph = tk.Frame(inner, width=BandCanvas.W, height=BandCanvas.H,
                                  bg=C_EMPTY, highlightthickness=1,
                                  highlightbackground='#22223a')
                    ph.grid(row=row, column=col, sticky='nsew',
                            padx=pad[0], pady=pad[1])
                    ph.pack_propagate(False)
                else:
                    band = BandCanvas(inner, ch)
                    band.grid(row=row, column=col, sticky='nsew',
                              padx=pad[0], pady=pad[1])
                    self._bands[ch] = band

        self._strip = None if empty else BalanceStrip(self)
        if self._strip is not None:
            self._strip.pack(fill='x', pady=(self.GAP, 0))

    def update(self, ch, val):
        if ch in self._bands:
            self._bands[ch].set_value(val)

    def apply_state(self, st):
        """Push one mat's contact set and balance into the widgets."""
        for ch, band in self._bands.items():
            band.set_contact(ch in st.contact)
        if self._strip is None:
            return
        if st.state == MatState.EMPTY:
            self._strip.show('empty — step on to begin', None)
        elif st.state == MatState.ADJUSTING:
            self._strip.show('settling…', None, C_MID)
        elif not st.valid_lr:
            # both feet inside one column: the bands cannot resolve L/R at all,
            # so show nothing rather than a number that means nothing
            self._strip.show('contact on one side only — reposition feet',
                             None, C_HIGH)
        else:
            self._strip.show('holding', st.x)

    def rezero_all(self):
        for band in self._bands.values():
            band.rezero()


# ── per-mat balance state ─────────────────────────────────────────────────────

def _stdev(seq):
    n = len(seq)
    if n < 2:
        return 0.0
    mean = sum(seq) / n
    return (sum((v - mean) ** 2 for v in seq) / n) ** 0.5


class MatState:
    """Empty baseline, contact set and left/right balance for one mat.

    B0       — per-channel rolling mean while EMPTY. Re-seeded on every step-off,
               because release hysteresis leaves the mat resting on a new floor
               rather than the old one.
    B1       — snapshot taken once the aggregate has stopped moving; net = B1 − B0.
    contact  — bands carrying at least CONTACT_FRAC of the most-loaded band. A
               *relative* test, so it needs no tuning per body weight.

    Balance is the ratio (R − L) / (R + L) of net load, reported relative to its
    value at settle — nobody stands symmetric, so their own neutral is the only
    meaningful zero. Being a ratio it is unchanged by creep that scales both
    sides alike, which is most of the drift visible on the raw charts.
    """

    EMPTY, ADJUSTING, HOLDING = 'empty', 'adjusting', 'holding'

    def __init__(self, channels):
        self.channels = tuple(channels)          # (TL, TR, BL, BR)
        self._b0_buf  = {ch: collections.deque(maxlen=B0_WINDOW)
                         for ch in self.channels}
        self._agg_buf = collections.deque(maxlen=SETTLE_WINDOW)
        self.reset()

    def reset(self):
        self.state    = self.EMPTY
        self.B0       = None
        self.contact  = set()
        self.valid_lr = False
        self.x = self.y = 0.0        # live balance, relative to settle
        self._ref     = (0.0, 0.0)
        self._t_enter = 0.0
        for buf in self._b0_buf.values():
            buf.clear()
        self._agg_buf.clear()

    def _baseline(self, oldest_half=False):
        """Mean of the empty-mat buffer. `oldest_half` drops the newest samples,
        which hold the ramp-up of the step that just triggered the transition."""
        b0 = {}
        for ch, buf in self._b0_buf.items():
            vals = list(buf)
            if oldest_half and len(vals) >= 4:
                vals = vals[:len(vals) // 2]
            b0[ch] = sum(vals) / len(vals) if vals else 0.0
        return b0

    def _nets(self, vals):
        # clamp at 0: a band can read below B0 as load moves off it, and a
        # negative term would distort the ratio
        return {ch: max(0.0, vals[ch] - self.B0[ch]) for ch in self.channels}

    def _cop(self, nets):
        """Normalised −1…+1 balance. +x = right, +y = toward the top of the mat."""
        tl, tr, bl, br = self.channels
        total = sum(nets.values())
        if total <= 0:
            return 0.0, 0.0
        x = ((nets[tr] + nets[br]) - (nets[tl] + nets[bl])) / total
        y = ((nets[tl] + nets[tr]) - (nets[bl] + nets[br])) / total
        return x, y

    def update(self, vals, now):
        agg = sum(vals.values())
        self._agg_buf.append(agg)

        if self.state == self.EMPTY:
            # instantaneous aggregate vs the rolling empty baseline; comparing
            # rolling to rolling gives Δ≈0 and this never fires
            ready = len(self._b0_buf[self.channels[0]]) >= B0_WINDOW // 2
            if ready and agg - sum(self._baseline().values()) > ONSET_AGG:
                self.B0 = self._baseline(oldest_half=True)
                self.state = self.ADJUSTING
                self._t_enter = now
            else:
                for ch, v in vals.items():
                    self._b0_buf[ch].append(v)
            return

        if agg - sum(self.B0.values()) < OFFSET_AGG:        # stepped off
            self.reset()
            return

        if self.state == self.ADJUSTING:
            if (now - self._t_enter >= MIN_SETTLE_S
                    and len(self._agg_buf) >= SETTLE_WINDOW
                    and _stdev(self._agg_buf) < SETTLE_STD):
                self._capture(vals)
            return

        x, y = self._cop(self._nets(vals))
        self.x, self.y = x - self._ref[0], y - self._ref[1]

    def _capture(self, vals):
        nets = self._nets(vals)
        peak = max(nets.values())
        self.contact = ({ch for ch, n in nets.items() if n >= CONTACT_FRAC * peak}
                        if peak > 0 else set())
        tl, tr, bl, br = self.channels
        # left/right is only observable if contact straddles the L/R boundary:
        # a band integrates over its whole area, so two feet inside one band
        # cannot be told apart however the weight moves between them
        self.valid_lr = bool({tl, bl} & self.contact) and bool({tr, br} & self.contact)
        self._ref = self._cop(nets)
        self.x = self.y = 0.0
        self.state = self.HOLDING



# ── serial reader thread ──────────────────────────────────────────────────────

def _checked_body(text):
    """Validate the NMEA *HH checksum and return the payload between $ and *.

    Returns the body string if the checksum matches (or validation is off),
    else None. Checksum = XOR of every char between '$' and '*'.
    """
    body, sep, cksum = text[1:].partition('*')
    if not VALIDATE_CHECKSUM:
        return body
    if not sep:                      # no '*HH' at all → can't trust the frame
        return None
    calc = 0
    for c in body:
        calc ^= ord(c)
    try:
        return body if calc == int(cksum[:2], 16) else None
    except ValueError:
        return None


class PortReader(threading.Thread):
    def __init__(self, port, data, ch_offset):
        super().__init__(daemon=True)
        self.port      = port
        self._data     = data
        self._offset   = ch_offset
        self.status    = 'connecting…'
        self.bad       = 0           # count of frames rejected by checksum

    def run(self):
        # outer loop: if the port drops or errors, back off and reconnect
        # instead of letting the thread die permanently
        while True:
            try:
                with serial.Serial(self.port, baudrate=BAUD,
                                   bytesize=8, parity='N', stopbits=1, timeout=1) as ser:
                    self.status = 'ok'
                    while True:
                        raw = ser.readline()
                        if not raw:
                            continue
                        text = raw.decode('ascii', errors='replace').strip()
                        if not text.upper().startswith('$SILINO'):
                            continue
                        body = _checked_body(text)
                        if body is None:                     # bad/missing checksum
                            self.bad += 1
                            continue
                        fields = body.split(',')
                        if len(fields) < 5:
                            continue
                        try:
                            num_ch = int(fields[3])
                        except ValueError:
                            continue                     # corrupt frame — skip, don't die
                        # each port owns a fixed 4-channel slot; never write past it
                        # so a bad num_ch can't bleed into another mat's channels
                        for i in range(min(num_ch, 4)):
                            idx = 4 + i
                            if idx < len(fields):
                                try:
                                    v = int(fields[idx])
                                except ValueError:
                                    continue            # non-numeric — keep last good
                                if RAW_MIN <= v <= RAW_MAX:
                                    self._data[self._offset + i] = v
                                # else: spurious spike — drop it, keep last good value
            except Exception as exc:
                self.status = str(exc)
                time.sleep(1)                            # back off, then reconnect


# ── demo mode (no serial) ─────────────────────────────────────────────────────

class DemoDriver:
    """Scripted stand-in for the hardware, so the contact/balance pipeline can be
    exercised without a mat. Each mat plays a different case:

      Mat 1 — someone steps on and slowly leans left↔right (the normal path)
      Mat 2 — stays empty (EMPTY never falsely triggers)
      Mat 3 — both feet in the left column, so left/right is not observable and
              the strip should say so instead of showing a number

    Sustained load also creeps ~15% over the first half-minute, which is the
    drift the ratio is supposed to cancel — watch the raw charts sag while the
    balance dot stays put.
    """

    REST   = 6000.0    # resting level of an unloaded band
    STEP_T = 4.0       # seconds of empty mat before anyone steps on
    RAMP   = 0.6       # how long the step-on takes
    LOADS  = {         # per mat, net counts per band when standing (TL,TR,BL,BR)
        0: (700, 700, 520, 520),
        1: (0, 0, 0, 0),
        2: (760, 0, 610, 0),
    }

    def __init__(self, data):
        self._data = data
        self._rng  = random.Random(7)
        self._t0   = time.monotonic()

    def step(self):
        t    = time.monotonic() - self._t0
        on   = max(0.0, min(1.0, (t - self.STEP_T) / self.RAMP))
        held = max(0.0, t - self.STEP_T - self.RAMP)
        creep = 1.0 - 0.15 * (1.0 - math.exp(-held / 25.0))
        lean  = 0.35 * math.sin(2 * math.pi * held / 12.0) if held > 2 else 0.0
        for m, channels in enumerate(MAT_CHANNELS):
            loads = self.LOADS[m]
            for i, ch in enumerate(channels):
                # channels are ordered (TL, TR, BL, BR) → odd index is the right side
                w = 1.0 + (lean if i % 2 else -lean)
                v = self.REST + loads[i] * on * creep * w
                self._data[ch] = int(max(0, min(MAX_VAL,
                                                v + self._rng.gauss(0, 6))))


# ── raw signal line chart ──────────────────────────────────────────────────────

CHART_CHANNELS = list(range(12))   # plot every channel (all 3 mats)
CHART_HISTORY  = 300    # samples kept on screen (~10 s at 30 fps)
CHART_W        = 680
CHART_H        = 460
ALIGN_H        = 300    # height of the baseline-aligned chart beneath the raw one
ALIGN_FLOOR    = 200    # min top-of-scale so a flat (all-rest) view isn't all noise
CHART_COLORS   = [
    '#FF5252', '#FF9800', '#FFD740', '#C6FF00',   # ch 0-3
    '#69F0AE', '#1DE9B6', '#40C4FF', '#448AFF',   # ch 4-7
    '#7C4DFF', '#E040FB', '#FF4081', '#BCAAA4',   # ch 8-11
]


class RawChart:
    """A line chart of the raw value of every channel.

    In `zeroed` mode each channel's *initial balance* — the first reading seen —
    is subtracted, so every line starts at 0 and reads `raw − initial`. The
    baseline is captured once and frozen, so a sustained press stays elevated for
    as long as it is held (it doesn't decay), which is what makes a per-band press
    obvious. Press `r` to re-capture the initial balance for every channel.
    """
    PAD_L, PAD_R, PAD_T, PAD_B = 52, 12, 14, 22

    def __init__(self, parent, channels, zeroed=False, height=CHART_H):
        self._channels = channels
        self._zeroed = zeroed
        self._hist = {ch: collections.deque(maxlen=CHART_HISTORY) for ch in channels}
        self._base = {}        # per-channel initial balance, frozen (zeroed mode only)
        # the width/height here are only initial hints; fill+expand lets the
        # canvas track its parent, and redraw() reads the live size each frame
        self.cv = tk.Canvas(parent, width=CHART_W, height=height,
                            bg=BG_MAT, highlightthickness=1,
                            highlightbackground=BORDER)
        self.cv.pack(fill='both', expand=True)

    def push(self, data):
        for ch in self._channels:
            if ch not in data:
                continue
            v = data[ch]
            if self._zeroed:
                if ch not in self._base:                  # capture the initial balance once
                    self._base[ch] = float(v)
                v = v - self._base[ch]                    # raw − initial (stays up while held)
            self._hist[ch].append(v)

    def rezero(self):
        """Re-capture the initial balance: forget baselines and clear history so
        each channel re-seeds to its current resting level on the next reading."""
        if not self._zeroed:
            return
        self._base.clear()
        for h in self._hist.values():
            h.clear()

    def redraw(self):
        cv = self.cv
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 2 or h < 2:                # not realised yet — nothing to draw onto
            return
        cv.delete('all')
        x0, y0 = self.PAD_L, self.PAD_T
        x1, y1 = w - self.PAD_R, h - self.PAD_B

        allvals = [v for ch in self._channels for v in self._hist[ch]]
        if not allvals:
            return
        if self._zeroed:
            # always keep 0 in view (rest line) and a minimum top so a flat,
            # all-resting view isn't zoomed into noise; allow negative (lift-off)
            vmin = min(0.0, min(allvals))
            vmax = max(ALIGN_FLOOR, max(allvals))
        else:
            vmin, vmax = min(allvals), max(allvals)
        if vmax - vmin < 1:
            vmax = vmin + 1
        margin = (vmax - vmin) * 0.08
        vmin -= margin
        vmax += margin
        span = vmax - vmin

        # horizontal grid + y-axis labels (auto-scaled to the visible data)
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            yy = y1 - (y1 - y0) * frac
            cv.create_line(x0, yy, x1, yy, fill=BORDER)
            cv.create_text(x0 - 6, yy, anchor='e', fill=MUTED,
                           font=('Courier', 8), text=f'{vmin + span * frac:.0f}')

        # brighter zero line: this is the resting level, so anything above it is
        # pressure on that band — the whole point of the aligned view
        if self._zeroed and vmin < 0 < vmax:
            yz = y1 - (y1 - y0) * ((0 - vmin) / span)
            cv.create_line(x0, yz, x1, yz, fill=MUTED, width=1)

        # one polyline per channel + legend with current raw value
        for i, ch in enumerate(self._channels):
            h = self._hist[ch]
            col = CHART_COLORS[i % len(CHART_COLORS)]
            cur = h[-1] if h else '—'
            cv.create_text(x1, y0 + 2 + i * 14, anchor='ne', fill=col,
                           font=('Courier', 9), text=f'ch {ch}: {cur}')
            if len(h) < 2:
                continue
            pts = []
            for j, v in enumerate(h):
                xx = x0 + (x1 - x0) * (j / (CHART_HISTORY - 1))
                yy = y1 - (y1 - y0) * ((v - vmin) / span)
                pts += [xx, yy]
            cv.create_line(*pts, fill=col, width=1.5)


# ── main app ──────────────────────────────────────────────────────────────────

class App:
    def __init__(self, root):
        self.root  = root
        self._data = {}
        self._demo = not HAS_SERIAL

        root.title('Yoga Mat Monitor')
        root.configure(bg=BG)
        root.resizable(True, True)
        root.minsize(860, 660)          # below this the bands/charts would overlap

        self._states = [MatState(chans) for chans in MAT_CHANNELS]

        self._build_ui()

        # press 'r' to re-capture the initial balance (aligned chart re-zeroes)
        # and force every mat back to EMPTY so B0/B1 are taken again
        root.bind('<r>', lambda e: (self._align.rezero(),
                                    [m.rezero_all() for m in self._mats],
                                    [st.reset() for st in self._states]))

        if self._demo:
            self._driver = DemoDriver(self._data)
        else:
            self._readers = []
            for i, port in enumerate(PORTS):
                r = PortReader(port, self._data, ch_offset=i * 4)
                r.start()
                self._readers.append(r)

        self._poll()

    def _build_ui(self):
        # status bar first so it keeps its slice when the body expands
        self._status = tk.Label(self.root, text='', bg='#0a0a12',
                                 fg=MUTED, font=('Arial', 8), anchor='w', padx=8)
        self._status.pack(fill='x', side='bottom')

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill='both', expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)   # mats
        body.columnconfigure(1, weight=2)   # charts get the larger share

        # left column: the mats, sharing the vertical space evenly
        left = tk.Frame(body, bg=BG, padx=24, pady=20)
        left.grid(row=0, column=0, sticky='nsew')

        self._mats = []
        for i, (label, channels) in enumerate(zip(MAT_LABELS, MAT_CHANNELS)):
            if i > 0:
                sep = tk.Frame(left, bg=BG, height=20)
                sep.pack(fill='x')
                tk.Frame(sep, bg=BORDER, height=1).place(
                    relx=0.05, rely=0.5, relwidth=0.9)

            mat = MatWidget(left, label, channels)
            mat.pack(fill='both', expand=True)
            self._mats.append(mat)

        # right column: raw chart on top, baseline-aligned chart beneath it,
        # both stretching to fill the height (raw a bit taller than aligned)
        right = tk.Frame(body, bg=BG, padx=12, pady=20)
        right.grid(row=0, column=1, sticky='nsew')
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=3)     # raw chart
        right.rowconfigure(3, weight=2)     # aligned chart

        tk.Label(right, text='Raw signal — all 12 channels',
                 bg=BG, fg=MUTED, font=('Arial', 9)).grid(row=0, column=0, sticky='w')
        raw_frame = tk.Frame(right, bg=BG)
        raw_frame.grid(row=1, column=0, sticky='nsew')
        self._chart = RawChart(raw_frame, CHART_CHANNELS)

        # baseline-aligned view directly under the raw chart: every channel
        # zeroed to its resting level so presses stand out on a common scale
        tk.Label(right, text='Raw − initial balance — press pops above 0  (r: re-zero)',
                 bg=BG, fg=MUTED, font=('Arial', 9)).grid(row=2, column=0,
                                                          sticky='w', pady=(12, 4))
        align_frame = tk.Frame(right, bg=BG)
        align_frame.grid(row=3, column=0, sticky='nsew')
        self._align = RawChart(align_frame, CHART_CHANNELS, zeroed=True, height=ALIGN_H)

    def _poll(self):
        if self._demo:
            self._driver.step()

        now = time.monotonic()
        for i, mat in enumerate(self._mats):
            chans = MAT_CHANNELS[i]
            for ch in chans:
                if ch in self._data:
                    mat.update(ch, self._data[ch])
            if all(ch in self._data for ch in chans):
                st = self._states[i]
                st.update({ch: self._data[ch] for ch in chans}, now)
                mat.apply_state(st)

        self._chart.push(self._data)
        self._chart.redraw()

        self._align.push(self._data)
        self._align.redraw()

        if self._demo:
            self._status.config(text='demo mode  —  pyserial not found or no ports')
        else:
            parts = [f'{PORTS[i]}: {r.status} (bad {r.bad})'
                     for i, r in enumerate(self._readers)]
            self._status.config(text='    '.join(parts))

        self.root.after(UPDATE_MS, self._poll)


if __name__ == '__main__':
    root = tk.Tk()
    App(root)
    root.mainloop()
