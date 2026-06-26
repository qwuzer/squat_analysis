# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
python main.py
```

Requires `pyserial` and `tkinter` (stdlib). No build step. The app opens a Tkinter window and immediately tries to open the serial port defined in `config.py`.

## Architecture

The system is a real-time pressure-mat squat analyser. Data flows in one direction:

```
SerialReader (thread) → Queue → App._poll() → ChannelStats.push() → SquatStateMachine.update() → UI refresh
```

**`serial_reader.py`** — Background thread. Parses `$SILINO,...` NMEA-style sentences from the mat over serial. Puts `{ts, readings[4]}` dicts onto a `Queue`.

**`rolling_stats.py` (`ChannelStats`)** — All signal processing lives here. Maintains time-windowed buffers for per-channel means, aggregate mean/std, and CoP (Center of Pressure). The CoP formula is ratio-based so it is immune to sensor drift and body-weight differences between users.

**`state_machine.py` (`SquatStateMachine`)** — Four-state FSM: `EMPTY → ADJUSTING → STABLE_STANDING → SQUATTING`. Transitions are driven entirely by rolling statistics — no timers or manual triggers. Captures two baselines:
- `B0` — environment baseline (rolling mean while mat is empty, never persisted across sessions)
- `B1` / `B1_net` — body-weight baseline (snapshot on entering STABLE_STANDING; `B1_net = B1 − B0` is what gets saved to a profile)

**`ui.py` (`App`)** — Tkinter UI polled at ~30 fps via `root.after()`. `App._poll()` drains the queue, feeds `ChannelStats` and the FSM, then calls `_refresh()`. The CoP canvas (`CoPCanvas`) shows a live dot relative to `CoP_B1` (the user's personal standing centre).

**`profiles.py` (`ProfileManager`)** — JSON persistence (`profiles.json`). One file, keyed by uid (millisecond timestamp). Each session stores `B0`, `B1_net`, `CoP_B1`, and per-rep metrics. `B0` is intentionally re-captured every session (drift makes old values stale).

**`config.py`** — All tuneable constants: serial port/baud, rolling window durations, FSM thresholds, UI parameters.

## Git workflow

When pushing changes for a PR, always create a descriptive branch name instead of pushing the auto-generated worktree branch (e.g. `claude/elated-mccarthy-*`). Use the format:

```
<type>/<short-description>
```

Examples: `fix/squat-threshold`, `feat/step-counter-ui`, `refactor/serial-parser`

Push like this:
```bash
git push origin HEAD:fix/your-description
gh pr create --head fix/your-description ...
```

## Key design constraints

- **Band sensors, not point sensors** — each channel covers a full side of the mat. CoP is directional (which way weight is shifted), not an exact anatomical position. Never display sub-millimetre precision; use directional language and a normalised −1…+1 grid.
- **EMPTY → ADJUSTING detection** — must compare the *instantaneous* aggregate (`sum(stats.last_readings)`) against the rolling `B0_agg`. Comparing rolling mean to rolling mean always gives Δ≈0 and the transition never fires.
- **B1 is valid only for a fixed foot position** — if the user repositions between STABLE_STANDING and SQUATTING, B1 must be recaptured.
- **CoP formula** (channels: 0=front-left, 1=back-left, 2=back-right, 3=front-right):
  ```
  CoP_x = 0.5 × (Ch3 + Ch2 − Ch0 − Ch1) / total   # negative = left
  CoP_y = 0.5 × (Ch0 + Ch3 − Ch1 − Ch2) / total   # negative = heel
  ```
