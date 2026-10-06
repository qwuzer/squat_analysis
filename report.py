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

Balance is measured against the empty hold just before each pose, not the one
from the start: after the first step-off the mat keeps ~2,000 counts it does
not give back, which against the start reading pulls tree from ±1.0 to ±0.4
(docs/findings.md 8.10). Total load is still shown against the start reading,
so that offset stays visible.
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

# Mat 2's bands (TL, TR, BL, BR) — the mat the protocol uses. Kept here rather
# than imported from mat_ui so this runs without tkinter or serial.
MAT_IN_USE = 'Mat 2'

SURF, INK, INK2, MUTED, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#8a8984', '#e6e5e1'
BAND_COLS = ('#2a78d6', '#eb6834', '#1baf7a', '#eda100')
BAND_NAMES = ('TL', 'TR', 'BL', 'BR')
REST_COL, HOLD_COL = '#e3eefb', '#efeee9'
EYES = '_eyes_closed'


def label(pose):
    """Display name: empty -> rest, tree_L_eyes_closed -> tree L (eyes closed)."""
    closed = pose.endswith(EYES)
    base = pose[:-len(EYES)] if closed else pose
    text = 'rest' if base == 'empty' else base.replace('_', ' ')
    return text + (' (eyes closed)' if closed else '')


def load(csv_path):
    base = csv_path[:-4]
    a = np.loadtxt(csv_path, delimiter=',', skiprows=1,
                   usecols=range(1, 14), ndmin=2)
    meta = json.load(open(base + '.json', encoding='utf-8'))
    holds_path = base + '_holds.csv'
    holds = R.load_holds(holds_path) if os.path.exists(holds_path) else []
    return a[:, 0], a[:, 1:], holds, meta


def empty_windows(t, holds):
    """[(start, end)] of usable empty readings: each empty hold minus its first
    3 s (the step-off), or before the first hold if it is not an empty one.

    Sessions recorded before empty holds were automatic have bare gaps between
    poses; for those, 4–9 s after a pose ends stands in. (Not the end of the
    gap: the next pose key is pressed once the subject is already settled.)"""
    wins = []
    prev_end = None
    for pose, s0, s1 in holds:
        if pose == 'empty' and s1 - s0 > 4:
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

    t, ch, holds, meta = load(csv_path)
    mats = meta['mat_channels']
    B = mats[MAT_IN_USE]
    wins = empty_windows(t, holds)
    if not wins:
        raise ValueError('no empty reading to measure against')
    base, first = baselines(t, ch[:, B], wins)

    total0 = (ch[:, B] - first).sum(1)                    # vs the start reading
    pos = np.clip(ch[:, B] - base, 0, None)                # vs the latest empty
    tot = pos.sum(1)
    on = tot > 0.35 * np.percentile(tot, 95)
    x = np.where(on, (pos[:, 1] + pos[:, 3] - pos[:, 0] - pos[:, 2])
                 / np.maximum(tot, 1), np.nan)
    y = np.where(on, (pos[:, 0] + pos[:, 1] - pos[:, 2] - pos[:, 3])
                 / np.maximum(tot, 1), np.nan)

    fig, axs = plt.subplots(4, 1, figsize=(16, 12), sharex=True, gridspec_kw=dict(
        height_ratios=[1.3, 1, 1, 1], hspace=0.38))
    for k, ax in enumerate(axs):
        for pose, s0, s1 in holds:
            rest = pose == 'empty'
            ax.axvspan(s0, s1, color=REST_COL if rest else HOLD_COL, lw=0,
                       zorder=0)
            if k == 0:
                ax.text((s0 + s1) / 2, 1.02, label(pose),
                        transform=ax.get_xaxis_transform(), ha='center',
                        va='bottom', fontsize=8.5 if rest else 9.5,
                        color=MUTED if rest else INK,
                        fontweight='normal' if rest else 'bold')
        ax.grid(axis='y', color=GRID, lw=0.8)
        ax.set_xlim(0, t[-1])

    ax = axs[0]
    for name, chans in mats.items():
        if name != MAT_IN_USE:
            for c in chans:
                ax.plot(t, ch[:, c], color='#d3d2cc', lw=0.7)
    for n, c, col in zip(BAND_NAMES, B, BAND_COLS):
        ax.plot(t, ch[:, c], color=col, lw=1.2, label=f'{n} (ch{c})')
    ax.set_ylabel('raw counts')
    ax.legend(loc='lower right', ncol=4, frameon=False, fontsize=9)
    ax.set_title(f'1 · Raw counts — {MAT_IN_USE} in colour, other mats grey',
                 loc='left', fontsize=11, color=INK, pad=22)

    ax = axs[1]
    ax.plot(t, total0, color='#2a78d6', lw=1.3)
    ax.axhline(0, color=MUTED, lw=1)
    ax.set_ylabel('counts above empty')
    ax.set_title(f'2 · Total load on {MAT_IN_USE}, against the first empty '
                 'reading', loc='left', fontsize=11, color=INK)

    for ax, v, ylab, title in (
            (axs[2], x, 'left ← → right',
             '3 · Left / right balance, against the empty hold before each pose'),
            (axs[3], y, 'back ← → front', '4 · Front / back balance')):
        ax.plot(t, v, color='#2a78d6', lw=1.3)
        ax.axhline(0, color=MUTED, lw=1)
        ax.set_ylim(-1.05, 1.05)
        ax.set_ylabel(ylab)
        ax.set_title(title, loc='left', fontsize=11, color=INK)
    axs[3].set_xlabel('seconds since Record')

    si = meta.get('subject_info', {})
    name = os.path.basename(csv_path)[:-4]
    def val(k):
        return '?' if si.get(k) is None else f'{si[k]:g}' if k != 'experience' else si[k]
    fig.suptitle(f"{name} — {val('weight_kg')} kg, {val('height_cm')} cm, "
                 f"experience {val('experience')} — blue = rest (empty), "
                 'grey = poses', x=0.01, ha='left', fontsize=13,
                 fontweight='bold', color=INK, y=0.995)
    fig.subplots_adjust(left=0.06, right=0.99, top=0.93, bottom=0.05)
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def poses_sheet(csv_path, out_path, cols=4, tile=(480, 270)):
    """One frame from the middle of every hold, in order. Returns False when
    the session has no video."""
    import cv2
    base = csv_path[:-4]
    if not (os.path.exists(base + '.mp4') and os.path.exists(base + '_frames.csv')):
        return False
    with open(base + '_frames.csv', newline='') as fh:
        el = np.array([float(r['elapsed_s']) for r in csv.DictReader(fh)])
    holds = R.load_holds(base + '_holds.csv')
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
        cv2.putText(f, text, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
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
