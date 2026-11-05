"""
recorder.py — session recording for the mats.

Writes the same shape of file the bicep pipeline consumes: one row per sample on
a uniform grid, a wall-clock `Time` column in `H-MM-SS.fff`, one column per
channel. Three files per session:

    <name>.csv          the signal
    <name>_holds.csv    one row per hold: pose, start_s, end_s
    <name>.json         session metadata and per-port frame counts

Keeping metadata in a sidecar rather than repeating it on every row is the one
thing done differently from the old pipeline. It costs nothing now and it is
what a database would ingest later.

Sampling: the mats each emit at ~100 Hz on their own clock, and the reader
threads keep only the latest value per channel. This samples that shared state
on a fixed grid, so the occasional sample is read twice or skipped. For postural
data, whose content sits below a few Hz, that is immaterial — and the per-port
frame counts in the JSON make it checkable rather than assumed.
"""

import csv
import json
import os
import subprocess
import threading
import time


HOLD_COLUMNS = ['pose', 'start_s', 'end_s']


def clock_str(t):
    """`H-MM-SS.fff`, the format the bicep pipeline's parser expects."""
    lt = time.localtime(t)
    return f'{lt.tm_hour}-{lt.tm_min:02d}-{lt.tm_sec + (t - int(t)):06.3f}'


def _git_sha():
    try:
        out = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'],
                             capture_output=True, text=True, timeout=5,
                             cwd=os.path.dirname(os.path.abspath(__file__)))
        return out.stdout.strip() or None
    except Exception:
        return None


class Recorder:
    """Samples a shared {channel: value} dict to CSV on a fixed grid."""

    FLUSH_EVERY = 2.0          # seconds; a crash then costs at most this much

    def __init__(self, data, channels, rate_hz=100):
        self._data     = data
        self._channels = list(channels)
        self._rate     = rate_hz
        self._thread   = None
        self._stop     = threading.Event()
        self.reset()

    def reset(self):
        self.path     = None
        self.rows     = 0
        self.dropped  = 0          # grid ticks where a channel had no value yet
        self.started  = None       # wall clock
        self._perf0   = None
        self.holds    = 0
        self._holds_path = None
        self._meta    = {}

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def perf0(self):
        """time.perf_counter() at elapsed_s = 0 — the shared clock origin."""
        return self._perf0

    @property
    def elapsed(self):
        return 0.0 if self._perf0 is None else time.perf_counter() - self._perf0

    # ── control ──────────────────────────────────────────────────────────────

    def start(self, out_dir, subject, meta=None):
        """Begin recording. Returns the path of the signal file."""
        if self.active:
            raise RuntimeError("already recording")
        self.reset()
        self.started = time.time()
        stamp = time.strftime('%Y%m%d_%H%M%S', time.localtime(self.started))
        name = f"{subject or 'session'}_{stamp}"
        # one folder per subject per day — a different day is a different
        # session — holding every file of every recording made in it
        folder = os.path.join(out_dir, name.rsplit('_', 1)[0])
        os.makedirs(folder, exist_ok=True)
        self.path = os.path.join(folder, name + '.csv')
        self._meta = dict(meta or {})
        self._meta.update({
            'subject_id': subject,
            'started_at': time.strftime('%Y-%m-%dT%H:%M:%S',
                                        time.localtime(self.started)),
            'rate_hz': self._rate,
            'columns': ['Time', 'elapsed_s'] + [f'ch{c}' for c in self._channels],
            'code_version': _git_sha(),
        })
        # the holds file is created now and appended to as each hold ends, so a
        # crash mid-session loses at most the hold in progress
        self._holds_path = self.path[:-4] + '_holds.csv'
        with open(self._holds_path, 'w', newline='', encoding='utf-8') as fh:
            csv.writer(fh).writerow(HOLD_COLUMNS)
        # set here, not in the writer thread, so the clock origin can be handed
        # to the camera process the moment start() returns
        self._perf0 = time.perf_counter()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self.path

    def add_hold(self, pose, start_s, end_s):
        """Append one completed hold. Times are elapsed_s, the same clock as the
        signal file, so a hold is every signal row with start_s <= t < end_s.

        These are the raw button times. A hold includes getting into the pose
        and coming out of it; trimming that is a feature-extraction decision,
        so it is not baked in here.
        """
        if self._holds_path is None:
            return False
        with open(self._holds_path, 'a', newline='', encoding='utf-8') as fh:
            csv.writer(fh).writerow([pose, f'{start_s:.4f}', f'{end_s:.4f}'])
        self.holds += 1
        return True

    def stop(self, port_stats=None, extra=None):
        """Stop, write the sidecars, and return the metadata that was written.
        `extra` is merged into the JSON, e.g. what the video recorder reports."""
        if not self.active:
            return None
        self._stop.set()
        self._thread.join(timeout=5)
        self._meta.update({
            'stopped_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'duration_s': round(self.elapsed, 3),
            'rows': self.rows,
            'rows_with_gaps': self.dropped,
            'holds': self.holds,
            'ports': port_stats or {},
        })
        self._meta.update(extra or {})
        base = self.path[:-4]
        with open(base + '.json', 'w', encoding='utf-8') as fh:
            json.dump(self._meta, fh, indent=2, ensure_ascii=False)
        return self._meta

    # ── writer thread ────────────────────────────────────────────────────────

    def _run(self):
        period = 1.0 / self._rate
        with open(self.path, 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(self._meta['columns'])
            next_t = self._perf0
            last_flush = self._perf0
            while not self._stop.is_set():
                next_t += period
                delay = next_t - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -0.5:
                    next_t = time.perf_counter()     # fell behind; resync
                now = time.perf_counter()
                vals = [self._data.get(c, '') for c in self._channels]
                if any(v == '' for v in vals):
                    self.dropped += 1
                w.writerow([clock_str(time.time()),
                            f'{now - self._perf0:.4f}'] + vals)
                self.rows += 1
                if now - last_flush >= self.FLUSH_EVERY:
                    fh.flush()
                    last_flush = now


# ── joining holds back onto the signal ────────────────────────────────────────

def load_holds(path):
    """[(pose, start_s, end_s)] sorted by start.

    Reads the current holds file, and also the older event logs from before it
    existed (a mark starts a pose, `end` closes it), so early recordings stay
    usable. In those, a hold still open when the file ends gets end_s = inf.
    """
    with open(path, newline='', encoding='utf-8') as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return []
    if 'start_s' in rows[0]:
        holds = [(r['pose'], float(r['start_s']), float(r['end_s'])) for r in rows]
        return sorted(holds, key=lambda h: h[1])

    holds, open_ = [], None
    for r in sorted(rows, key=lambda r: float(r['elapsed_s'])):
        t = float(r['elapsed_s'])
        label = (r.get('label') or '').strip()
        pose = (r.get('pose') or '').strip() or label
        if label in ('end', '-'):
            if open_:
                holds.append((open_[0], open_[1], t))
                open_ = None
        elif open_ is None or pose != open_[0]:
            if open_:
                holds.append((open_[0], open_[1], t))
            open_ = (pose, t)
        # same pose again with another label was a note — not a boundary
    if open_:
        holds.append((open_[0], open_[1], float('inf')))
    return holds


def merge_labels(signal_csv, holds_csv=None, out_csv=None):
    """Write a copy of the signal with a `pose` column: the pose of the hold that
    row falls in, or empty between holds.

    Storage keeps the two apart, so fixing a hold never rewrites a signal file.
    This is the joined view for tools that want one flat table.
    """
    base = signal_csv[:-4]
    if holds_csv is None:
        holds_csv = base + '_holds.csv'
        if not os.path.exists(holds_csv):            # recorded before holds files
            holds_csv = base + '_events.csv'
    out_csv = out_csv or base + '_labelled.csv'
    holds = load_holds(holds_csv) if os.path.exists(holds_csv) else []

    n, i = 0, 0
    with open(signal_csv, newline='', encoding='utf-8') as src, \
            open(out_csv, 'w', newline='', encoding='utf-8') as dst:
        reader = csv.reader(src)
        writer = csv.writer(dst)
        writer.writerow(next(reader) + ['pose'])
        for row in reader:
            t = float(row[1])                      # elapsed_s
            while i < len(holds) and holds[i][2] <= t:
                i += 1                             # past this hold's end
            inside = i < len(holds) and holds[i][1] <= t
            writer.writerow(row + [holds[i][0] if inside else ''])
            n += 1
    return out_csv, n, len(holds)


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 3 or sys.argv[1] != 'merge':
        raise SystemExit("usage: python recorder.py merge <session.csv>")
    path, rows, holds = merge_labels(sys.argv[2])
    print(f"{rows:,} rows, {holds} holds -> {path}")
