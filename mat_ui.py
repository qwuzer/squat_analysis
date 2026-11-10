"""
mat_ui.py  —  Yoga mat pressure monitor
Three mats in a row, front to back, each with 4 bands in a 2×2 grid.
COM5 → ch 8-11  → Mat 1, front
COM6 → ch 4-7   → Mat 2, middle
COM7 → ch 0-3   → Mat 3, back
(checked by standing on each mat, 2026-10-01; PORTS and MAT_CHANNELS below)
Run: python mat_ui.py

The screen is built for running the collection script (protocol.py):

  left   the footprint map — which bands are loaded right now, measured against
         the empty hold before the pose, and whether that matches the pose
  right  the script — the hold in progress, its timer and the words to read
         out, and every hold still to do

Space starts the next hold once the mat has been clean for 7 s, and ends it.
"""

import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import protocol as P
from recorder import DISCARDED, Recorder
from video import GOPRO, CameraProcess

try:
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

try:
    import winsound
except ImportError:                      # not Windows: fall back to Tk's bell
    winsound = None

# ── config ────────────────────────────────────────────────────────────────────
PORTS      = ["COM7", "COM6", "COM5"]
BAUD       = 1_500_000
MIN_PORT_FPS = 90  # below this a port's pill turns red; the mats send 100/s
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

# Each list is that mat's channels in (TL, TR, BL, BR) order; T is the band
# nearer the front of the row. Mats 1 and 3 were swapped until 2026-10-01:
# ch 8-11 had been shown as Mat 3 and ch 0-3 as Mat 1, the reverse of the
# physical mats. Recordings are keyed by channel number, so the raw data is
# unaffected — only which mat a channel is called.
MAT_CHANNELS = [
    [11, 8, 10, 9],    # Mat 1 — COM5 — front
    [4, 7, 5, 6],      # Mat 2 — COM6 — middle
    [3, 0, 2, 1],      # Mat 3 — COM7 — back
]
MAT_LABELS = ["Mat 1", "Mat 2", "Mat 3"]
# protocol.py's band names ('2.TR') -> channel
BAND_CH = {f'{m + 1}.{b}': ch for m, chans in enumerate(MAT_CHANNELS)
           for b, ch in zip(P.BANDS, chans)}

# ── palette (light) ───────────────────────────────────────────────────────────
FONT     = 'Segoe UI'
MONO     = 'Consolas'

BG       = '#F4F5F8'   # window
CARD     = '#FFFFFF'   # panels
TILE     = '#F7F8FA'   # a band with nothing on it
BORDER   = '#E2E5EB'
GRID     = '#EEF0F4'
MUTED    = '#8A90A2'
FG       = '#1C1F2E'
ACCENT   = '#2F6FEB'
OK_COL   = '#2F9E5B'
WARN_COL = '#C27C0E'
ERR_COL  = '#E5484D'

# ── recording ─────────────────────────────────────────────────────────────────
# Sessions are written next to the app in the same shape the bicep pipeline
# reads: uniform grid, wall-clock Time column, one column per channel. See
# recorder.py for why metadata goes in a sidecar instead of repeated columns.
HERE = os.path.dirname(os.path.abspath(__file__))
RECORD_DIR = os.path.join(HERE, 'recordings')
RECORD_HZ  = 100    # matches what the mats actually emit
REC_ON     = '#E5484D'

# Camera recorded alongside the mats, on the same clock — see video.py. GOPRO
# reads the USB GoPro directly (quit GoPro's Webcam app first, it holds the
# stream's port); a number is a webcam index (0 = the laptop's own). None turns
# video off. If the camera cannot be opened the mat recording carries on.
VIDEO_CAMERA = GOPRO
VIDEO_FPS    = 30
VIDEO_SIZE   = (1280, 720)   # written size — enough to see the pose, ~25 MB/min

EMPTY = 'empty'
TOUCHDOWN = 'touchdown'
FOOTPRINT_OK = 0.9     # share of a hold the footprint must match to log `ok`
CONFIRM_S = 3.0        # a second press within this confirms (Space early, X)
FULL_LOAD = 1000       # counts above empty at which a band's fill is darkest

# Field values and per-subject progress survive a restart. Local to this
# machine and gitignored — it holds body weights.
SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'mat_ui_settings.json')
REMEMBERED = ('subject', 'session', 'weight_kg', 'height_cm', 'experience')


def _ms(t):
    """M:SS."""
    t = max(0, int(t))
    return f'{t // 60}:{t % 60:02d}'


def _num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return text.strip() or None


def _blend(a, b, t):
    """Mix hex colour `a` toward `b` by `t` (0..1)."""
    a = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return '#' + ''.join(f'{round(x + (y - x) * t):02x}' for x, y in zip(a, b))


def _beep(times=1):
    def run():
        for _ in range(times):
            winsound.Beep(1000, 220)
            time.sleep(0.08)
    if winsound:
        threading.Thread(target=run, daemon=True).start()


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
                with serial.Serial(self.port, baudrate=BAUD, bytesize=8,
                                   parity='N', stopbits=1, timeout=0.05) as ser:
                    try:                     # room for ~10 s of frames
                        ser.set_buffer_size(rx_size=1 << 16)
                    except Exception:
                        pass
                    self.status = 'ok'
                    # Read whatever has arrived in one call and split it into
                    # lines here. readline() costs a system call per byte —
                    # ~6,000 a second per mat — which on its own held the
                    # mats to ~23 frames/s with checksum errors, and with the
                    # camera running, to under 2.
                    buf = b''
                    while True:
                        buf += ser.read(max(1, ser.in_waiting))
                        *lines, buf = buf.split(b'\n')
                        for raw in lines:
                            self._parse(raw)
            except Exception as exc:
                self.status = str(exc)
                time.sleep(1)                            # back off, then reconnect

    def _parse(self, raw):
        text = raw.decode('ascii', errors='replace').strip()
        if not text.upper().startswith('$SILINO'):
            return
        body = _checked_body(text)
        if body is None:                     # bad/missing checksum
            self.bad += 1
            return
        fields = body.split(',')
        if len(fields) < 5:
            return
        self.frames += 1
        try:
            num_ch = int(fields[3])
        except ValueError:
            return                           # corrupt frame — skip, don't die
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


# ── demo mode (no serial) ─────────────────────────────────────────────────────

class DemoDriver:
    """Stand-in for the hardware: a subject who follows the script. They step
    off when a hold ends, back on a few seconds after the mat reads clean, onto
    exactly the pose's bands — and the mat keeps part of the load after the
    first step-off, as it does on some days, so the offset path gets used."""

    REST = 6000.0
    LOAD = 800.0
    OFFSET = 450.0

    def __init__(self, data):
        self._data = data
        self._t0 = time.monotonic()
        self.on = set()            # bands the demo subject is standing on
        self._offset = set()       # bands left high after a step-off
        self._was = set()

    def step(self):
        import random
        t = time.monotonic() - self._t0
        if self._was - self.on:
            self._offset |= self._was - self.on
        self._was = set(self.on)
        for band, ch in BAND_CH.items():
            v = self.REST + (self.OFFSET if band in self._offset else 0)
            if band in self.on:
                v += self.LOAD * (1 + 0.08 * random.uniform(-1, 1)) + \
                     40 * (1 if int(t * 2) % 2 else -1)
            self._data[ch] = int(max(0, min(MAX_VAL, v + random.gauss(0, 12))))


# ── footprint map ─────────────────────────────────────────────────────────────

class FootprintMap(tk.Canvas):
    """The three mats from above, front at the top, gaps to scale.

    Each band is filled by how far it sits above the empty reading, and
    coloured by whether the current pose wants it: green — expected and
    loaded; red dashed outline — expected but empty; orange — loaded but not
    part of the pose. During an empty hold, red means a band is still loaded."""

    GAP = 0.25          # gap between mats as a share of a mat's side (12.5/50 cm)

    def __init__(self, parent):
        super().__init__(parent, bg=CARD, highlightthickness=0, bd=0,
                         width=480, height=560)
        self._items = {}            # band -> (rect, text)
        self._mats = []             # (rect, label)
        self._shown = {}            # band -> last (fill, outline, dash, width, text)
        for m in range(3):
            self._mats.append((
                self.create_rectangle(0, 0, 0, 0, outline=BORDER, width=1.5,
                                      fill=CARD),
                self.create_text(0, 0, anchor='e', fill=MUTED,
                                 font=(FONT, 10, 'bold'),
                                 text=f'{P.MATS[m].upper()}\n{MAT_LABELS[m]}',
                                 justify='right')))
        for band in BAND_CH:
            self._items[band] = (
                self.create_rectangle(0, 0, 0, 0, fill=TILE, outline=BORDER,
                                      width=1.5),
                self.create_text(0, 0, text='', fill=FG,
                                 font=(FONT, 15, 'bold'), justify='center'))
        self.bind('<Configure>', lambda e: self._layout(e.width, e.height))

    def _layout(self, w, h):
        label_w = 90
        side = min((h - 24) / (3 + 2 * self.GAP), w - label_w - 24)
        x0 = label_w + (w - label_w - side) / 2
        y = (h - side * (3 + 2 * self.GAP)) / 2
        bw, bh = side * 0.40, side * 0.32
        for m in range(3):
            rect, lab = self._mats[m]
            self.coords(rect, x0, y, x0 + side, y + side)
            self.coords(lab, x0 - 14, y + side / 2)
            for b in P.BANDS:
                cx = x0 + side * (0.27 if b[1] == 'L' else 0.73)
                cy = y + side * (0.26 if b[0] == 'T' else 0.74)
                rect_b, text_b = self._items[f'{m + 1}.{b}']
                self.coords(rect_b, cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)
                self.coords(text_b, cx, cy)
            y += side * (1 + self.GAP)
        font = max(10, min(20, int(side * 0.075)))
        for _, t in self._items.values():
            self.itemconfig(t, font=(FONT, font, 'bold'))

    def show(self, looks):
        """looks: band -> (fill, outline, dash, width, text). Reconfigures only
        what changed, so a still mat costs nothing."""
        for band, look in looks.items():
            if self._shown.get(band) == look:
                continue
            self._shown[band] = look
            fill, outline, dash, width, text = look
            rect, t = self._items[band]
            self.itemconfig(rect, fill=fill, outline=outline, dash=dash, width=width)
            self.itemconfig(t, text=text)


# ── main app ──────────────────────────────────────────────────────────────────

class App:
    def __init__(self, root):
        self.root  = root
        self._data = {}
        self._demo = not HAS_SERIAL
        self._recorder = Recorder(self._data, range(4 * len(PORTS)), RECORD_HZ)
        self._video = (None if VIDEO_CAMERA is None else
                       CameraProcess(VIDEO_CAMERA, VIDEO_FPS, VIDEO_SIZE))
        self._settings = self._load_settings()
        self._tracker = P.Tracker()
        self._hold     = None     # {'name', 'step', 'start', 'ok', 'n', 'probs'}
        self._cursor   = 0        # index into P.STEPS of the next hold
        self._side     = 'R'      # side the staff call, for the steps that need one
        self._details  = False    # D: raw and net counts on the map
        self._pending  = None     # (action, until) awaiting a second press
        self._beeped   = False
        self._touches  = 0
        self._flash    = None     # (text, colour, until) — a transient message
        self._idle_status = 'not recording'
        self._shown_text = {}     # label -> (text, colour), skips no-op updates

        root.title('Yoga Mat Monitor')
        root.configure(bg=BG)
        root.resizable(True, True)
        root.minsize(1200, 760)

        self._build_ui()
        self._cursor = self._next_undone(0)

        keys = {'<space>': self._space, '<x>': self._discard,
                '<t>': self._touchdown, '<s>': self._flip_side,
                '<z>': self._zero, '<d>': self._toggle_details,
                '<Left>': lambda: self._move(-1), '<Right>': lambda: self._move(1)}
        for key, fn in keys.items():
            root.bind(key, lambda e, fn=fn: None if self._typing() else fn())
            if len(key) == 3:                     # caps lock on
                root.bind(key.upper(), lambda e, fn=fn: None if self._typing() else fn())
        # closing mid-recording must still close the open hold and write the
        # sidecar, both of which only happen on stop
        root.protocol('WM_DELETE_WINDOW', self._on_close)
        self._refresh_script()

        if self._demo:
            self._driver = DemoDriver(self._data)
        else:
            self._readers = []
            for i, port in enumerate(PORTS):
                r = PortReader(port, self._data, i * 4)
                r.start()
                self._readers.append(r)
        if self._video:
            self._video.open()
        self.root.after(UPDATE_MS, self._poll)

    # ── layout ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_header()
        self._build_record_bar()

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill='both', expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=5, uniform='body')
        body.columnconfigure(1, weight=6, uniform='body')

        left = self._card(body, 'Footprint', 'load above the last empty reading')
        left.grid(row=0, column=0, sticky='nsew', padx=(14, 7), pady=10)
        self._verdict = tk.Label(left.body, text='', bg=CARD, fg=MUTED,
                                 font=(FONT, 15, 'bold'), anchor='w',
                                 justify='left', wraplength=520)
        self._verdict.pack(side='bottom', fill='x', pady=(8, 0))
        self._map = FootprintMap(left.body)
        self._map.pack(fill='both', expand=True)

        right = self._card(body, 'Protocol', 'Space start / end  ·  X discard  ·  '
                           'T touch-down  ·  S side  ·  ← → skip  ·  Z zero  ·  D details')
        right.grid(row=0, column=1, sticky='nsew', padx=(7, 14), pady=10)
        self._build_protocol(right.body)

    def _build_protocol(self, b):
        top = tk.Frame(b, bg=CARD)
        top.pack(fill='x')
        self._phase = tk.Label(top, text='', bg=GRID, fg=MUTED,
                               font=(FONT, 9, 'bold'), padx=8, pady=2)
        self._phase.pack(side='left')
        self._sess_lbl = tk.Label(top, text='', bg=CARD, fg=MUTED,
                                  font=(MONO, 11, 'bold'))
        self._sess_lbl.pack(side='right')

        self._title = tk.Label(b, text='', bg=CARD, fg=FG, anchor='w',
                               font=(FONT, 20, 'bold'))
        self._title.pack(fill='x', pady=(10, 0))
        clock = tk.Frame(b, bg=CARD)
        clock.pack(fill='x', pady=(2, 0))
        self._clock = tk.Label(clock, text='', bg=CARD, fg=MUTED,
                               font=(MONO, 30, 'bold'))
        self._clock.pack(side='left')
        self._touch_lbl = tk.Label(clock, text='', bg=CARD, fg=WARN_COL,
                                   font=(FONT, 11, 'bold'))
        self._touch_lbl.pack(side='left', padx=(16, 0))
        self._bar = tk.Canvas(b, height=10, bg=GRID, highlightthickness=0, bd=0)
        self._bar.pack(fill='x', pady=(2, 12))
        self._bar_fill = self._bar.create_rectangle(0, 0, 0, 10, fill=ACCENT,
                                                    outline='')

        tk.Label(b, text='READ OUT', bg=CARD, fg=MUTED,
                 font=(FONT, 8, 'bold')).pack(anchor='w')
        self._say_lbl = tk.Label(b, text='', bg=CARD, fg=FG, anchor='nw',
                                 justify='left', font=(FONT, 13),
                                 wraplength=600, height=3)
        self._say_lbl.pack(fill='x', pady=(2, 12))
        b.bind('<Configure>', lambda e: self._say_lbl.config(
            wraplength=max(200, e.width - 10)))

        tk.Label(b, text='SCRIPT', bg=CARD, fg=MUTED,
                 font=(FONT, 8, 'bold')).pack(anchor='w')
        grid = tk.Frame(b, bg=CARD)
        grid.pack(fill='x', pady=(4, 0))
        # eight pose-sides in two blocks of four, so the whole script fits
        sides = []
        for i, st in enumerate(P.STEPS):
            if not sides or sides[-1][0] != st.label:
                sides.append((st.label, []))
            sides[-1][1].append(i)
        self._cells = {}             # step index -> label
        for block in range(2):
            c0 = block * 7
            if block:
                tk.Frame(grid, bg=CARD, width=12).grid(row=0, column=c0 - 1)
            for c, h in enumerate(['', '1', '2', '3', 'A', 'B']):
                tk.Label(grid, text=h, bg=CARD, fg=MUTED, font=(FONT, 8, 'bold'),
                         width=2 if c else 12, anchor='center' if c else 'w'
                         ).grid(row=0, column=c0 + c)
            for r, (label, idxs) in enumerate(sides[block * 4:block * 4 + 4]):
                tk.Label(grid, text=label, bg=CARD, fg=FG, font=(FONT, 10),
                         anchor='w', width=12).grid(row=r + 1, column=c0, sticky='w')
                for i in idxs:
                    st = P.STEPS[i]
                    col = st.rep if st.cond == 'correct' else 3 + 'AB'.index(st.cond) + 1
                    cell = tk.Label(grid, text='·', bg=CARD, fg=MUTED, width=2,
                                    font=(FONT, 11, 'bold'), cursor='hand2')
                    cell.grid(row=r + 1, column=c0 + col, padx=1, pady=1)
                    cell.bind('<Button-1>', lambda e, i=i: self._jump(i))
                    self._cells[i] = cell
        tk.Button(b, text='reset progress', relief='flat', bd=0, bg=CARD,
                  fg=MUTED, activebackground=GRID, font=(FONT, 8),
                  cursor='hand2', command=self._reset_progress
                  ).pack(anchor='e', pady=(6, 0))

    def _card(self, parent, title, subtitle=''):
        """A white panel with a title row; put content in `.body`."""
        card = tk.Frame(parent, bg=CARD, highlightthickness=1,
                        highlightbackground=BORDER, padx=14, pady=12)
        head = tk.Frame(card, bg=CARD)
        head.pack(fill='x', pady=(0, 8))
        tk.Label(head, text=title, bg=CARD, fg=FG,
                 font=(FONT, 11, 'bold')).pack(side='left')
        if subtitle:
            tk.Label(head, text=subtitle, bg=CARD, fg=MUTED,
                     font=(FONT, 8)).pack(side='left', padx=(10, 0))
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
        self._rec_status = tk.Label(head, text=self._idle_status, bg=CARD,
                                    fg=MUTED, font=(FONT, 10), anchor='w')
        self._rec_status.pack(side='left', fill='x', expand=True, padx=(12, 0))
        self._pills, self._pill_state = {}, {}
        for name in (['demo'] if self._demo else PORTS) + ['camera']:
            self._pills[name] = tk.Label(pills, text='', bg=CARD, fg=MUTED,
                                         font=(FONT, 9), padx=8)
            self._pills[name].pack(side='left')

    def _set_pill(self, name, text, colour):
        if self._pill_state.get(name) != (text, colour):   # skip no-op reconfigures
            self._pill_state[name] = (text, colour)
            self._pills[name].config(text='● ' + text, fg=colour)

    def _build_record_bar(self):
        """Pinned to the bottom: session fields, Record, and the hold buttons."""
        wrapper = tk.Frame(self.root, bg=CARD, padx=16, pady=10)
        wrapper.pack(fill='x', side='bottom')
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill='x', side='bottom')
        top = tk.Frame(wrapper, bg=CARD)
        top.pack(fill='x')

        def column(title):
            col = tk.Frame(top, bg=CARD)
            col.pack(side='left', padx=(0, 12))
            tk.Label(col, text=title.upper(), bg=CARD, fg=MUTED,
                     font=(FONT, 7, 'bold')).pack(anchor='w')
            return col

        def field(title, width):
            col = column(title)
            # tk.Entry has no inner padding, so a wrapper frame carries the
            # border and the entry sits inside it with room either side
            box = tk.Frame(col, bg=TILE, highlightthickness=1,
                           highlightbackground=BORDER, highlightcolor=BORDER)
            box.pack(anchor='w')
            entry = tk.Entry(box, width=width, bg=TILE, fg=FG,
                             insertbackground=FG, relief='flat', bd=0,
                             highlightthickness=0, font=(FONT, 10))
            entry.pack(padx=8, pady=5)
            entry.bind('<FocusIn>',
                       lambda e: box.config(highlightbackground=ACCENT))
            entry.bind('<FocusOut>',
                       lambda e: (box.config(highlightbackground=BORDER),
                                  self._fields_changed()))
            # Enter commits the field and hands the keyboard back to the hotkeys
            entry.bind('<Return>', lambda e: self.root.focus_set())
            return entry

        def combo(title, values, width, readonly):
            col = column(title)
            c = ttk.Combobox(col, values=values, width=width, font=(FONT, 10),
                             state='readonly' if readonly else 'normal')
            c.pack(anchor='w', ipady=3)
            c.bind('<<ComboboxSelected>>',
                   lambda e: (self._fields_changed(), self.root.focus_set()))
            c.bind('<FocusOut>', lambda e: self._fields_changed())
            c.bind('<Return>', lambda e: self.root.focus_set())
            return c

        # a list of the IDs already used, so the same person on their second
        # day is typed the same way and stays one person
        self._subject = combo('subject ID', self._known_subjects(), 10, False)
        session = combo('session', ['1', '2'], 3, True)
        weight = field('weight kg', 6)
        height = field('height cm', 6)
        experience = combo('experience', ['none', 'some', 'regular'], 9, True)
        self._fields = {'subject': self._subject, 'session': session,
                        'weight_kg': weight, 'height_cm': height,
                        'experience': experience}
        self._restore_fields()

        btns = tk.Frame(top, bg=CARD)
        btns.pack(side='left', padx=(4, 14), pady=(15, 0))
        self._rec_btn = tk.Button(
            btns, text='●  Record', width=10, relief='flat', bd=0,
            font=(FONT, 10, 'bold'), cursor='hand2', padx=8, pady=3,
            command=self._toggle_record)
        self._rec_btn.pack(side='left')
        self._go_btn = tk.Button(
            btns, text='', width=24, relief='flat', bd=0, font=(FONT, 10, 'bold'),
            cursor='hand2', padx=10, pady=3, disabledforeground=MUTED,
            command=self._space)
        self._go_btn.pack(side='left', padx=(8, 0))
        for text, fn in (('X  discard', self._discard),
                         ('T  touch-down', self._touchdown)):
            tk.Button(btns, text=text, relief='flat', bd=0, bg=GRID, fg=FG,
                      activebackground=BORDER, font=(FONT, 9), cursor='hand2',
                      padx=8, pady=3, command=fn).pack(side='left', padx=(6, 0))
        self._style_record_btn(False)

    def _style_record_btn(self, recording):
        if recording:
            self._rec_btn.config(text='■  Stop', bg=REC_ON, fg='#FFFFFF',
                                 activebackground=_blend(REC_ON, '#000000', 0.12),
                                 activeforeground='#FFFFFF')
        else:
            self._rec_btn.config(text='●  Record',
                                 bg=_blend(CARD, REC_ON, 0.12), fg=REC_ON,
                                 activebackground=_blend(CARD, REC_ON, 0.22),
                                 activeforeground=REC_ON)

    # ── remembered fields and progress ───────────────────────────────────────

    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, encoding='utf-8') as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        self._settings['fields'] = {k: w.get() for k, w in self._fields.items()}
        try:
            with open(SETTINGS_PATH, 'w', encoding='utf-8') as fh:
                json.dump(self._settings, fh, indent=2, ensure_ascii=False)
        except OSError:
            pass                        # never let a settings write stop a session

    def _restore_fields(self):
        for key, value in self._settings.get('fields', {}).items():
            w = self._fields.get(key)
            if w is None:
                continue
            if isinstance(w, ttk.Combobox):
                w.set(value)
            else:
                w.delete(0, 'end')
                w.insert(0, value)
        if not self._fields['session'].get():
            self._fields['session'].set('1')

    def _known_subjects(self):
        names = {k.split('|')[0] for k in self._settings.get('progress', {})}
        try:
            for d in os.listdir(RECORD_DIR):
                if os.path.isdir(os.path.join(RECORD_DIR, d)) and '_' in d:
                    names.add(d.rsplit('_', 1)[0])
        except OSError:
            pass
        return sorted(names)

    def _fields_changed(self):
        # also fires on FocusOut while the window is being torn down, when the
        # widgets are already gone — settings were saved just before, so skip
        try:
            self._save_settings()
            if not self._hold:
                self._cursor = self._next_undone(0)
            self._refresh_script()
        except tk.TclError:
            pass

    def _subject_name(self):
        return self._subject.get().strip() or 'session'

    def _progress_key(self):
        return f"{self._subject_name()}|{self._fields['session'].get() or '1'}"

    def _done(self):
        """Step keys this subject has finished in this session, across restarts."""
        return self._settings.get('progress', {}).get(self._progress_key(), [])

    def _mark_done(self, key, done=True):
        keys = self._settings.setdefault('progress', {}).setdefault(
            self._progress_key(), [])
        if done and key not in keys:
            keys.append(key)
        elif not done and key in keys:
            keys.remove(key)
        self._save_settings()

    def _next_undone(self, start):
        done = set(self._done())
        n = len(P.STEPS)
        for k in range(n):
            i = (start + k) % n
            if P.STEPS[i].key not in done:
                return i
        return min(start, n - 1)

    def _reset_progress(self):
        if messagebox.askyesno('Reset progress',
                               f'Clear the script progress for '
                               f'{self._subject_name()}, session '
                               f"{self._fields['session'].get()}?", parent=self.root):
            self._settings.get('progress', {}).pop(self._progress_key(), None)
            self._save_settings()
            self._cursor = 0
            self._refresh_script()
        self.root.focus_set()

    # ── holds ────────────────────────────────────────────────────────────────

    def _step(self):
        return P.STEPS[self._cursor]

    def _start(self, name, step=None):
        self._hold = {'name': name, 'step': step, 'start': self._recorder.elapsed,
                      'ok': 0, 'n': 0, 'probs': {}}
        self._beeped = False
        self._touches = 0
        if name == EMPTY:
            self._tracker.start_empty()
        else:
            self._tracker.start_pose()

    def _end(self, discarded=False):
        """Close the hold in progress and write it."""
        h = self._hold
        if not h:
            return None
        now = self._recorder.elapsed
        if h['name'] == EMPTY:
            # the stretch the live zero came from, on the signal's clock, so
            # analysis measures against exactly what the operator saw
            w = self._tracker.ready_window
            p0 = self._recorder.perf0
            fp = (f'ready {w[0] - p0:.2f}-{w[1] - p0:.2f}' if w and p0
                  else 'not ready')
        elif h['n'] and h['ok'] / h['n'] >= FOOTPRINT_OK:
            fp = 'ok'
        elif h['probs']:
            fp = max(h['probs'], key=h['probs'].get)
        else:
            fp = ''
        name = h['name'] + (DISCARDED if discarded else '')
        self._recorder.add_hold(name, h['start'], now, fp)
        self._hold = None
        return h, now - h['start'], fp

    def _space(self):
        """Empty hold, mat ready → start the next pose. Pose → end it and start
        the empty hold after it."""
        if not self._recorder.active:
            self._say('press Record first (subject off the mat)', ERR_COL)
            return
        h = self._hold
        if h and h['name'] != EMPTY:
            st = h['step']
            _, took, fp = self._end()
            self._mark_done(st.key)
            self._cursor = self._next_undone(self._cursor)
            self._start(EMPTY)
            self._say(f"{st.title(self._side)} — saved, {took:.0f} s"
                      + ('' if fp == 'ok' else f'  ·  footprint: {fp}'),
                      OK_COL if fp == 'ok' else WARN_COL)
        else:
            if not self._tracker.ready and not self._confirmed('start'):
                self._say('mat not clean yet — wait, or press Space again '
                          'to start anyway', WARN_COL)
                return
            self._end()
            st = self._step()
            self._start(st.name(self._side), st)
            self._say(f'{st.title(self._side)} — started')
        self._refresh_script()
        self.root.focus_set()

    def _confirmed(self, action):
        """True on the second press of `action` within CONFIRM_S."""
        now = time.monotonic()
        if self._pending and self._pending[0] == action and now < self._pending[1]:
            self._pending = None
            return True
        self._pending = (action, now + CONFIRM_S)
        return False

    def _discard(self):
        """X during a pose: it failed — end it as discarded and redo it.
        X during the empty hold after it: discard the hold just saved (press
        twice)."""
        if not self._recorder.active:
            return
        h = self._hold
        if h and h['name'] != EMPTY:
            st = h['step']
            self._end(discarded=True)
            self._start(EMPTY)
            self._say(f'{st.title(self._side)} — discarded, redo it', WARN_COL)
        else:
            if not self._confirmed('discard'):
                self._say('press X again to discard the last hold', WARN_COL)
                return
            name = self._recorder.discard_last()
            if not name:
                self._say('nothing to discard', MUTED)
                return
            # the latest finished step with that name is the one to redo
            keys = [i for i, st in enumerate(P.STEPS)
                    if name in (st.name('R'), st.name('L')) and st.key in self._done()]
            if keys:
                self._mark_done(P.STEPS[keys[-1]].key, done=False)
                self._cursor = keys[-1]
            self._say(f'{name} — discarded, redo it', WARN_COL)
        self._refresh_script()
        self.root.focus_set()

    def _touchdown(self):
        h = self._hold
        if not (self._recorder.active and h and h['name'] != EMPTY):
            return
        t = self._recorder.elapsed
        self._recorder.add_hold(TOUCHDOWN, t, t)
        self._touches += 1
        self._say(f'touch-down logged at {t - h["start"]:.1f} s', WARN_COL, 2)

    def _flip_side(self):
        if self._hold and self._hold['name'] != EMPTY:
            return                         # a running hold keeps its label
        self._side = 'L' if self._side == 'R' else 'R'
        self._refresh_script()

    def _move(self, d):
        if self._hold and self._hold['name'] != EMPTY:
            return
        self._cursor = (self._cursor + d) % len(P.STEPS)
        self._refresh_script()

    def _jump(self, i):
        if self._hold and self._hold['name'] != EMPTY:
            return
        self._cursor = i
        self._refresh_script()
        self.root.focus_set()

    def _zero(self):
        """Take the mat as it is now as empty — only between recordings or
        during an empty hold, never with someone in a pose."""
        if self._hold and self._hold['name'] != EMPTY:
            return
        self._tracker.zero()
        self._say('zeroed — the mat as it is now reads as empty', ACCENT)

    def _toggle_details(self):
        self._details = not self._details

    # ── recording ────────────────────────────────────────────────────────────

    def _typing(self):
        """True while a text box has focus, so hotkeys do not steal keystrokes."""
        return isinstance(self.root.focus_get(), (tk.Entry, ttk.Entry))

    def _toggle_record(self):
        if self._recorder.active:
            h = self._hold
            if h and h['name'] != EMPTY:
                self._space()            # a pose in progress counts as done
            self._end()
            video = self._video.stop() if self._video else None
            meta = self._recorder.stop(self._port_stats(), extra={'video': video})
            self._style_record_btn(False)
            self._idle_status = (f"saved {os.path.basename(self._recorder.path)}"
                                 f"  ·  {meta['rows']:,} rows, "
                                 f"{meta['holds']} holds")
            self._make_report(self._recorder.path)
            if video and video['status'] == 'ok':
                self._idle_status += f", {video['frames_written']:,} video frames"
            elif video:
                self._idle_status += '  ·  no video'
            self._flash = None
        else:
            f = {k: w.get() for k, w in self._fields.items()}
            self._frames0 = {r.port: (r.frames, r.bad)
                             for r in getattr(self, '_readers', [])}
            path = self._recorder.start(RECORD_DIR, self._subject_name(), meta={
                'mat_channels': {MAT_LABELS[i]: chans
                                 for i, chans in enumerate(MAT_CHANNELS)},
                'mat_layout': {MAT_LABELS[i]: P.MATS[i] for i in range(3)},
                'ports': PORTS,
                'baud': BAUD,
                'demo': self._demo,
                'subject_info': {'weight_kg': _num(f['weight_kg']),
                                 'height_cm': _num(f['height_cm']),
                                 'experience': f['experience'] or None,
                                 'session': int(f['session'] or 1)},
                'footprint_check': {'load_on': P.LOAD_ON, 'clean': P.CLEAN,
                                    'drop': P.DROP, 'clean_s': P.CLEAN_S},
            })
            if self._video:
                self._video.start(path[:-4], self._recorder.perf0)
            self._style_record_btn(True)
            # the subject is off the mat when Record is pressed, so the
            # session opens with its empty reading
            self._start(EMPTY)
            self._say(f'recording to {os.path.basename(path)}')
            self._subject.config(values=self._known_subjects())
        self._save_settings()
        self._refresh_script()
        self.root.focus_set()

    def _make_report(self, csv_path):
        """Graph and pose screenshots for the session just saved (report.py),
        in a separate process at below-normal priority so it can take nothing
        from the mats if the next recording starts straight away."""
        try:
            subprocess.Popen(
                [sys.executable, os.path.join(HERE, 'report.py'), csv_path],
                cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)
                | getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self._idle_status += '  ·  report being made'
        except Exception as e:
            self._idle_status += f'  ·  no report ({e})'

    def _on_close(self):
        if self._recorder.active:
            self._toggle_record()
        if self._video:
            self._video.close()
        self._save_settings()
        self.root.destroy()

    # ── live readouts ────────────────────────────────────────────────────────

    def _say(self, text, colour=FG, secs=4.0):
        self._flash = (text, colour, time.monotonic() + secs)

    def _show(self, widget, text, colour, **kw):
        if self._shown_text.get(widget) != (text, colour):
            self._shown_text[widget] = (text, colour)
            widget.config(text=text, fg=colour, **kw)

    def _target(self):
        """The footprint the map checks against right now, or None."""
        h = self._hold
        if h and h['name'] != EMPTY:
            return h['step'].footprint
        if h and h['name'] == EMPTY and not self._tracker.ready:
            return P.EMPTY_FOOTPRINT
        return self._step().footprint        # idle, or getting into the next pose

    def _update_map(self):
        tr = self._tracker
        net, level = tr.net(), tr.level()
        h = self._hold
        clearing = h and h['name'] == EMPTY and not tr.ready
        fp = self._target()
        want = fp.bands()
        loaded = {b for b, v in net.items() if v >= P.LOAD_ON}
        total = sum(max(0.0, net.get(b, 0)) for b in loaded) or 1.0
        looks = {}
        for band in BAND_CH:
            v = net.get(band, 0.0)
            on = band in loaded
            depth = 0.25 + 0.75 * min(1.0, max(0.0, v) / FULL_LOAD)
            text = f'{100 * max(0.0, v) / total:.0f}%' if on else ''
            if clearing:
                st = tr.state.get(band, 'clean')
                if st == 'loaded':
                    look = (_blend(CARD, ERR_COL, depth * 0.7), ERR_COL, '', 2, 'on')
                elif st == 'settling':
                    look = (_blend(CARD, WARN_COL, 0.25), WARN_COL, '', 1.5, '…')
                elif st == 'offset':
                    look = (_blend(TILE, MUTED, 0.18), BORDER, '', 1.5, 'offset')
                else:
                    look = (TILE, BORDER, '', 1.5, '')
            elif band in want and on:
                look = (_blend(CARD, OK_COL, depth * 0.75), OK_COL, '', 2, text)
            elif band in want:
                look = (CARD, ERR_COL, (5, 4), 2.5, 'missing')
            elif on:
                look = (_blend(CARD, WARN_COL, depth * 0.75), WARN_COL, '', 2, text)
            else:
                look = (TILE, BORDER, '', 1.5, '')
            if self._details and band in level:
                look = look[:4] + (f'{level[band]:.0f}\n{v:+.0f}',)
            looks[band] = look
        self._map.show(looks)
        return fp, loaded

    def _verdict_text(self, fp, loaded):
        """One line, readable from the mat: is the subject where they should be?"""
        tr, h = self._tracker, self._hold
        if h and h['name'] == EMPTY and not tr.ready:
            still = [b for b, s in tr.state.items() if s == 'loaded']
            if still:
                return ('Still on the mat: ' + ', '.join(P.band_name(b) for b in still[:3]),
                        ERR_COL)
            return (f'Mat clearing — {tr.clean_s(time.perf_counter()):.0f} / {P.CLEAN_S} s clean',
                    WARN_COL)
        ok, missing, extra = fp.check(loaded)
        pose = self._step().label if not (h and h['name'] != EMPTY) else h['step'].label
        if not loaded:
            if h and h['name'] == EMPTY:
                return f'✓ Mat zeroed — subject can get into {pose}', OK_COL
            return 'Nothing on the mat', MUTED
        if ok:
            tail = '' if (h and h['name'] != EMPTY) else ' — Space when settled'
            return f'✓ Footprint matches {pose}{tail}', OK_COL
        parts = []
        if missing:
            parts.append('missing ' + ', '.join(P.band_name(b) for b in missing))
        if extra:
            parts.append('extra ' + ', '.join(P.band_name(b) for b in extra))
        return '✗ ' + ';  '.join(parts), ERR_COL if missing else WARN_COL

    def _update_protocol(self, fp, loaded):
        rec, h, tr = self._recorder, self._hold, self._tracker
        now = rec.elapsed
        self._show(self._sess_lbl, f'session {_ms(now)}' if rec.active else '',
                   REC_ON)
        posing = h and h['name'] != EMPTY
        st = h['step'] if posing else self._step()
        self._show(self._title, st.title(self._side), FG if posing else MUTED)
        self._show(self._say_lbl, st.say(self._side), FG)

        if not rec.active:
            phase, pcol = 'NOT RECORDING', MUTED
            clock, ccol, frac = '', MUTED, 0
        elif posing:
            held = now - h['start']
            phase, pcol = 'HOLD', ACCENT
            clock = f'{held:.0f} / {st.target} s'
            ccol = OK_COL if held >= st.target else FG
            frac = min(1.0, held / st.target)
            if held >= st.target and not self._beeped:
                self._beeped = True
                _beep(1 if st.cond == 'correct' else 2)
        elif tr.ready:
            phase, pcol = 'EMPTY · READY', OK_COL
            clock, ccol, frac = 'ready', OK_COL, 0
        else:
            phase, pcol = 'EMPTY · CLEARING', WARN_COL
            c = tr.clean_s(time.perf_counter())   # the tracker runs on perf_counter
            clock, ccol = f'{c:.0f} / {P.CLEAN_S} s clean', WARN_COL
            frac = c / P.CLEAN_S
        self._show(self._phase, phase, '#FFFFFF' if rec.active else MUTED,
                   bg=pcol if rec.active else GRID)
        self._show(self._clock, clock, ccol)
        self._show(self._touch_lbl, f'{self._touches} touch-down'
                   + ('s' if self._touches != 1 else '') if posing and self._touches
                   else '', WARN_COL)
        w = self._bar.winfo_width()
        self._bar.coords(self._bar_fill, 0, 0, w * frac, 10)
        self._bar.itemconfig(self._bar_fill,
                             fill=OK_COL if frac >= 1 else (WARN_COL if not posing else ACCENT))

        # what Space will do
        if not rec.active:
            go, gcol, state = 'Space · press Record first', GRID, 'disabled'
        elif posing:
            go, gcol, state = '■  End hold  (Space)', FG, 'normal'
        elif tr.ready:
            go, gcol, state = f'▶  Start {st.label}  (Space)', ACCENT, 'normal'
        else:
            go, gcol, state = '…  wait for a clean mat', GRID, 'normal'
        if self._shown_text.get(self._go_btn) != (go, gcol):
            self._shown_text[self._go_btn] = (go, gcol)
            self._go_btn.config(text=go, bg=gcol, state=state,
                                fg='#FFFFFF' if gcol in (FG, ACCENT) else FG,
                                activebackground=gcol)

        # the footprint tally for the hold's sidecar row
        if posing and now - h['start'] > 1.0:
            ok, missing, extra = fp.check(loaded)
            h['n'] += 1
            if ok:
                h['ok'] += 1
            else:
                prob = '; '.join((['missing ' + ' '.join(missing)] if missing else [])
                                 + (['extra ' + ' '.join(extra)] if extra else []))
                h['probs'][prob] = h['probs'].get(prob, 0) + 1

    def _refresh_script(self):
        done = set(self._done())
        h = self._hold
        current = (P.STEPS.index(h['step']) if h and h['name'] != EMPTY
                   else self._cursor)
        for i, cell in self._cells.items():
            st = P.STEPS[i]
            if i == current:
                cell.config(text='▶', fg='#FFFFFF', bg=ACCENT)
            elif st.key in done:
                cell.config(text='✓', fg=OK_COL, bg=_blend(CARD, OK_COL, 0.12))
            else:
                cell.config(text='·', fg=MUTED, bg=CARD)
        self._shown_text.pop(self._title, None)
        self._shown_text.pop(self._say_lbl, None)

    def _update_status(self):
        if self._flash and time.monotonic() < self._flash[2]:
            text, colour = self._flash[:2]
        elif self._recorder.active:
            text = (f'recording to {os.path.basename(self._recorder.path)}'
                    f'  ·  {self._recorder.rows:,} rows')
            colour = MUTED
        else:
            text, colour = self._idle_status, MUTED
        self._show(self._rec_status, text, colour)

    def _port_stats(self):
        # The readers count from app launch; subtract the counts taken at Record
        # so the sidecar describes this recording — frames should then come out
        # close to rate_hz x duration_s.
        base = getattr(self, '_frames0', {})
        return {r.port: {'frames': r.frames - base.get(r.port, (0, 0))[0],
                         'bad_checksum': r.bad - base.get(r.port, (0, 0))[1],
                         'status': r.status}
                for r in getattr(self, '_readers', [])}

    def _port_rates(self):
        """Accepted frames per second per port over the last ~2 s, or {}
        until there is a window to measure over."""
        import collections
        now = time.monotonic()
        hist = getattr(self, '_rate_hist', None)
        if hist is None:
            hist = self._rate_hist = collections.deque()
        hist.append((now, {r.port: r.frames for r in self._readers}))
        while len(hist) > 2 and now - hist[1][0] >= 2.0:
            hist.popleft()
        t0, f0 = hist[0]
        if now - t0 < 1.5:
            return {}
        return {p: (n - f0[p]) / (now - t0) for p, n in hist[-1][1].items()}

    def _camera_pill(self):
        v = self._video
        if v is None:
            return 'camera off', MUTED
        if v.status.startswith('error'):
            return 'camera · ' + v.status[7:47], ERR_COL
        if v.status != 'live':
            # recording while not live means no video is being written
            return f'camera {v.status}', ERR_COL if v.recording else WARN_COL
        if v.recording:
            return f'camera · {v.written:,} frames', OK_COL
        return 'camera live', OK_COL

    def _demo_subject(self):
        """Where the demo subject stands: off during the empty hold until the
        mat has read clean for 2 s, then on the next pose's bands."""
        h, tr, d = self._hold, self._tracker, self._driver
        if h and h['name'] != EMPTY:
            d.on = set(h['step'].footprint.need[0])
        elif h and tr.ready:
            if not getattr(self, '_demo_ready_at', None):
                self._demo_ready_at = time.monotonic()
            if time.monotonic() - self._demo_ready_at > 2:
                d.on = set(self._step().footprint.need[0])
        else:
            self._demo_ready_at = None
            d.on = set()

    def _poll(self):
        if self._demo:
            self._demo_subject()
            self._driver.step()

        if len(self._data) == len(BAND_CH):
            self._tracker.push(time.perf_counter(),
                               {b: float(self._data[ch]) for b, ch in BAND_CH.items()})
        fp, loaded = self._update_map()
        self._show(self._verdict, *self._verdict_text(fp, loaded))
        self._update_protocol(fp, loaded)
        self._update_status()

        if self._demo:
            self._set_pill('demo', 'demo mode — no ports', WARN_COL)
        else:
            rates = self._port_rates()
            for r in self._readers:
                fps = rates.get(r.port)
                if r.status == 'ok' and fps is not None and fps < MIN_PORT_FPS:
                    # the failure that went unnoticed for three sessions: the
                    # port is open but frames have slowed to a trickle
                    text, colour = f'{r.port} · {fps:.0f} fps (low)', ERR_COL
                elif r.status == 'ok':
                    text, colour = r.port, OK_COL
                elif r.status.startswith('connecting'):
                    text, colour = f'{r.port} connecting', WARN_COL
                else:
                    text, colour = f'{r.port} · {r.status[:40]}', ERR_COL
                if r.bad:
                    text += f' · {r.bad} bad'
                self._set_pill(r.port, text, colour)
        self._set_pill('camera', *self._camera_pill())

        self.root.after(UPDATE_MS, self._poll)


if __name__ == '__main__':
    root = tk.Tk()
    App(root)
    root.mainloop()
