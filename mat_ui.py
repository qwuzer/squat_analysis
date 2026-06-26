"""
mat_ui.py  —  Yoga mat pressure monitor
3 mats side-by-side, each with 4 channels in a 2×2 grid.
COM5 → ch 0-3   (mat 2)
COM6 → ch 4-7   (mat 1)
COM7 → ch 8-11  (mat 3, placeholder)
Run: python mat_ui.py
"""

import collections
import threading
import tkinter as tk

try:
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

# ── config ────────────────────────────────────────────────────────────────────
PORTS      = ["COM5", "COM6", "COM7"]
BAUD       = 1_500_000
UPDATE_MS  = 33
MAX_VAL    = 1023

# (top-left, top-right, bottom-left, bottom-right)
MAT_CHANNELS = [
    [4, 7, 5, 6],
    [3, 0, 2, 1],
    [8, 11, 9, 10],
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
DRIFT_ALPHA   = 0.30   # baseline drift-tracking speed while EMPTY


# ── band canvas ───────────────────────────────────────────────────────────────

class BandCanvas(tk.Canvas):
    W, H   = 106, 84
    BAR_Y1 = 70
    BAR_Y2 = 74
    PAD    = 10

    def __init__(self, parent, ch_num):
        super().__init__(parent, width=self.W, height=self.H,
                         bg=BG_MAT, highlightthickness=1,
                         highlightbackground=BORDER)
        self._ch       = ch_num
        self._val      = 0
        self._net      = 0
        self._zero     = None      # baseline; seeded on 1st reading, tracks drift while EMPTY
        self._raw_hist = collections.deque(maxlen=SLOPE_WINDOW + 1)  # for multi-sample slope
        self._state    = 'empty'   # 'empty' | 'pressed'

        self._ch_id = self.create_text(self.PAD, 14, anchor='w',
                                       text=f'ch {ch_num}', fill=MUTED,
                                       font=('Courier', 9))

        self._val_id = self.create_text(self.PAD, 46, anchor='w',
                                         text='—', fill=FG,
                                         font=('Courier', 17, 'bold'))

        self.create_rectangle(self.PAD, self.BAR_Y1,
                              self.W - self.PAD, self.BAR_Y2,
                              fill=BORDER, outline='')

        self._bar = self.create_rectangle(self.PAD, self.BAR_Y1,
                                           self.PAD, self.BAR_Y2,
                                           fill=C_LOW, outline='')

    def set_value(self, val):
        if self._zero is None:           # first reading seeds the baseline
            self._zero = float(val)

        # slope measured over SLOPE_WINDOW samples, so a gradual press still
        # accumulates past the threshold (a single-sample slope misses slow ramps)
        self._raw_hist.append(val)
        slope = val - self._raw_hist[0]

        if self._state == 'empty':
            self._zero += (val - self._zero) * DRIFT_ALPHA   # absorb slow drift
            if slope >= ONSET_SLOPE:                          # sudden push down
                self._state = 'pressed'
        else:  # pressed — baseline frozen
            if slope <= RELEASE_SLOPE:                        # sudden release
                self._state = 'empty'
                self._zero = float(val)                       # trust the new floor

        net = int(round(val - self._zero))
        self._val = val
        self._net = net

        if self._state == 'pressed':
            col, text_col = S_PRESS, '#ffffff'
        else:
            col, text_col = S_EMPTY, '#1a1a2a'

        # fill the whole cell with the state color
        self.config(bg=col, highlightbackground=_hex_shade(col, 0.6))
        self.itemconfig(self._ch_id, fill=text_col)
        self.itemconfig(self._val_id, text=str(net), fill=text_col)

        pct = max(0.0, min(1.0, net / NET_MAX))
        bar_x2 = self.PAD + int((self.W - self.PAD * 2) * pct)
        self.coords(self._bar, self.PAD, self.BAR_Y1, bar_x2, self.BAR_Y2)
        self.itemconfig(self._bar, fill=text_col)

    def rezero(self):
        """Reset this channel's baseline and state to the latest reading."""
        self._zero     = float(self._val)
        self._raw_hist.clear()
        self._state    = 'empty'


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
        shell.pack()

        inner = tk.Frame(shell, bg=BG_EMPTY if empty else BG_MAT,
                         padx=self.GAP, pady=self.GAP)
        inner.pack()

        for row in range(2):
            for col in range(2):
                ch = channels[row * 2 + col]
                if empty:
                    ph = tk.Frame(inner, width=BandCanvas.W, height=BandCanvas.H,
                                  bg=C_EMPTY, highlightthickness=1,
                                  highlightbackground='#22223a')
                    ph.grid(row=row, column=col,
                            padx=(0 if col == 0 else self.GAP, 0),
                            pady=(0 if row == 0 else self.GAP, 0))
                    ph.pack_propagate(False)
                else:
                    band = BandCanvas(inner, ch)
                    band.grid(row=row, column=col,
                              padx=(0 if col == 0 else self.GAP, 0),
                              pady=(0 if row == 0 else self.GAP, 0))
                    self._bands[ch] = band

    def update(self, ch, val):
        if ch in self._bands:
            self._bands[ch].set_value(val)

    def rezero_all(self):
        for band in self._bands.values():
            band.rezero()


# ── serial reader thread ──────────────────────────────────────────────────────

class PortReader(threading.Thread):
    def __init__(self, port, data, ch_offset):
        super().__init__(daemon=True)
        self.port      = port
        self._data     = data
        self._offset   = ch_offset
        self.status    = 'connecting…'

    def run(self):
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
                    body   = text[1:].split('*')[0]
                    fields = body.split(',')
                    if len(fields) < 5:
                        continue
                    num_ch = int(fields[3])
                    for i in range(num_ch):
                        idx = 4 + i
                        if idx < len(fields):
                            try:
                                self._data[self._offset + i] = int(fields[idx])
                            except ValueError:
                                pass
        except Exception as exc:
            self.status = str(exc)


# ── demo mode (no serial) ─────────────────────────────────────────────────────

class DemoDriver:
    def __init__(self, data):
        import random
        self._data = data
        self._vel  = {}
        self._tgt  = {}
        for ch in range(8):  # only mat 1 & 2
            self._vel[ch] = 0.0
            self._tgt[ch] = float(random.randint(100, 800))

    def step(self):
        import random
        for ch in range(8):
            if random.random() < 0.018:
                self._tgt[ch] = float(random.randint(60, 970))
            v = self._vel[ch] * 0.80 + (self._tgt[ch] - self._data.get(ch, 0)) * 0.07
            self._vel[ch] = v
            self._data[ch] = int(max(0, min(MAX_VAL, self._data.get(ch, 0) + v)))


# ── raw signal line chart ──────────────────────────────────────────────────────

CHART_MAT     = 0       # which mat's 4 channels to plot (index into MAT_CHANNELS)
CHART_HISTORY = 300     # samples kept on screen (~10 s at 30 fps)
CHART_W       = 680
CHART_H       = 360
CHART_COLORS  = ['#FF5252', '#40C4FF', '#69F0AE', '#FFD740']


class RawChart:
    """A separate window plotting the raw (un-zeroed) value of 4 channels."""
    PAD_L, PAD_R, PAD_T, PAD_B = 52, 12, 14, 22

    def __init__(self, parent, channels):
        self._channels = channels
        self._hist = {ch: collections.deque(maxlen=CHART_HISTORY) for ch in channels}
        self.cv = tk.Canvas(parent, width=CHART_W, height=CHART_H,
                            bg=BG_MAT, highlightthickness=1,
                            highlightbackground=BORDER)
        self.cv.pack()

    def push(self, data):
        for ch in self._channels:
            if ch in data:
                self._hist[ch].append(data[ch])

    def redraw(self):
        cv = self.cv
        cv.delete('all')
        x0, y0 = self.PAD_L, self.PAD_T
        x1, y1 = CHART_W - self.PAD_R, CHART_H - self.PAD_B

        allvals = [v for ch in self._channels for v in self._hist[ch]]
        if not allvals:
            return
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
        root.resizable(False, False)

        self._build_ui()

        # press 'r' to re-zero every channel to its current reading
        root.bind('<r>', lambda e: [m.rezero_all() for m in self._mats])

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
        body = tk.Frame(self.root, bg=BG)
        body.pack()

        # left column: the mats
        left = tk.Frame(body, bg=BG, padx=24, pady=20)
        left.pack(side='left', anchor='n')

        self._mats = []
        for i, (label, channels) in enumerate(zip(MAT_LABELS, MAT_CHANNELS)):
            if i > 0:
                sep = tk.Frame(left, bg=BG, height=20)
                sep.pack(fill='x')
                tk.Frame(sep, bg=BORDER, height=1).place(
                    relx=0.05, rely=0.5, relwidth=0.9)

            mat = MatWidget(left, label, channels, empty=(i == 2))
            mat.pack()
            self._mats.append(mat)

        # right column: the raw-signal chart
        right = tk.Frame(body, bg=BG, padx=12, pady=20)
        right.pack(side='left', anchor='n')
        tk.Label(right, text=f'Raw signal — {MAT_LABELS[CHART_MAT]}',
                 bg=BG, fg=MUTED, font=('Arial', 9)).pack(anchor='w', pady=(0, 4))
        self._chart = RawChart(right, MAT_CHANNELS[CHART_MAT])

        self._status = tk.Label(self.root, text='', bg='#0a0a12',
                                 fg=MUTED, font=('Arial', 8), anchor='w', padx=8)
        self._status.pack(fill='x', side='bottom')

    def _poll(self):
        if self._demo:
            self._driver.step()

        for i, mat in enumerate(self._mats):
            for ch in MAT_CHANNELS[i]:
                if ch in self._data:
                    mat.update(ch, self._data[ch])

        self._chart.push(self._data)
        self._chart.redraw()

        if self._demo:
            self._status.config(text='demo mode  —  pyserial not found or no ports')
        else:
            parts = [f'{PORTS[i]}: {r.status}' for i, r in enumerate(self._readers)]
            self._status.config(text='    '.join(parts))

        self.root.after(UPDATE_MS, self._poll)


if __name__ == '__main__':
    root = tk.Tk()
    App(root)
    root.mainloop()
