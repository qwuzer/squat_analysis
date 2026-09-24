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
import os
import random
import threading
import time
import tkinter as tk

from recorder import Recorder

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
    [4, 7, 5, 6],
    [11, 8, 10, 9],
]
MAT_LABELS = ["Mat 1", "Mat 2", "Mat 3"]

# ── palette (light) ───────────────────────────────────────────────────────────
FONT     = 'Segoe UI'
MONO     = 'Consolas'

BG       = '#F4F5F8'   # window
CARD     = '#FFFFFF'   # panels
TILE     = '#F7F8FA'   # a band at rest
BORDER   = '#E2E5EB'
GRID     = '#EEF0F4'   # chart gridlines and empty bar tracks
MUTED    = '#8A90A2'
FG       = '#1C1F2E'
ACCENT   = '#2F6FEB'
BAR      = '#C5CBD8'   # raw-level bar under each band
OK_COL   = '#2F9E5B'
WARN_COL = '#C27C0E'
ERR_COL  = '#E5484D'
C_EMPTY  = '#EEF0F4'   # placeholder tile for a mat with no data

# ── slope direction display ───────────────────────────────────────────────────
# Rate of change sidesteps every baseline problem in band_state_detection.md: it
# does not matter what a band's resting level is, nor that it never returns to it
# after a release — only which way it is moving right now. Tilting onto the toes
# shows up as the two front bands rising at a similar rate while the two back
# bands fall at a similar rate, which is readable straight off the colours.
# Tuned against a 60s recording taken WHILE STANDING on the mat, not an empty
# one (docs/measurement_tools.md §1.5). Standing is the harder case: load-
# dependent creep decorrelates the bands, and the person's own postural sway
# lands on top. An empty mat would have suggested settings ~2x more sensitive
# that flicker constantly once someone is on it.
SLOPE_N        = 30    # samples in the slope window (~1.0 s at 30 fps)
SLOPE_DEADBAND = 170   # counts/s below this reads as flat — sits above the noise
SLOPE_FULL     = 1200  # counts/s at which the tint reaches full intensity
SLOPE_UP       = '#E5484D'   # red   — value rising
SLOPE_DOWN     = '#2F9E5B'   # green — value falling

# ── weight-shift arrow (one mat) ──────────────────────────────────────────────
# Slope says how things are *changing*, not where they *are*, so the arrow is a
# velocity: which way load is being transferred right now. That costs nothing —
# no baseline, no calibration, nothing that can drift. The price is that it goes
# blank when the mat is still, and a held pose is exactly that. Showing where
# the weight *is* would need an empty-mat baseline; this deliberately does not.
ARROW_MAT   = 1     # index into MAT_CHANNELS / MAT_LABELS — the mat in use
ARROW_MIN   = 200   # counts/s of arrow length below which the mat reads "still"
ARROW_FULL  = 2400  # counts/s that reaches the edge of the circle

# ── recording ─────────────────────────────────────────────────────────────────
# Sessions are written next to the app in the same shape the bicep pipeline
# reads: uniform grid, wall-clock Time column, one column per channel. See
# recorder.py for why metadata goes in a sidecar instead of repeated columns.
RECORD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'recordings')
RECORD_HZ  = 100    # matches what the mats actually emit
REC_ON     = '#E5484D'

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


# ── drawing helpers ───────────────────────────────────────────────────────────

def _blend(a, b, t):
    """Mix hex colour `a` toward `b` by `t` (0..1)."""
    a = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return '#' + ''.join(f'{round(x + (y - x) * t):02x}' for x, y in zip(a, b))


def _rr_points(x1, y1, x2, y2, r):
    """Polygon points for a rounded rectangle; draw with smooth=True."""
    r = max(0.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


# ── band tile ─────────────────────────────────────────────────────────────────

class BandCanvas(tk.Canvas):
    # natural size — the tile grows to fill its grid cell, and every item is
    # re-laid out from the live size in _layout() so it stays proportional
    W, H = 96, 64

    def __init__(self, parent, ch_num):
        super().__init__(parent, width=self.W, height=self.H, bg=CARD,
                         highlightthickness=0, bd=0)
        self._ch   = ch_num
        self._val  = 0
        self._hist = collections.deque(maxlen=SLOPE_N)
        self._tint = TILE
        self._slope_val = 0.0

        # items are created once; positions/fonts are (re)set in _layout()
        self._tile     = self.create_polygon(0, 0, 0, 0, smooth=True,
                                             fill=TILE, outline=BORDER)
        self._ch_id    = self.create_text(0, 0, anchor='w', text=f'ch {ch_num}',
                                          fill=MUTED, font=(FONT, 9))
        self._slope_id = self.create_text(0, 0, anchor='e', text='',
                                          fill=MUTED, font=(MONO, 9))
        self._val_id   = self.create_text(0, 0, anchor='w', text='—',
                                          fill=FG, font=(MONO, 16, 'bold'))
        self._track    = self.create_rectangle(0, 0, 0, 0, fill=GRID, outline='')
        self._bar      = self.create_rectangle(0, 0, 0, 0, fill=BAR, outline='')

        self._geom = (10, 50, 53, self.W)   # (pad, bar_y1, bar_y2, w) until first Configure
        self.bind('<Configure>', lambda e: self._layout(e.width, e.height))

    def _layout(self, w, h):
        """Recompute item positions and font sizes for the current canvas size."""
        pad    = max(8, int(w * 0.09))
        v_font = max(11, min(28, int(h * 0.26)))
        c_font = max(8, min(11, int(h * 0.13)))
        self.coords(self._tile, *_rr_points(1, 1, w - 1, h - 1, min(12, h * 0.16)))
        self.itemconfig(self._val_id, font=(MONO, v_font, 'bold'))
        self.itemconfig(self._ch_id, font=(FONT, c_font))
        self.itemconfig(self._slope_id, font=(MONO, c_font))
        self.coords(self._ch_id, pad, int(h * 0.20))
        self.coords(self._slope_id, w - pad, int(h * 0.20))
        self.coords(self._val_id, pad, int(h * 0.54))
        bar_y1 = int(h * 0.80)
        bar_y2 = bar_y1 + 3
        self.coords(self._track, pad, bar_y1, w - pad, bar_y2)
        self._geom = (pad, bar_y1, bar_y2, w)
        self._draw_bar()

    def _draw_bar(self):
        pad, y1, y2, w = self._geom
        pct = max(0.0, min(1.0, self._val / MAX_VAL))
        x2 = pad + int((w - pad * 2) * pct)
        self.coords(self._bar, pad, y1, x2, y2)

    def set_value(self, val):
        # the number stays pure raw — no baseline, no zeroing. Only the colour
        # is derived, and it comes from the slope, not the level.
        self._val = val
        self._hist.append(val)
        self.itemconfig(self._val_id, text=str(val))
        self._draw_bar()
        self._apply_slope()

    def slope(self):
        """Latest rate of change in counts/s (0 until the window has filled).

        Cached from the last set_value(), so callers that want the number — the
        arrow cross — are not recomputing it a second time each frame.
        """
        return self._slope_val

    def _compute_slope(self):
        """Counts per second, as the difference between the means of the two
        halves of the window. Averaging both halves rejects far more jitter than
        a plain endpoint difference, which rides on two single noisy samples."""
        n = len(self._hist)
        if n < SLOPE_N:
            return 0.0
        h = list(self._hist)
        half = n // 2
        delta = sum(h[n - half:]) / half - sum(h[:half]) / half
        # the two half-windows have their centres (n - half) samples apart
        return delta / ((n - half) * UPDATE_MS / 1000.0)

    def _apply_slope(self):
        s = self._slope_val = self._compute_slope()
        if abs(s) < SLOPE_DEADBAND:
            tint, edge, ink, txt = TILE, BORDER, FG, ''
        else:
            col = SLOPE_UP if s > 0 else SLOPE_DOWN
            # intensity tracks |slope|, so bands changing at the same rate look
            # alike — comparing rates across bands is the whole point of the view
            tint = _blend(TILE, col, 0.10 + 0.28 * min(1.0, abs(s) / SLOPE_FULL))
            edge = _blend(TILE, col, 0.45)
            ink  = _blend(col, '#000000', 0.25)
            txt  = f'{s:+.0f}/s'
        self.itemconfig(self._val_id, fill=ink)
        self.itemconfig(self._slope_id, text=txt, fill=ink)
        if tint != self._tint:                  # reconfigure only on a real change
            self._tint = tint
            self.itemconfig(self._tile, fill=tint, outline=edge)

    def rezero(self):
        """Forget the slope window so the colours restart from flat."""
        self._hist.clear()
        self._apply_slope()


# ── weight-shift arrow ────────────────────────────────────────────────────────

class ArrowCross(tk.Canvas):
    """Which way weight is moving on one mat, from its four band slopes.

        vx = (TR + BR) − (TL + BL)      # + = toward the right
        vy = (TL + TR) − (BL + BR)      # + = toward the front

    Pressing down evenly moves all four bands the same way, so both terms
    cancel and the arrow correctly stays put instead of reading as a shift.
    Direction is exact; the length is a rate, not a distance.
    """

    W, H = 176, 176
    DIRS = ['right', 'front-right', 'front', 'front-left',
            'left', 'back-left', 'back', 'back-right']

    def __init__(self, parent):
        super().__init__(parent, width=self.W, height=self.H, bg=CARD,
                         highlightthickness=0, bd=0)
        self._ring  = self.create_oval(0, 0, 0, 0, outline=BORDER, width=1.5,
                                       fill=TILE)
        self._ax_h  = self.create_line(0, 0, 0, 0, fill=BORDER)
        self._ax_v  = self.create_line(0, 0, 0, 0, fill=BORDER)
        self._arrow = self.create_line(0, 0, 0, 0, fill=ACCENT, width=4,
                                       arrow='last', arrowshape=(12, 14, 6),
                                       capstyle='round')
        self._hub   = self.create_oval(0, 0, 0, 0, fill=MUTED, outline='')
        self._edges = {k: self.create_text(0, 0, text=k, fill=MUTED,
                                           font=(FONT, 8, 'bold'))
                       for k in ('F', 'B', 'L', 'R')}
        self.reading = None          # (direction, counts/s), or None when still
        self.itemconfigure(self._arrow, state='hidden')
        self._geom = (self.W / 2, self.H / 2, 60)
        self.bind('<Configure>', lambda e: self._layout(e.width, e.height))

    def _layout(self, w, h):
        cx, cy = w / 2, h / 2
        r = max(20, min(w, h) / 2 - 16)
        self.coords(self._ring, cx - r, cy - r, cx + r, cy + r)
        self.coords(self._ax_h, cx - r, cy, cx + r, cy)
        self.coords(self._ax_v, cx, cy - r, cx, cy + r)
        self.coords(self._hub, cx - 3, cy - 3, cx + 3, cy + 3)
        self.coords(self._edges['F'], cx, cy - r - 8)
        self.coords(self._edges['B'], cx, cy + r + 8)
        self.coords(self._edges['L'], cx - r - 9, cy)
        self.coords(self._edges['R'], cx + r + 9, cy)
        self._geom = (cx, cy, r)
        self._place()

    def update_vector(self, slopes):
        """slopes are the four band rates in (TL, TR, BL, BR) order, counts/s."""
        tl, tr, bl, br = slopes
        self._v = ((tr + br) - (tl + bl), (tl + tr) - (bl + br))
        self._place()

    def _place(self):
        vx, vy = getattr(self, '_v', (0.0, 0.0))
        cx, cy, r = self._geom
        mag = (vx * vx + vy * vy) ** 0.5
        if mag < ARROW_MIN:            # nothing moving faster than the noise
            self.itemconfigure(self._arrow, state='hidden')
            self.reading = None
            return
        frac = min(1.0, mag / ARROW_FULL)
        # screen y grows downward, so the front of the mat is -y
        self.coords(self._arrow, cx, cy,
                    cx + r * frac * vx / mag, cy - r * frac * vy / mag)
        self.itemconfigure(self._arrow, state='normal')
        self.reading = (self.DIRS[int(round(math.atan2(vy, vx)
                                            / (math.pi / 4))) % 8], mag)


# ── mat card ──────────────────────────────────────────────────────────────────

class MatWidget(tk.Frame):
    """One mat as a card: its name, its channels, and the 2×2 band grid."""
    GAP = 8

    def __init__(self, parent, label, channels, empty=False, badge=None):
        super().__init__(parent, bg=CARD, highlightthickness=1,
                         highlightbackground=BORDER, padx=12, pady=10)
        self._bands = {}
        self._channels = tuple(channels)

        head = tk.Frame(self, bg=CARD)
        head.pack(fill='x', pady=(0, 6))
        tk.Label(head, text=label, bg=CARD, fg=FG,
                 font=(FONT, 10, 'bold')).pack(side='left')
        tk.Label(head, text='ch ' + ' · '.join(str(c) for c in sorted(channels)),
                 bg=CARD, fg=MUTED, font=(FONT, 8)).pack(side='left', padx=(8, 0))
        if badge:
            tk.Label(head, text=badge, bg=_blend(CARD, ACCENT, 0.12), fg=ACCENT,
                     font=(FONT, 8, 'bold'), padx=7, pady=1).pack(side='right')

        grid = tk.Frame(self, bg=CARD)
        grid.pack(fill='both', expand=True)
        for i in range(2):
            grid.rowconfigure(i, weight=1, uniform='band')
            grid.columnconfigure(i, weight=1, uniform='band')

        for row in range(2):
            for col in range(2):
                ch = channels[row * 2 + col]
                if empty:
                    cell = tk.Frame(grid, width=BandCanvas.W,
                                    height=BandCanvas.H, bg=C_EMPTY)
                else:
                    cell = BandCanvas(grid, ch)
                    self._bands[ch] = cell
                # identical padding on every cell, so the four tiles come out the
                # same size — uneven padding shrinks some tiles within equal rows
                cell.grid(row=row, column=col, sticky='nsew',
                          padx=self.GAP // 2, pady=self.GAP // 2)

    def update(self, ch, val):
        if ch in self._bands:
            self._bands[ch].set_value(val)

    def slopes(self):
        """The four band rates in (TL, TR, BL, BR) order, counts/s."""
        return [self._bands[ch].slope() if ch in self._bands else 0.0
                for ch in self._channels]

    def rezero_all(self):
        for band in self._bands.values():
            band.rezero()


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
        self.frames    = 0           # accepted frames, for the session sidecar

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
                        self.frames += 1
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
    """Scripted stand-in for the hardware so the slope colours can be checked
    without a mat. Each mat plays a different case:

      Mat 1 — stood on but held still: every band grey and the deadband has
              to hold, which is the case that matters most for a held pose
      Mat 2 — someone tilting front<->back, so the two front bands rise while
              the two back bands fall and the arrow swings F <-> B
      Mat 3 — empty, so every band should stay grey rather than flickering

    Jitter is +/-50 counts, matching the resting jitter in the hardware notes,
    so the deadband is being tested against a realistic noise floor.
    """

    REST   = 6000.0
    PERIOD = 5.0                # seconds per full front->back->front cycle
    LOADS  = {0: (700, 700, 520, 520),
              1: (700, 700, 520, 520),
              2: (0, 0, 0, 0)}
    TILT   = {0: 0.0, 1: 0.60, 2: 0.0}

    def __init__(self, data):
        self._data = data
        self._rng  = random.Random(7)
        self._t0   = time.monotonic()

    def step(self):
        t = time.monotonic() - self._t0
        for m, channels in enumerate(MAT_CHANNELS):
            loads = self.LOADS[m]
            tilt  = self.TILT[m] * math.sin(2 * math.pi * t / self.PERIOD)
            for i, ch in enumerate(channels):
                # channels are ordered (TL, TR, BL, BR): the first two are front
                w = 1.0 + (tilt if i < 2 else -tilt)
                v = self.REST + loads[i] * w
                self._data[ch] = int(max(0, min(MAX_VAL,
                                                v + self._rng.gauss(0, 50))))


# ── raw signal line chart ──────────────────────────────────────────────────────

CHART_CHANNELS = list(range(12))   # plot every channel (all 3 mats)
CHART_HISTORY  = 300    # samples kept on screen (~10 s at 30 fps)
CHART_W        = 680
CHART_H        = 380
ALIGN_H        = 260    # height of the baseline-aligned chart beneath the raw one
ALIGN_FLOOR    = 200    # min top-of-scale so a flat (all-rest) view isn't all noise
# one hue family per mat, so a line's colour says which mat it came from
CHART_COLORS   = [
    '#1F6FB2', '#4C9BE0', '#0B4A80', '#7DB6EA',   # Mat 1 — ch 0-3
    '#D9480F', '#F08C3A', '#9C3208', '#F4A96B',   # Mat 2 — ch 4-7
    '#2B8A3E', '#52B766', '#18612A', '#8BCF99',   # Mat 3 — ch 8-11
]


class RawChart:
    """A line chart of the raw value of every channel.

    In `zeroed` mode each channel's *initial balance* — the first reading seen —
    is subtracted, so every line starts at 0 and reads `raw − initial`. The
    baseline is captured once and frozen, so a sustained press stays elevated for
    as long as it is held (it doesn't decay), which is what makes a per-band press
    obvious. Press `r` to re-capture the initial balance for every channel.
    """
    PAD_L, PAD_R, PAD_T, PAD_B = 48, 10, 26, 16

    def __init__(self, parent, channels, zeroed=False, height=CHART_H):
        self._channels = channels
        self._zeroed = zeroed
        self._hist = {ch: collections.deque(maxlen=CHART_HISTORY) for ch in channels}
        self._base = {}        # per-channel initial balance, frozen (zeroed mode only)
        # the width/height here are only initial hints; fill+expand lets the
        # canvas track its parent, and redraw() reads the live size each frame
        self.cv = tk.Canvas(parent, width=CHART_W, height=height,
                            bg=CARD, highlightthickness=0, bd=0)
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

    def _legend(self, x):
        """One row across the top: mat name, then a swatch per channel."""
        cv, y = self.cv, 9
        for m, chans in enumerate(MAT_CHANNELS):
            t = cv.create_text(x, y, anchor='w', text=MAT_LABELS[m], fill=MUTED,
                               font=(FONT, 8, 'bold'))
            x = cv.bbox(t)[2] + 8        # measured, so it holds at any DPI
            for ch in sorted(chans):
                cv.create_rectangle(x, y - 4, x + 8, y + 4, outline='',
                                    fill=CHART_COLORS[ch % len(CHART_COLORS)])
                t = cv.create_text(x + 12, y, anchor='w', text=str(ch), fill=FG,
                                   font=(MONO, 8))
                x = cv.bbox(t)[2] + 10
            x += 14

    def redraw(self):
        cv = self.cv
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 2 or h < 2:                # not realised yet — nothing to draw onto
            return
        cv.delete('all')
        x0, y0 = self.PAD_L, self.PAD_T
        x1, y1 = w - self.PAD_R, h - self.PAD_B
        self._legend(x0)

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
            cv.create_line(x0, yy, x1, yy, fill=GRID)
            cv.create_text(x0 - 6, yy, anchor='e', fill=MUTED,
                           font=(MONO, 8), text=f'{vmin + span * frac:.0f}')

        # darker zero line: this is the resting level, so anything above it is
        # pressure on that band — the whole point of the aligned view
        if self._zeroed and vmin < 0 < vmax:
            yz = y1 - (y1 - y0) * ((0 - vmin) / span)
            cv.create_line(x0, yz, x1, yz, fill=_blend(BORDER, '#000000', 0.2))

        for ch in self._channels:
            hist = self._hist[ch]
            if len(hist) < 2:
                continue
            pts = []
            for j, v in enumerate(hist):
                pts += [x0 + (x1 - x0) * (j / (CHART_HISTORY - 1)),
                        y1 - (y1 - y0) * ((v - vmin) / span)]
            cv.create_line(*pts, fill=CHART_COLORS[ch % len(CHART_COLORS)],
                           width=1.4)


# ── main app ──────────────────────────────────────────────────────────────────

class App:
    def __init__(self, root):
        self.root  = root
        self._data = {}
        self._demo = not HAS_SERIAL
        self._recorder = Recorder(self._data, range(4 * len(PORTS)), RECORD_HZ)

        root.title('Yoga Mat Monitor')
        root.configure(bg=BG)
        root.resizable(True, True)
        root.minsize(960, 700)          # below this the bands/charts would overlap

        self._build_ui()

        # press 'r' to re-capture the initial balance (aligned chart re-zeroes),
        # 'm' to drop a label. Both are ignored while a text box has focus.
        root.bind('<r>', lambda e: None if self._typing() else
                  (self._align.rezero(), [m.rezero_all() for m in self._mats]))
        root.bind('<m>', lambda e: None if self._typing() else self._mark())

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
        # header and record bar are packed before the body so they keep their
        # slices when the body expands
        self._build_header()
        self._build_record_bar()

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill='both', expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)   # mats
        body.columnconfigure(1, weight=2)   # charts get the larger share

        # left: the three mats, locked to identical heights. `uniform` makes the
        # rows equal whatever their content wants, and every card gets the same
        # padding so the cards themselves — not just the rows — match.
        left = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky='nsew', padx=(14, 7), pady=9)
        left.columnconfigure(0, weight=1)
        self._mats = []
        for i, (label, channels) in enumerate(zip(MAT_LABELS, MAT_CHANNELS)):
            left.rowconfigure(i, weight=1, uniform='mat')
            mat = MatWidget(left, label, channels,
                            badge='in use' if i == ARROW_MAT else None)
            mat.grid(row=i, column=0, sticky='nsew', pady=5)
            self._mats.append(mat)

        # right: the arrow, then the raw chart, then the aligned chart
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky='nsew', padx=(7, 14), pady=9)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=3)     # raw chart
        right.rowconfigure(2, weight=2)     # aligned chart

        arrow = self._card(right, f'Weight shift · {MAT_LABELS[ARROW_MAT]}',
                           'direction of movement — blank while still')
        arrow.grid(row=0, column=0, sticky='ew', pady=5)
        self._cross = ArrowCross(arrow.body)
        self._cross.pack(side='left')
        # the readout is big on purpose: it has to be legible from on the mat
        readout = tk.Frame(arrow.body, bg=CARD)
        readout.pack(side='left', fill='y', padx=(28, 0))
        tk.Frame(readout, bg=CARD).pack(expand=True, fill='both')
        self._dir_lbl = tk.Label(readout, text='still', bg=CARD, fg=MUTED,
                                 font=(FONT, 28, 'bold'), anchor='w')
        self._dir_lbl.pack(anchor='w')
        self._rate_lbl = tk.Label(readout, text='nothing moving above the noise',
                                  bg=CARD, fg=MUTED, font=(FONT, 10), anchor='w')
        self._rate_lbl.pack(anchor='w')
        tk.Frame(readout, bg=CARD).pack(expand=True, fill='both')
        self._shown = None

        raw = self._card(right, 'Raw signal', 'all 12 channels')
        raw.grid(row=1, column=0, sticky='nsew', pady=5)
        self._chart = RawChart(raw.body, CHART_CHANNELS)

        aligned = self._card(right, 'Raw − initial balance',
                             'a press pops above 0  ·  r to re-zero')
        aligned.grid(row=2, column=0, sticky='nsew', pady=5)
        self._align = RawChart(aligned.body, CHART_CHANNELS, zeroed=True,
                               height=ALIGN_H)

    def _card(self, parent, title, subtitle=''):
        """A white panel with a title row; put content in `.body`."""
        card = tk.Frame(parent, bg=CARD, highlightthickness=1,
                        highlightbackground=BORDER, padx=12, pady=10)
        head = tk.Frame(card, bg=CARD)
        head.pack(fill='x', pady=(0, 6))
        tk.Label(head, text=title, bg=CARD, fg=FG,
                 font=(FONT, 10, 'bold')).pack(side='left')
        if subtitle:
            tk.Label(head, text=subtitle, bg=CARD, fg=MUTED,
                     font=(FONT, 8)).pack(side='left', padx=(8, 0))
        card.body = tk.Frame(card, bg=CARD)
        card.body.pack(fill='both', expand=True)
        return card

    def _build_header(self):
        """App name on the left, one status pill per port on the right."""
        head = tk.Frame(self.root, bg=CARD)
        head.pack(fill='x', side='top')
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill='x', side='top')
        tk.Label(head, text='Yoga Mat Monitor', bg=CARD, fg=FG,
                 font=(FONT, 12, 'bold')).pack(side='left', padx=16, pady=10)
        pills = tk.Frame(head, bg=CARD)
        pills.pack(side='right', padx=10)
        self._pills, self._pill_state = {}, {}
        for name in (['demo'] if self._demo else PORTS):
            self._pills[name] = tk.Label(pills, text='', bg=CARD, fg=MUTED,
                                         font=(FONT, 9), padx=8)
            self._pills[name].pack(side='left')

    def _set_pill(self, name, text, colour):
        if self._pill_state.get(name) != (text, colour):   # skip no-op reconfigures
            self._pill_state[name] = (text, colour)
            self._pills[name].config(text='\u25cf ' + text, fg=colour)

    def _build_record_bar(self):
        """Session fields and controls, pinned to the bottom of the window."""
        bar = tk.Frame(self.root, bg=CARD, padx=16, pady=10)
        bar.pack(fill='x', side='bottom')
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill='x', side='bottom')

        def field(title, width):
            col = tk.Frame(bar, bg=CARD)
            col.pack(side='left', padx=(0, 12))
            tk.Label(col, text=title.upper(), bg=CARD, fg=MUTED,
                     font=(FONT, 7, 'bold')).pack(anchor='w')
            # tk.Entry has no inner padding, so a wrapper frame carries the
            # border and the entry sits inside it with room either side
            wrap = tk.Frame(col, bg=TILE, highlightthickness=1,
                            highlightbackground=BORDER, highlightcolor=BORDER)
            wrap.pack(anchor='w')
            entry = tk.Entry(wrap, width=width, bg=TILE, fg=FG,
                             insertbackground=FG, relief='flat', bd=0,
                             highlightthickness=0, font=(FONT, 10))
            entry.pack(padx=8, pady=5)
            entry.bind('<FocusIn>',
                       lambda e: wrap.config(highlightbackground=ACCENT))
            entry.bind('<FocusOut>',
                       lambda e: wrap.config(highlightbackground=BORDER))
            # Enter commits the field and hands the keyboard back to the hotkeys
            entry.bind('<Return>', lambda e: self.root.focus_set())
            return entry

        self._subject = field('subject', 12)
        self._pose    = field('pose', 16)
        self._label   = field('label', 18)
        # Enter in the label box drops the mark straight away
        self._label.bind('<Return>', lambda e: (self._mark(),
                                                self.root.focus_set()))

        btns = tk.Frame(bar, bg=CARD)
        btns.pack(side='left', padx=(4, 14), pady=(15, 0))
        self._rec_btn = tk.Button(
            btns, text='\u25cf  Record', width=10, relief='flat', bd=0,
            font=(FONT, 10, 'bold'), cursor='hand2', padx=8, pady=3,
            command=self._toggle_record)
        self._rec_btn.pack(side='left', padx=(0, 8))
        self._style_record_btn(False)

        self._mark_btn = tk.Button(
            btns, text='Mark  (m)', width=10, relief='flat', bd=0,
            bg=GRID, fg=FG, activebackground=BORDER, activeforeground=FG,
            disabledforeground=MUTED, font=(FONT, 10), cursor='hand2',
            padx=8, pady=3, command=self._mark, state='disabled')
        self._mark_btn.pack(side='left')

        self._rec_status = tk.Label(bar, text='not recording', bg=CARD,
                                    fg=MUTED, font=(FONT, 9), anchor='w')
        self._rec_status.pack(side='left', fill='x', expand=True, pady=(15, 0))

    def _style_record_btn(self, recording):
        if recording:
            self._rec_btn.config(text='\u25a0  Stop', bg=REC_ON, fg='#FFFFFF',
                                 activebackground=_blend(REC_ON, '#000000', 0.12),
                                 activeforeground='#FFFFFF')
        else:
            self._rec_btn.config(text='\u25cf  Record',
                                 bg=_blend(CARD, REC_ON, 0.12), fg=REC_ON,
                                 activebackground=_blend(CARD, REC_ON, 0.22),
                                 activeforeground=REC_ON)

    def _typing(self):
        """True while a text box has focus, so hotkeys do not steal keystrokes."""
        return isinstance(self.root.focus_get(), tk.Entry)

    def _toggle_record(self):
        if self._recorder.active:
            meta = self._recorder.stop(self._port_stats())
            self._style_record_btn(False)
            self._mark_btn.config(state='disabled')
            self._rec_status.config(
                text=f"saved {os.path.basename(self._recorder.path)}  \u00b7  "
                     f"{meta['rows']:,} rows, {meta['events']} marks", fg=FG)
        else:
            subject = self._subject.get().strip() or 'session'
            path = self._recorder.start(RECORD_DIR, subject, meta={
                'mat_channels': {MAT_LABELS[i]: chans
                                 for i, chans in enumerate(MAT_CHANNELS)},
                'ports': PORTS,
                'baud': BAUD,
                'demo': self._demo,
            })
            self._style_record_btn(True)
            self._mark_btn.config(state='normal')
            self._rec_status.config(text=f'recording to {os.path.basename(path)}')
        self.root.focus_set()

    def _mark(self):
        pose = self._pose.get().strip()
        label = self._label.get().strip()
        if not pose and not label:
            label = 'mark'
        if self._recorder.mark(label, pose):
            what = ' / '.join(x for x in (pose, label) if x)
            self._rec_status.config(
                text=f'marked {what} at {self._recorder.elapsed:.1f}s', fg=FG)

    def _port_stats(self):
        return {r.port: {'frames': r.frames, 'bad_checksum': r.bad,
                         'status': r.status}
                for r in getattr(self, '_readers', [])}

    def _poll(self):
        if self._demo:
            self._driver.step()

        for i, mat in enumerate(self._mats):
            for ch in MAT_CHANNELS[i]:
                if ch in self._data:
                    mat.update(ch, self._data[ch])

        self._cross.update_vector(self._mats[ARROW_MAT].slopes())
        reading = self._cross.reading
        shown = None if reading is None else (reading[0], round(reading[1], -1))
        if shown != self._shown:                  # skip no-op reconfigures
            self._shown = shown
            if shown is None:
                self._dir_lbl.config(text='still', fg=MUTED)
                self._rate_lbl.config(text='nothing moving above the noise')
            else:
                self._dir_lbl.config(text=shown[0], fg=ACCENT)
                self._rate_lbl.config(text=f'{shown[1]:,.0f} counts/s', fg=FG)

        self._chart.push(self._data)
        self._chart.redraw()

        self._align.push(self._data)
        self._align.redraw()

        if self._recorder.active:
            t = self._recorder.elapsed
            self._rec_status.config(
                text=f'\u25cf REC  {int(t)//60:02d}:{int(t)%60:02d}   '
                     f'{self._recorder.rows:,} rows', fg=REC_ON)

        if self._demo:
            self._set_pill('demo', 'demo mode — no ports', WARN_COL)
        else:
            for r in self._readers:
                if r.status == 'ok':
                    text, colour = r.port, OK_COL
                elif r.status.startswith('connecting'):
                    text, colour = f'{r.port} connecting', WARN_COL
                else:
                    text, colour = f'{r.port} · {r.status[:40]}', ERR_COL
                if r.bad:
                    text += f' · {r.bad} bad'
                self._set_pill(r.port, text, colour)

        self.root.after(UPDATE_MS, self._poll)


if __name__ == '__main__':
    root = tk.Tk()
    App(root)
    root.mainloop()
