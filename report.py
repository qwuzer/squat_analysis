"""
report.py — a quick-look report for each recorded session.

    python report.py recordings/elijio_20261006/elijio_20261006_153536.csv
    python report.py recordings/elijio_20261006        # every session in it
    python report.py --all                             # every session missing one

Writes two files next to the session:

    <name>_report.png   the mat signal: raw bands, total load, left/right and
                        front/back balance, holds shaded and labelled
    <name>_poses.jpg    one video frame from the middle of every hold, labelled
                        with the pose — rest, tree L, tree R, ... (needs video)

mat_ui runs this automatically after Stop, in its own low-priority process.

Load is measured against the empty hold just before each pose, not the one
from the start: on some days the mat keeps ~2,000 counts after the first
step-off, which against the start reading pulls tree from ±1.0 to ±0.4
(docs/findings.md 8.10).

Poses span all three mats (Mat 1 front, Mat 2 middle, Mat 3 back), so balance
is over all twelve bands: left/right as a −1…+1 ratio, and front/back as a
position along the row in cm, gaps between mats included.
"""

import csv
import glob
import json
import os
import sys

import numpy as np

import recorder as R

HERE = os.path.dirname(os.path.abspath(__file__))
RECORD_DIR = os.path.join(HERE, 'recordings')

SURF, INK, INK2, MUTED, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#8a8984', '#e6e5e1'
MAT_COLS = ('#2a78d6', '#eb6834', '#1baf7a')       # Mat 1 front, 2 middle, 3 back
REST_COL, HOLD_COL, BAD_COL = '#e3eefb', '#efeee9', '#f7dede'
EYES = '_eyes_closed'
# where each band sits along the row, cm from the front edge: 50 cm mats,
# 12.5 cm gaps, front band a quarter of the way in, back band three quarters
MAT_CM, GAP_CM = 50.0, 12.5
BANDS = ('TL', 'TR', 'BL', 'BR')
ROW_CM = {(m, b): m * (MAT_CM + GAP_CM) + (0.26 if b[0] == 'T' else 0.74) * MAT_CM
          for m in range(3) for b in BANDS}
ROW_LEN = 3 * MAT_CM + 2 * GAP_CM


def label(pose):
    """Display name: empty -> rest, tree_R_correct -> tree R, tree_R_A ->
    tree R A, dog_B_L -> dog B L; older names: tree_L_eyes_closed -> tree L
    (eyes closed). A discarded hold says so."""
    bad = pose.endswith(R.DISCARDED)
    pose = pose[:-len(R.DISCARDED)] if bad else pose
    closed = pose.endswith(EYES)
    base = pose[:-len(EYES)] if closed else pose
    base = base.replace('_correct', '')
    text = 'rest' if base == 'empty' else base.replace('_', ' ')
    return text + (' (eyes closed)' if closed else '') + (' (discarded)' if bad else '')


def is_event(pose):
    """Touch-downs and marks are instants in the holds file, not holds."""
    return pose in R.EVENTS


def load(csv_path):
    base = csv_path[:-4]
    a = np.loadtxt(csv_path, delimiter=',', skiprows=1,
                   usecols=range(1, 14), ndmin=2)
    meta = json.load(open(base + '.json', encoding='utf-8'))
    holds_path = base + '_holds.csv'
    holds = R.load_holds(holds_path) if os.path.exists(holds_path) else []
    meta['_ready'] = ready_windows(holds_path)
    return a[:, 0], a[:, 1:], holds, meta


def ready_windows(holds_path):
    """{empty-hold start_s: (a, b)} from the `ready a-b` the app logs."""
    out = {}
    if not os.path.exists(holds_path):
        return out
    with open(holds_path, newline='', encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            fp = r.get('footprint') or ''
            if r['pose'] == 'empty' and fp.startswith('ready '):
                a, b = fp[6:].split('-')
                out[float(r['start_s'])] = (float(a), float(b))
    return out


def empty_windows(t, holds, ready=None):
    """[(start, end)] of usable empty readings: the clean stretch the app
    logged for each empty hold (`ready a-b`), else the hold minus its first
    3 s (the step-off), or before the first hold if it is not an empty one.

    Sessions recorded before empty holds were automatic have bare gaps between
    poses; for those, 4–9 s after a pose ends stands in. (Not the end of the
    gap: the next pose key is pressed once the subject is already settled.)"""
    wins = []
    prev_end = None
    for pose, s0, s1 in holds:
        if pose == 'empty' and s0 in (ready or {}):
            # the stretch the live check zeroed on: since Nov 2026 the subject
            # steps back on before the empty hold ends, so its tail is not empty
            wins.append(ready[s0])
        elif pose == 'empty' and s1 - s0 > 4:
            wins.append((s0 + 3, s1 - 0.5))
        elif prev_end is not None and s0 - prev_end > 10:
            wins.append((prev_end + 4, min(prev_end + 9, s0 - 3)))
        prev_end = None if pose == 'empty' else s1
    if not holds or holds[0][0] != 'empty':
        first = holds[0][1] if holds else t[-1]
        if first > 4:
            wins.insert(0, (1.0, first - 2))
    return wins


def baselines(t, ch, wins):
    """Per-sample baseline: the mean of the latest empty window that ended
    before that sample (the first window for anything before it)."""
    base = np.empty_like(ch)
    means = [ch[(t >= a) & (t <= b)].mean(0) for a, b in wins]
    idx = np.searchsorted([b for a, b in wins], t) - 1
    idx = np.clip(idx, 0, len(wins) - 1)
    for k, m in enumerate(means):
        base[idx == k] = m
    return base, means[0]


def graph(csv_path, out_path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        'font.family': 'Segoe UI', 'font.size': 10, 'axes.edgecolor': GRID,
        'axes.labelcolor': INK2, 'xtick.color': INK2, 'ytick.color': INK2,
        'axes.spines.top': False, 'axes.spines.right': False,
        'figure.facecolor': SURF, 'axes.facecolor': SURF})

    t, ch, all_holds, meta = load(csv_path)
    holds = [h for h in all_holds if not is_event(h[0])]
    events = [h for h in all_holds if is_event(h[0])]
    mats = list(meta['mat_channels'].items())
    layout = meta.get('mat_layout', {})
    wins = empty_windows(t, holds, meta['_ready'])
    if not wins:
        raise ValueError('no empty reading to measure against')
    base, _ = baselines(t, ch, wins)
    net = np.clip(ch - base, 0, None)                      # vs the latest empty

    per_mat = np.stack([net[:, chans].sum(1) for _, chans in mats], 1)
    tot = per_mat.sum(1)
    on = tot > 0.35 * np.percentile(tot, 95)
    left = sum(net[:, chans[i]] for _, chans in mats for i in (0, 2))
    right = sum(net[:, chans[i]] for _, chans in mats for i in (1, 3))
    x = np.where(on, (right - left) / np.maximum(tot, 1), np.nan)
    along = sum(net[:, chans[i]] * ROW_CM[m, b]
                for m, (_, chans) in enumerate(mats) for i, b in enumerate(BANDS))
    y = np.where(on, along / np.maximum(tot, 1), np.nan)

    width = float(np.clip(t[-1] / 100, 16, 48))       # long sessions get room
    many = len(holds) > 24                             # labels turn sideways
    fig, axs = plt.subplots(4, 1, figsize=(width, 13), sharex=True,
                            gridspec_kw=dict(height_ratios=[1.2, 1, 0.8, 1],
                                             hspace=0.42))
    for k, ax in enumerate(axs):
        for pose, s0, s1 in holds:
            rest = pose == 'empty'
            colour = (BAD_COL if pose.endswith(R.DISCARDED) else
                      REST_COL if rest else HOLD_COL)
            ax.axvspan(s0, s1, color=colour, lw=0, zorder=0)
            if k == 0 and not rest:
                ax.text((s0 + s1) / 2, 1.02, label(pose),
                        transform=ax.get_xaxis_transform(), ha='center',
                        va='bottom', fontsize=8 if many else 9.5, color=INK,
                        rotation=90 if many else 0)
        for _, s0, _ in events:
            ax.axvline(s0, color='#eda100', lw=1.2, zorder=1)
        ax.grid(axis='y', color=GRID, lw=0.8)
        ax.set_xlim(0, t[-1])

    ax = axs[0]
    for (name, chans), col in zip(mats, MAT_COLS):
        for j, c in enumerate(chans):
            ax.plot(t, ch[:, c], color=col, lw=0.9,
                    label=f'{name} {layout.get(name, "")}'.strip() if j == 0 else None)
    ax.set_ylabel('raw counts')
    ax.legend(loc='lower right', ncol=3, frameon=False, fontsize=9)
    ax.set_title('1 · Raw counts, every band', loc='left', fontsize=11,
                 color=INK, pad=90 if many else 22)

    ax = axs[1]
    for k, ((name, _), col) in enumerate(zip(mats, MAT_COLS)):
        ax.plot(t, per_mat[:, k], color=col, lw=1.3, label=name)
    ax.axhline(0, color=MUTED, lw=1)
    ax.set_ylabel('counts above empty')
    ax.legend(loc='upper right', ncol=3, frameon=False, fontsize=9)
    ax.set_title('2 · Load on each mat, against the empty hold before each pose',
                 loc='left', fontsize=11, color=INK)

    ax = axs[2]
    ax.plot(t, x, color='#2a78d6', lw=1.3)
    ax.axhline(0, color=MUTED, lw=1)
    ax.set_ylim(-1.05, 1.05)
    ax.set_ylabel('left ← → right')
    ax.set_title('3 · Left / right balance, all bands', loc='left', fontsize=11,
                 color=INK)

    ax = axs[3]
    for m in range(3):                         # each mat as a band of the axis
        lo = m * (MAT_CM + GAP_CM)
        ax.axhspan(lo, lo + MAT_CM, color=MAT_COLS[m], alpha=0.07, lw=0)
    ax.plot(t, y, color='#2a78d6', lw=1.3)
    ax.set_ylim(ROW_LEN, 0)                    # front of the row at the top
    ax.set_ylabel('cm from the front')
    ax.set_title('4 · Where the load sits along the row (front mat at the top)',
                 loc='left', fontsize=11, color=INK)
    axs[3].set_xlabel('seconds since Record')

    si = meta.get('subject_info', {})
    name = os.path.basename(csv_path)[:-4]

    def val(k):
        v = si.get(k)
        return '?' if v is None else f'{v:g}' if isinstance(v, (int, float)) else v
    fig.suptitle(f"{name} — session {val('session')}, {val('weight_kg')} kg, "
                 f"{val('height_cm')} cm, experience {val('experience')} — "
                 'blue = rest, grey = pose, red = discarded, orange line = touch-down',
                 x=0.01, ha='left', fontsize=13, fontweight='bold', color=INK,
                 y=0.995)
    fig.subplots_adjust(left=0.05, right=0.99, top=0.88 if many else 0.93,
                        bottom=0.05)
    fig.savefig(out_path, dpi=100)
    plt.close(fig)


def poses_sheet(csv_path, out_path, cols=6, tile=(400, 225)):
    """One frame from the middle of every hold, in order. Returns False when
    the session has no video."""
    import cv2
    base = csv_path[:-4]
    if not (os.path.exists(base + '.mp4') and os.path.exists(base + '_frames.csv')):
        return False
    with open(base + '_frames.csv', newline='') as fh:
        el = np.array([float(r['elapsed_s']) for r in csv.DictReader(fh)])
    holds = [h for h in R.load_holds(base + '_holds.csv') if not is_event(h[0])]
    if not len(el) or not holds:
        return False
    cap = cv2.VideoCapture(base + '.mp4')
    seen, tiles = {}, []
    for pose, s0, s1 in holds:
        seen[pose] = seen.get(pose, 0) + 1
        mid = (s0 + s1) / 2
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(np.argmin(abs(el - mid))))
        ok, f = cap.read()
        f = cv2.resize(f, tile) if ok else np.full((tile[1], tile[0], 3), 40,
                                                   np.uint8)
        text = f'{label(pose)} {seen[pose]}  ·  {mid:.0f} s'
        cv2.rectangle(f, (0, 0), (tile[0], 30), (255, 255, 255), -1)
        cv2.putText(f, text, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 0, 0), 1, cv2.LINE_AA)
        tiles.append(f)
    cap.release()
    while len(tiles) % cols:
        tiles.append(np.full_like(tiles[0], 252))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    cv2.imwrite(out_path, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])
    return True


def report(csv_path):
    """Write both files for one session; returns the paths written."""
    base = csv_path[:-4]
    out = []
    graph(csv_path, base + '_report.png')
    out.append(base + '_report.png')
    if poses_sheet(csv_path, base + '_poses.jpg'):
        out.append(base + '_poses.jpg')
    return out


def sessions(path):
    """Signal CSVs under a folder (any depth), identified by their holds file."""
    holds = glob.glob(os.path.join(path, '**', '*_holds.csv'), recursive=True)
    return sorted(h[:-len('_holds.csv')] + '.csv' for h in holds)


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    if args == ['--all']:
        todo = [s for s in sessions(RECORD_DIR)
                if not os.path.exists(s[:-4] + '_report.png')]
    else:
        todo = [s for a in args
                for s in (sessions(a) if os.path.isdir(a) else [a])]
    for s in todo:
        try:
            print(os.path.basename(s), '->',
                  ', '.join(os.path.basename(p) for p in report(s)))
        except Exception as e:          # one bad session must not stop the rest
            print(os.path.basename(s), '-> failed:', e)
