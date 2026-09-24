"""
recorder.py — session recording for the mats.

Writes the same shape of file the bicep pipeline consumes: one row per sample on
a uniform grid, a wall-clock `Time` column in `H-MM-SS.fff`, one column per
channel. Three files per session:

    <name>.csv          the signal
    <name>_events.csv   labels dropped during the session, with timestamps
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


EVENT_COLUMNS = ['Time', 'elapsed_s', 'pose', 'label']


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
        self.events   = []
        self._meta    = {}

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def elapsed(self):
        return 0.0 if self._perf0 is None else time.perf_counter() - self._perf0

    # ── control ──────────────────────────────────────────────────────────────

    def start(self, out_dir, subject, meta=None):
        """Begin recording. Returns the path of the signal file."""
        if self.active:
            raise RuntimeError("already recording")
        self.reset()
        os.makedirs(out_dir, exist_ok=True)
        self.started = time.time()
        stamp = time.strftime('%Y%m%d_%H%M%S', time.localtime(self.started))
        name = f"{subject or 'session'}_{stamp}"
        self.path = os.path.join(out_dir, name + '.csv')
        self._meta = dict(meta or {})
        self._meta.update({
            'subject_id': subject,
            'started_at': time.strftime('%Y-%m-%dT%H:%M:%S',
                                        time.localtime(self.started)),
            'rate_hz': self._rate,
            'columns': ['Time', 'elapsed_s'] + [f'ch{c}' for c in self._channels],
            'code_version': _git_sha(),
        })
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self.path

    def mark(self, label, pose=''):
        """Drop a timestamped mark carrying the current pose and a label.
        Ignored when not recording."""
        if not self.active:
            return False
        self.events.append({'Time': clock_str(time.time()),
                            'elapsed_s': round(self.elapsed, 4),
                            'pose': pose,
                            'label': label})
        return True

    def stop(self, port_stats=None):
        """Stop, write the sidecars, and return the metadata that was written."""
        if not self.active:
            return None
        self._stop.set()
        self._thread.join(timeout=5)
        self._meta.update({
            'stopped_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'duration_s': round(self.elapsed, 3),
            'rows': self.rows,
            'rows_with_gaps': self.dropped,
            'events': len(self.events),
            'ports': port_stats or {},
        })
        base = self.path[:-4]
        with open(base + '.json', 'w', encoding='utf-8') as fh:
            json.dump(self._meta, fh, indent=2, ensure_ascii=False)
        with open(base + '_events.csv', 'w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=EVENT_COLUMNS)
            w.writeheader()
            w.writerows(self.events)
        return self._meta

    # ── writer thread ────────────────────────────────────────────────────────

    def _run(self):
        period = 1.0 / self._rate
        with open(self.path, 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(self._meta['columns'])
            self._perf0 = time.perf_counter()
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


# ── joining marks back onto the signal ────────────────────────────────────────

def merge_labels(signal_csv, events_csv=None, out_csv=None, none_label=''):
    """Write a copy of the signal with `pose` and `label` columns from the marks.

    A mark applies from its own timestamp until the next one, so a held pose is
    the span between two marks. A mark labelled `end` or `-` closes the current
    span — clearing both columns — without opening a new one. Event files
    recorded before the pose field existed have no `pose` column; they merge
    with an empty pose.

    Storage keeps the two apart — re-labelling never rewrites a signal file, and
    two people can label the same session independently. This is the joined view
    for tools that want one flat table, generated on demand rather than baked in.
    """
    base = signal_csv[:-4]
    events_csv = events_csv or base + '_events.csv'
    out_csv = out_csv or base + '_labelled.csv'

    marks = []
    if os.path.exists(events_csv):
        with open(events_csv, newline='', encoding='utf-8') as fh:
            for row in csv.DictReader(fh):
                label = row['label'].strip()
                pose = (row.get('pose') or '').strip()
                if label in ('end', '-'):
                    pose, label = none_label, none_label
                marks.append((float(row['elapsed_s']), pose, label))
    marks.sort(key=lambda m: m[0])

    n, i, current = 0, 0, (none_label, none_label)
    with open(signal_csv, newline='', encoding='utf-8') as src, \
            open(out_csv, 'w', newline='', encoding='utf-8') as dst:
        reader = csv.reader(src)
        writer = csv.writer(dst)
        writer.writerow(next(reader) + ['pose', 'label'])
        for row in reader:
            t = float(row[1])                      # elapsed_s
            while i < len(marks) and marks[i][0] <= t:
                current = marks[i][1:]
                i += 1
            writer.writerow(row + list(current))
            n += 1
    return out_csv, n, len(marks)


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 3 or sys.argv[1] != 'merge':
        raise SystemExit("usage: python recorder.py merge <session.csv>")
    path, rows, marks = merge_labels(sys.argv[2])
    print(f"{rows:,} rows, {marks} marks -> {path}")
