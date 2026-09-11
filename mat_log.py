"""
mat_log.py — bench measurements for the pressure mats.

Two jobs, neither of which needs the UI:

    python mat_log.py noise        how much does a resting band wobble?
    python mat_log.py linearity    is a band's reading proportional to force?

`noise` answers the question every threshold in mat_ui.py depends on, and
prints the SLOPE_DEADBAND / ARROW_MIN those measurements imply. `linearity` is
a guided test: it walks you through standing on the mat three different ways
and checks whether your (unchanged) body weight produces the same total.

Parsing is imported from mat_ui so this measures exactly what the UI sees.
"""

import argparse
import collections
import csv
import math
import threading
import time

try:
    import serial
except ImportError:                                  # pyserial is not stdlib
    serial = None

from mat_ui import (BAUD, MAT_CHANNELS, MAT_LABELS, PORTS, RAW_MAX, RAW_MIN,
                    SLOPE_N, UPDATE_MS, _checked_body)


# ── reader ────────────────────────────────────────────────────────────────────

class LogReader(threading.Thread):
    """Records every frame a port emits.

    The UI keeps only the latest value per channel and samples it at 30 fps.
    That is the right thing for a display and the wrong thing here: the whole
    point is the per-sample distribution and the true frame rate, and a
    latest-value snapshot throws both away.
    """

    def __init__(self, port, ch_offset):
        super().__init__(daemon=True)
        self.port    = port
        self._offset = ch_offset
        self.samples = []            # (monotonic ts, {ch: value})
        self.bad     = 0             # frames rejected by checksum
        self.status  = 'connecting'
        self.stop    = threading.Event()

    def run(self):
        try:
            with serial.Serial(self.port, baudrate=BAUD, bytesize=8,
                               parity='N', stopbits=1, timeout=1) as ser:
                self.status = 'ok'
                while not self.stop.is_set():
                    raw = ser.readline()
                    if not raw:
                        continue
                    text = raw.decode('ascii', errors='replace').strip()
                    if not text.upper().startswith('$SILINO'):
                        continue
                    body = _checked_body(text)
                    if body is None:
                        self.bad += 1
                        continue
                    fields = body.split(',')
                    if len(fields) < 5:
                        continue
                    try:
                        num_ch = int(fields[3])
                    except ValueError:
                        continue
                    vals = {}
                    for i in range(min(num_ch, 4)):
                        idx = 4 + i
                        if idx < len(fields):
                            try:
                                v = int(fields[idx])
                            except ValueError:
                                continue
                            if RAW_MIN <= v <= RAW_MAX:
                                vals[self._offset + i] = v
                    if vals:
                        self.samples.append((time.monotonic(), vals))
        except Exception as exc:                     # port gone, permission, ...
            self.status = str(exc)


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5


def _start_readers():
    if serial is None:
        raise SystemExit("pyserial is not installed — run:  pip install pyserial")
    readers = [LogReader(p, i * 4) for i, p in enumerate(PORTS)]
    for r in readers:
        r.start()
    time.sleep(1.5)                                  # let the ports open
    dead = [r for r in readers if r.status != 'ok']
    for r in dead:
        print(f"  !! {r.port}: {r.status}")
    if len(dead) == len(readers):
        raise SystemExit("no port opened — nothing to measure")
    return readers


def _window(readers, t_from, t_to=None):
    """{ch: [values]} for every frame recorded inside the time window."""
    out = collections.defaultdict(list)
    for r in readers:
        for ts, vals in r.samples:
            if ts < t_from or (t_to is not None and ts > t_to):
                continue
            for ch, v in vals.items():
                out[ch].append(v)
    return out


def _capture(readers, seconds):
    """Block for `seconds`, then return the frames recorded during them."""
    t0 = time.monotonic()
    time.sleep(seconds)
    return _window(readers, t0), t0, time.monotonic()


# ── noise ─────────────────────────────────────────────────────────────────────

def cmd_noise(args):
    print(f"\nLeave the mats EMPTY and untouched for {args.seconds:.0f}s.")
    input("Press Enter to start... ")
    readers = _start_readers()
    print(f"recording {args.seconds:.0f}s ...")
    data, t0, t1 = _capture(readers, args.seconds)
    for r in readers:
        r.stop.set()

    span = t1 - t0
    print("\nper-channel resting behaviour")
    print(f"{'ch':>4} {'frames':>8} {'mean':>9} {'std':>7} {'p2p':>7} "
          f"{'min':>7} {'max':>7}")
    print("-" * 54)
    stds = {}
    for ch in sorted(data):
        xs = data[ch]
        stds[ch] = _std(xs)
        print(f"{ch:>4} {len(xs):>8} {_mean(xs):>9.1f} {stds[ch]:>7.1f} "
              f"{max(xs) - min(xs):>7} {min(xs):>7} {max(xs):>7}")

    print("\nper-port framing")
    for r in readers:
        n = len([1 for ts, _ in r.samples if ts >= t0])
        print(f"  {r.port}: {n / span:6.1f} frames/s   "
              f"{r.bad} rejected by checksum   [{r.status}]")
    poll = 1000.0 / UPDATE_MS
    slowest = min((len([1 for ts, _ in r.samples if ts >= t0]) / span)
                  for r in readers if r.status == 'ok')
    if slowest < poll:
        print(f"  !! slowest port is {slowest:.1f} frames/s but the UI polls at "
              f"{poll:.0f} fps —\n     it will re-read the same value and "
              f"under-report slopes")

    if not stds:
        return
    worst = max(stds.values())
    med = sorted(stds.values())[len(stds) // 2]

    # σ of the two-half-means slope estimate, in counts/s (see
    # docs/slope_direction_display.md §3.1)
    half = SLOPE_N // 2
    sep_s = (SLOPE_N - half) * UPDATE_MS / 1000.0
    factor = (2.0 / half) ** 0.5 / sep_s
    print(f"\nsample jitter: median std {med:.1f} counts, worst {worst:.1f}")
    print(f"slope noise  : {med * factor:.0f} counts/s median, "
          f"{worst * factor:.0f} worst  (window of {SLOPE_N} samples)")
    print("\nsuggested settings for mat_ui.py, sized off the WORST channel:")
    print(f"  SLOPE_DEADBAND = {int(round(worst * factor * 3.3 / 10)) * 10}"
          f"     # 3.3x the slope noise")
    print(f"  ARROW_MIN      = {int(round(worst * factor * 8.0 / 10)) * 10}"
          f"     # each axis sums 4 bands, so ~2x the noise")

    if args.csv:
        with open(args.csv, 'w', newline='') as fh:
            w = csv.writer(fh)
            w.writerow(['t_seconds', 'channel', 'value'])
            for r in readers:
                for ts, vals in r.samples:
                    if ts < t0:
                        continue
                    for ch, v in sorted(vals.items()):
                        w.writerow([f'{ts - t0:.4f}', ch, v])
        print(f"\nraw frames written to {args.csv}")


# ── linearity ─────────────────────────────────────────────────────────────────

# Same body weight, three distributions. If a band's reading is proportional to
# force, the TOTAL across the mat is the same every time — moving your weight
# around cannot change how much of it there is. If spreading the load out raises
# the total, the sensor is compressive (reads high per unit force when lightly
# loaded); if it lowers it, the opposite.
CONFIGS = [
    ('ONE band',   'both feet together on a SINGLE band, as much weight on '
                   'that one band as you can manage'),
    ('TWO bands',  'one foot on each of two bands, side by side'),
    ('FOUR bands', 'feet apart, weight spread across all four bands'),
]


def _settled_nets(readers, channels, args, prompt):
    """Re-baseline on the empty mat, then capture one standing configuration."""
    input(f"\n  1) Step OFF the mat completely, then press Enter... ")
    time.sleep(args.settle)
    base, _, _ = _capture(readers, args.base_seconds)

    input(f"  2) {prompt}\n     Hold still, then press Enter... ")
    time.sleep(args.settle)
    load, _, _ = _capture(readers, args.seconds)

    nets, moved = {}, 0.0
    for ch in channels:
        b, l = base.get(ch, []), load.get(ch, [])
        if not b or not l:
            print(f"     !! no data for ch {ch}")
            return None, None, None
        nets[ch] = _mean(l) - _mean(b)
        moved = max(moved, _std(l))
    total = sum(max(0.0, n) for n in nets.values())
    # participation ratio: 1.0 = all load on one band, 4.0 = evenly over four
    pos = [max(0.0, n) for n in nets.values()]
    n_eff = (sum(pos) ** 2 / sum(p * p for p in pos)) if sum(pos) > 0 else 0.0
    print("     " + "  ".join(f"ch{ch}={nets[ch]:+7.0f}" for ch in channels))
    print(f"     total={total:8.0f}   bands carrying load={n_eff:.2f}"
          f"   wobble during capture={moved:.0f}")
    if moved > args.wobble:
        print(f"     !! you moved a lot during that capture — consider redoing it")
    return total, n_eff, nets


def cmd_linearity(args):
    idx = args.mat - 1
    if not 0 <= idx < len(MAT_CHANNELS):
        raise SystemExit(f"--mat must be 1..{len(MAT_CHANNELS)}")
    channels = MAT_CHANNELS[idx]

    print(f"\nLinearity test on {MAT_LABELS[idx]} (channels {channels}).")
    print("Same body weight, three different distributions. If the sensor is")
    print("linear the TOTAL is the same every time — redistributing your weight")
    print("cannot change how much of it there is.\n")
    print("The mat is re-baselined before every configuration, so the leftover")
    print("stretch from the previous one does not carry into the next.")
    input("\nPress Enter to begin... ")

    readers = _start_readers()
    runs = collections.defaultdict(list)     # name -> [(total, n_eff), ...]

    for rep in range(args.repeats):
        # alternate the order so any drift over the session averages out rather
        # than always landing on the last configuration
        order = CONFIGS if rep % 2 == 0 else list(reversed(CONFIGS))
        print(f"\n{'=' * 62}\npass {rep + 1} of {args.repeats}")
        for name, prompt in order:
            print(f"\n-- {name} --")
            total, n_eff, _ = _settled_nets(readers, channels, args, prompt)
            if total is not None:
                runs[name].append((total, n_eff))

    for r in readers:
        r.stop.set()

    print(f"\n{'=' * 62}\nRESULT\n")
    print(f"{'configuration':<14} {'total':>10} {'spread':>9} {'bands':>7}")
    print("-" * 44)
    summary = []
    for name, _ in CONFIGS:
        vals = runs.get(name, [])
        if not vals:
            continue
        totals = [t for t, _ in vals]
        effs = [e for _, e in vals]
        spread = (max(totals) - min(totals)) if len(totals) > 1 else 0.0
        print(f"{name:<14} {_mean(totals):>10.0f} {spread:>9.0f} "
              f"{_mean(effs):>7.2f}")
        summary.append((name, _mean(totals), _mean(effs)))

    if len(summary) < 2:
        print("\nnot enough configurations captured to conclude anything")
        return

    ref_name, ref_total, _ = summary[0]
    print(f"\nrelative to '{ref_name}':")
    for name, total, _ in summary[1:]:
        pct = 100.0 * (total / ref_total - 1.0) if ref_total else 0.0
        print(f"  {name:<14} {total / ref_total:5.2f}x  ({pct:+.0f}%)")

    worst = max(abs(t / ref_total - 1.0) for _, t, _ in summary) if ref_total else 0
    print()
    if worst < 0.10:
        print(f"  Totals agree within {worst * 100:.0f}% — linear enough. CoP and")
        print("  load-fraction maths can use the readings directly.")
    else:
        print(f"  Totals disagree by up to {worst * 100:.0f}% — NOT linear.")
        print("  Symmetry comparisons are still fine (equal vs equal), but any")
        print("  'you are 62% on your front foot' number will be biased.")

    # reading ~ force^k over an N-band even split gives total ~ N^(1-k),
    # so a straight line through (log N_eff, log total) has slope 1-k
    pts = [(e, t) for _, t, e in summary if e > 0 and t > 0]
    if len(pts) >= 2:
        xs = [math.log(e) for e, _ in pts]
        ys = [math.log(t) for _, t in pts]
        mx, my = _mean(xs), _mean(ys)
        den = sum((x - mx) ** 2 for x in xs)
        if den > 0:
            k = 1.0 - sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
            print(f"\n  rough exponent: reading ~ force^{k:.2f}"
                  f"   (1.00 would be perfectly linear)")
            if abs(k - 1.0) >= 0.10:
                print(f"  a correction of force ~ reading^{1 / k:.2f} would "
                      f"straighten it out")
            print("  (estimated from how concentrated the load was, so treat it")
            print("   as an indication rather than a calibration)")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    n = sub.add_parser('noise', help='measure resting jitter and frame rate')
    n.add_argument('--seconds', type=float, default=60.0)
    n.add_argument('--csv', help='also write every frame to this file')
    n.set_defaults(func=cmd_noise)

    l = sub.add_parser('linearity', help='is the reading proportional to force?')
    l.add_argument('--mat', type=int, default=2, help='which mat (1-based)')
    l.add_argument('--repeats', type=int, default=2, help='passes over the set')
    l.add_argument('--seconds', type=float, default=5.0, help='capture length')
    l.add_argument('--base-seconds', type=float, default=4.0, dest='base_seconds')
    l.add_argument('--settle', type=float, default=2.0,
                   help='pause after you press Enter before capturing')
    l.add_argument('--wobble', type=float, default=120.0,
                   help='warn if a channel moves more than this during capture')
    l.set_defaults(func=cmd_linearity)

    args = ap.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == '__main__':
    main()
