"""
debug_map.py — replay a recording through the live map's logic and draw what
the map showed for every band, and why.

    python debug_map.py recordings/S01_20261110/S01_20261110_140000.csv

Writes <recording>_debug.png next to it: one row per band, its load above the
empty reading the map was using (the line), the LOAD_ON threshold (dashed),
and under it a strip of what the map showed — green on and wanted, orange on
but not wanted, red dashed missing, grey offset / settling during an empty
hold. Grey bands in the background are holds; orange lines are touch-downs
(T), which double as "look here" marks for a debug run.

The replay drives protocol.Tracker exactly as mat_ui does: an empty hold from
Record, a pose from each hold's start, an empty hold again from its end.
"""
import os
import sys

import numpy as np

import protocol as P
import report as RP

MAT_CHANNELS = [[11, 8, 10, 9], [4, 7, 5, 6], [3, 0, 2, 1]]   # = mat_ui
BAND_CH = {f'{m + 1}.{b}': c for m, chans in enumerate(MAT_CHANNELS)
           for b, c in zip(P.BANDS, chans)}
OLD = {'standing': 'mountain', 'tree_L': 'tree_L', 'tree_R': 'tree_R'}
FPS = 30

LOOKS = {'ok': '#1baf7a', 'extra': '#eda100', 'missing': '#d03b3b',
         'offset': '#b9b8b3', 'settling': '#e8d9a8', 'loaded': '#d03b3b'}


def footprint_of(name):
    """The footprint a hold of this name was checked against, or None."""
    for st in P.STEPS:
        if name in (st.name('R'), st.name('L')):
            return st.footprint
    base = name.replace('_eyes_closed', '')
    fp = {p[0]: p[2] for p in P.POSES}
    return fp.get(OLD.get(base, base))


def replay(t, ch, holds):
    """Per UI frame: time, net per band, ref per band, and the look per band."""
    poses = [h for h in holds if h[0] != 'empty' and not RP.is_event(h[0])]
    marks = sorted([(a, 'pose', p) for p, a, b in poses] +
                   [(b, 'end', p) for p, a, b in poses])
    nxt = {i: footprint_of(poses[i + 1][0]) if i + 1 < len(poses) else None
           for i in range(len(poses))}
    tr = P.Tracker()
    tr.start_empty()                                    # Record
    cur, done, out = None, 0, []
    step = max(1, int(round(1 / (FPS * np.median(np.diff(t))))))
    mi = 0
    for i in range(0, len(t), step):
        now = t[i]
        while mi < len(marks) and marks[mi][0] <= now:
            _, kind, p = marks[mi]
            mi += 1
            if kind == 'pose':
                tr.start_pose()
                cur = p
            else:
                tr.start_empty()
                cur, done = None, done + 1
        tr.push(now, {b: float(ch[i, c]) for b, c in BAND_CH.items()})
        if tr.ref is None:
            continue
        net = tr.net()
        clearing = tr.in_empty and not tr.ready
        fp = (footprint_of(cur) if cur else
              P.EMPTY_FOOTPRINT if clearing else
              footprint_of(poses[done][0]) if done < len(poses) else None)
        want = fp.bands() if fp else set()
        look = {}
        for b in BAND_CH:
            on = net[b] >= P.LOAD_ON
            if clearing:
                st = tr.state.get(b, 'clean')
                look[b] = None if st == 'clean' else st
            elif b in want:
                look[b] = 'ok' if on else 'missing'
            else:
                look[b] = 'extra' if on else None
        out.append((now, net, dict(tr.ref), look))
    return out


def draw(csv_path, out_path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'Segoe UI', 'font.size': 9,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'figure.facecolor': RP.SURF, 'axes.facecolor': RP.SURF,
                         'axes.edgecolor': RP.GRID, 'xtick.color': RP.INK2,
                         'ytick.color': RP.INK2})
    t, ch, holds, meta = RP.load(csv_path)
    rows = replay(t, ch, holds)
    tt = np.array([r[0] for r in rows])
    bands = list(BAND_CH)
    net = np.array([[r[1][b] for b in bands] for r in rows])
    ref = np.array([[r[2][b] for b in bands] for r in rows])
    dt = np.diff(tt, append=tt[-1] + 1 / FPS)

    hi = max(1500.0, float(np.percentile(net, 99.5)) * 1.1)
    lo = -400.0
    strip = 0.12 * (hi - lo)
    width = float(np.clip(t[-1] / 25, 16, 60))
    fig, axs = plt.subplots(len(bands), 1, figsize=(width, 1.25 * len(bands) + 1),
                            sharex=True, gridspec_kw=dict(hspace=0.25))
    for k, (ax, b) in enumerate(zip(axs, bands)):
        for pose, s0, s1 in holds:
            if RP.is_event(pose):
                ax.axvline(s0, color='#eda100', lw=1.4, zorder=1)
            elif pose != 'empty':
                bad = pose.endswith('_discarded')
                ax.axvspan(s0, s1, color=RP.BAD_COL if bad else RP.HOLD_COL,
                           lw=0, zorder=0)
                if k == 0:
                    ax.text((s0 + s1) / 2, 1.05, RP.label(pose), ha='center',
                            va='bottom', fontsize=8,
                            transform=ax.get_xaxis_transform())
        ax.axhline(0, color=RP.MUTED, lw=0.8)
        ax.axhline(P.LOAD_ON, color=RP.INK2, lw=0.9, ls=(0, (4, 3)))
        mat = int(b[0]) - 1
        ax.plot(tt, np.clip(net[:, k], lo, hi), color=RP.MAT_COLS[mat], lw=1.1)
        for kind, col in LOOKS.items():          # the strip: what the map showed
            on = np.array([r[3][b] == kind for r in rows])
            if on.any():
                ax.broken_barh(list(zip(tt[on], dt[on])), (lo, strip),
                               color=col, lw=0)
        jumps = np.flatnonzero(np.abs(np.diff(ref[:, k])) > 1e-6) + 1
        for j in jumps:                          # the empty reading was replaced
            ax.plot(tt[j], hi * 0.92, 'v', color=RP.INK2, ms=4)
        ax.set_ylim(lo, hi)
        ax.set_ylabel(f'{b}\n{P.band_name(b).replace(" mat ", chr(10))}',
                      rotation=0, ha='right', va='center', fontsize=8)
        ax.grid(axis='y', color=RP.GRID, lw=0.6)
        ax.set_xlim(0, t[-1])
    axs[-1].set_xlabel('seconds since Record')
    fig.suptitle(f'{os.path.basename(csv_path)[:-4]} — load above the empty reading '
                 f'(line), LOAD_ON {P.LOAD_ON} (dashed), ▼ empty reading replaced; '
                 'strip = map: green ok · orange extra · red missing · '
                 'grey offset · tan settling',
                 x=0.01, ha='left', fontsize=11, fontweight='bold', y=0.998)
    fig.subplots_adjust(left=0.07, right=0.995, top=0.95, bottom=0.03)
    fig.savefig(out_path, dpi=90)
    plt.close(fig)


if __name__ == '__main__':
    for path in sys.argv[1:]:
        out = path[:-4] + '_debug.png'
        draw(path, out)
        print(out)
