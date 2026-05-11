import math
import tkinter as tk
from tkinter import simpledialog

from config import TARGET_ZONE_RADIUS, UI_UPDATE_MS
from state_machine import State

# ------------------------------------------------------------------
# Color / label constants
# ------------------------------------------------------------------

_BG      = '#0f0f1a'
_BG2     = '#1a1a2e'
_FG      = '#e0e0e0'
_ACCENT  = '#f0a500'
_GREEN   = '#2ecc71'
_ORANGE  = '#f39c12'
_RED     = '#e74c3c'
_BLUE    = '#3498db'
_GREY    = '#888888'

STATE_COLOR = {
    State.EMPTY:           _GREY,
    State.ADJUSTING:       _ORANGE,
    State.STABLE_STANDING: _GREEN,
    State.SQUATTING:       _BLUE,
}
STATE_LABEL = {
    State.EMPTY:           'EMPTY — mat unoccupied',
    State.ADJUSTING:       'ADJUSTING — stabilise your stance',
    State.STABLE_STANDING: 'STABLE STANDING',
    State.SQUATTING:       'SQUATTING',
}


# ------------------------------------------------------------------
# CoP canvas widget
# ------------------------------------------------------------------

class CoPCanvas(tk.Canvas):
    _MARGIN = 30

    def __init__(self, parent, size=320, **kw):
        super().__init__(parent, width=size, height=size, bg=_BG2,
                         highlightthickness=0, **kw)
        self.size = size
        self._draw_static()

    def _draw_static(self):
        s, m = self.size, self._MARGIN
        cx = cy = s // 2
        # Grid lines
        for offset in (-0.5, 0, 0.5):
            px = int(cx + offset * (cx - m))
            py = int(cy + offset * (cy - m))
            self.create_line(px, m, px, s - m, fill='#2a2a40', width=1)
            self.create_line(m, py, s - m, py, fill='#2a2a40', width=1)
        # Axes (brighter)
        self.create_line(m, cy, s - m, cy, fill='#444', width=1)
        self.create_line(cx, m, cx, s - m, fill='#444', width=1)
        # Axis labels
        self.create_text(cx, m - 14, text='Toes ↑',   fill=_GREY, font=('Arial', 8))
        self.create_text(cx, s - m + 14, text='↓ Heel', fill=_GREY, font=('Arial', 8))
        self.create_text(m - 4, cy, text='←\nLeft',   fill=_GREY, font=('Arial', 8), anchor='e')
        self.create_text(s - m + 4, cy, text='Right\n→', fill=_GREY, font=('Arial', 8), anchor='w')
        # Target zone (dashed circle)
        r = int(TARGET_ZONE_RADIUS * (cx - m))
        self.create_oval(cx - r, cy - r, cx + r, cy + r,
                         outline=_GREEN, width=2, dash=(5, 4), tags='target')
        # CoP dot
        self.create_oval(cx - 10, cy - 10, cx + 10, cy + 10,
                         fill=_GREY, outline='', tags='dot')

    def _to_px(self, nx, ny):
        half = (self.size // 2) - self._MARGIN
        cx = cy = self.size // 2
        return cx + nx * half, cy - ny * half   # y inverted

    def update(self, rel_x, rel_y, visible):
        self.itemconfig('target', state='normal' if visible else 'hidden')
        self.itemconfig('dot',    state='normal' if visible else 'hidden')
        if not visible:
            return
        px, py = self._to_px(rel_x, rel_y)
        r = 10
        self.coords('dot', px - r, py - r, px + r, py + r)
        dist = math.sqrt(rel_x ** 2 + rel_y ** 2)
        if dist < TARGET_ZONE_RADIUS:
            color = _GREEN
        elif dist < TARGET_ZONE_RADIUS * 1.6:
            color = _ORANGE
        else:
            color = _RED
        self.itemconfig('dot', fill=color)


# ------------------------------------------------------------------
# User selection dialog
# ------------------------------------------------------------------

class _UserDialog:
    def __init__(self, parent, users):
        self.result = None
        top         = tk.Toplevel(parent)
        top.title('Select User')
        top.configure(bg=_BG)
        top.grab_set()
        self.top    = top

        tk.Label(top, text='Select user', bg=_BG, fg=_FG,
                 font=('Arial', 12, 'bold')).pack(padx=24, pady=(16, 6))

        self._var = tk.StringVar(value=users[0][0] if users else '')
        for uid, name in users:
            tk.Radiobutton(top, text=name, value=uid, variable=self._var,
                           bg=_BG, fg=_FG, selectcolor='#333',
                           activebackground=_BG, activeforeground=_FG,
                           font=('Arial', 11)).pack(anchor='w', padx=24)

        btn = tk.Frame(top, bg=_BG)
        btn.pack(pady=12)
        tk.Button(btn, text='Select',   command=self._select,
                  bg='#2a2a3e', fg=_FG, relief='flat', padx=10).pack(side='left', padx=6)
        tk.Button(btn, text='New user', command=self._new,
                  bg='#2a2a3e', fg=_FG, relief='flat', padx=10).pack(side='left', padx=6)

    def _select(self):
        self.result = self._var.get()
        self.top.destroy()

    def _new(self):
        self.result = '__new__'
        self.top.destroy()


# ------------------------------------------------------------------
# Main application window
# ------------------------------------------------------------------

class App:

    def __init__(self, root, fsm, stats, profiles, serial_queue):
        self.root    = root
        self.fsm     = fsm
        self.stats   = stats
        self.profiles = profiles
        self.queue   = serial_queue

        root.title('Squat Analysis')
        root.configure(bg=_BG)
        root.resizable(False, False)

        self._build_ui()
        self._select_or_create_user()
        self._poll()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = self.root

        # ── Top bar ─────────────────────────────────────────────────
        top = tk.Frame(root, bg=_BG)
        top.pack(fill='x', padx=12, pady=(8, 0))

        tk.Label(top, text='User:', bg=_BG, fg=_GREY,
                 font=('Arial', 10)).pack(side='left')
        self._user_lbl = tk.Label(top, text='—', bg=_BG, fg=_ACCENT,
                                   font=('Arial', 10, 'bold'))
        self._user_lbl.pack(side='left', padx=(4, 12))
        tk.Button(top, text='Switch', command=self._select_or_create_user,
                  bg='#2a2a3e', fg=_FG, relief='flat',
                  font=('Arial', 9), padx=6).pack(side='left')

        self._state_lbl = tk.Label(top, text='EMPTY — mat unoccupied',
                                    bg=_GREY, fg='white',
                                    font=('Arial', 10, 'bold'), padx=8, pady=4)
        self._state_lbl.pack(side='right')

        # ── Middle: CoP canvas + right panel ────────────────────────
        mid = tk.Frame(root, bg=_BG)
        mid.pack(fill='both', expand=True, padx=12, pady=8)

        self._cop = CoPCanvas(mid, size=320)
        self._cop.pack(side='left')

        right = tk.Frame(mid, bg=_BG)
        right.pack(side='left', fill='y', padx=(14, 0))

        # Balance hint
        self._hint_lbl = tk.Label(right, text='', bg=_BG, fg=_GREEN,
                                   font=('Arial', 13, 'bold'), wraplength=180,
                                   justify='left')
        self._hint_lbl.pack(anchor='w', pady=(8, 0))

        # Rep counter
        self._lbl(right, 'Reps this set', pady_top=20)
        self._rep_var = tk.StringVar(value='0')
        tk.Label(right, textvariable=self._rep_var, bg=_BG, fg='white',
                 font=('Arial', 40, 'bold')).pack(anchor='w')

        # Last rep
        self._lbl(right, 'Last rep imbalance', pady_top=12)
        self._last_rep_var = tk.StringVar(value='—')
        tk.Label(right, textvariable=self._last_rep_var, bg=_BG, fg=_FG,
                 font=('Arial', 10), justify='left').pack(anchor='w')

        # Channel raw values
        self._lbl(right, 'Channels (raw)', pady_top=16)
        self._ch_vars = []
        ch_names = ['Ch0  L-toe', 'Ch1  L-heel', 'Ch2  R-heel', 'Ch3  R-toe']
        for name in ch_names:
            row = tk.Frame(right, bg=_BG)
            row.pack(anchor='w')
            tk.Label(row, text=f'{name}:', bg=_BG, fg='#666',
                     font=('Courier', 9), width=13, anchor='w').pack(side='left')
            var = tk.StringVar(value='—')
            tk.Label(row, textvariable=var, bg=_BG, fg=_FG,
                     font=('Courier', 9)).pack(side='left')
            self._ch_vars.append(var)

        # ── Status bar ───────────────────────────────────────────────
        self._status = tk.Label(root, text='', bg='#0a0a12', fg='#555',
                                 font=('Arial', 8), anchor='w', padx=6)
        self._status.pack(fill='x', side='bottom')

    def _lbl(self, parent, text, pady_top=0):
        tk.Label(parent, text=text, bg=_BG, fg=_GREY,
                 font=('Arial', 9)).pack(anchor='w', pady=(pady_top, 0))

    # ------------------------------------------------------------------
    # User selection
    # ------------------------------------------------------------------

    def _select_or_create_user(self):
        users = self.profiles.list_users()
        if not users:
            uid = self._make_new_user()
        else:
            dlg = _UserDialog(self.root, users)
            self.root.wait_window(dlg.top)
            if dlg.result == '__new__':
                uid = self._make_new_user()
            elif dlg.result:
                uid = dlg.result
            else:
                uid = users[0][0]

        self.profiles.select_user(uid)
        self._user_lbl.config(text=self.profiles.current_display_name)

    def _make_new_user(self):
        name = simpledialog.askstring('New User', 'Enter name:', parent=self.root)
        return self.profiles.create_user(name or f'User {len(self.profiles.list_users())+1}')

    # ------------------------------------------------------------------
    # Main poll loop
    # ------------------------------------------------------------------

    def _poll(self):
        changed = False
        while not self.queue.empty():
            pkt = self.queue.get_nowait()
            if 'error' in pkt:
                self._status.config(text=f'Serial error: {pkt["error"]}')
                continue
            readings = pkt.get('readings', [])
            if len(readings) < 4:
                continue
            ts = pkt['ts']
            self.stats.push(ts, readings)
            self.fsm.update(ts, readings)
            changed = True

        if changed:
            self._refresh()

        self.root.after(UI_UPDATE_MS, self._poll)

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------

    def _refresh(self):
        state = self.fsm.state

        # State label
        self._state_lbl.config(text=STATE_LABEL.get(state, state),
                                bg=STATE_COLOR.get(state, _GREY))

        # CoP canvas
        show = state in (State.STABLE_STANDING, State.SQUATTING)
        if show:
            rx, ry = self.fsm.cop_relative()
            self._cop.update(rx, ry, visible=True)
            self._update_hint(rx, ry)
        else:
            self._cop.update(0, 0, visible=False)
            self._hint_lbl.config(text='')

        # Channel raws
        for i, var in enumerate(self._ch_vars):
            var.set(str(self.stats.last_readings[i]))

        # Rep count
        self._rep_var.set(str(len(self.fsm.reps)))

        # Status bar
        agg    = self.stats.rolling_agg_mean
        std    = self.stats.rolling_agg_std
        b0     = self.fsm.B0_agg
        margin = getattr(self.fsm, '_last_margin', 0.0)
        delta  = agg - b0
        sq_margin = self.fsm._squat_margin(self.fsm.B1_std) if self.fsm.B1_agg else 0
        self._status.config(text=(
            f'agg={agg:.0f}  B0={b0:.0f}  Δ={delta:+.0f}  '
            f'stand±{margin:.0f}  std={std:.1f}  '
            f'B1={self.fsm.B1_agg:.0f}  squat±{sq_margin:.0f}'
        ))

    def _update_hint(self, rx, ry):
        dist = math.sqrt(rx ** 2 + ry ** 2)
        if dist < TARGET_ZONE_RADIUS:
            self._hint_lbl.config(text='Balanced', fg=_GREEN)
            return
        parts = []
        if rx < -0.08:
            parts.append('shift right')
        elif rx > 0.08:
            parts.append('shift left')
        if ry < -0.08:
            parts.append('shift forward (toes)')
        elif ry > 0.08:
            parts.append('shift back (heel)')
        self._hint_lbl.config(text='\n'.join(parts) if parts else '', fg=_ORANGE)

    # ------------------------------------------------------------------
    # Callbacks wired from main.py
    # ------------------------------------------------------------------

    def on_rep_complete(self, rep):
        self.profiles.add_rep(rep)
        dev = rep['cop_deviation']
        dx, dy = dev[0], dev[1]
        x_dir = 'L' if dx < 0 else 'R'
        y_dir = 'heel' if dy < 0 else 'toe'
        self._last_rep_var.set(
            f"X: {x_dir} {abs(dx):.2f}   Y: {y_dir} {abs(dy):.2f}\n"
            f"Duration: {rep['duration']:.1f}s   "
            f"Peak: {rep['peak_magnitude']:.0f}"
        )

    def on_state_change(self, new_state):
        # Save baselines to profile whenever B1 is captured
        if new_state == State.STABLE_STANDING and self.fsm.B1_net is not None:
            self.profiles.update_baselines(
                self.fsm.B0, self.fsm.B1_net, self.fsm.CoP_B1)
