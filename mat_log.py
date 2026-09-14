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

from mat_ui import (ARROW_MIN, BAUD, MAT_CHANNELS, MAT_LABELS, PORTS, RAW_MAX,
                    RAW_MIN, SLOPE_DEADBAND, SLOPE_N, UPDATE_MS, _checked_body)


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
    if args.loaded:
        # the deadband has to clear the noise DURING a pose, not just on an
        # empty mat: creep under load is a different regime from empty drift
        print(f"\nStand on the mat and hold as still as you can for "
              f"{args.seconds:.0f}s.")
    else:
        print(f"\nLeave the mats EMPTY and untouched for {args.seconds:.0f}s.")
        print("Stay off the floor nearby - footfall carries through.")
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

# A band integrates pressure over its whole area, which defeats the obvious
# test. Moving a foot from one band to another leaves the pressure UNDER that
# foot unchanged — only which band counts it changes — so the total across the
# mat stays put however nonlinear the sensor is. To see the response curve the
# force on a fixed contact patch has to change. That needs a known weight.
#
# Two independent readings come out of this:
#
#   counts/kg   from holding known weights while standing normally. The step
#               from +2kg to +4kg should be the same size as the step from
#               0 to +2kg. If the second step is smaller, the sensor is
#               compressive.
#
#   exponent k  from comparing the whole of body weight against those small
#               increments. If reading ~ force^k then measuring counts/kg at
#               the top of the range and dividing body weight in counts by it
#               gives W/k, not W. So k = (real body weight) / (implied one).
#               This spans the full load range instead of the last 4 kg.
#
# A cross-check comes free: standing on one foot doubles the force on that
# band at the same contact area, so its reading should double.

SEQUENCE = [
    ('off', 'Step OFF the mat completely'),
    ('two', 'Both feet on the mat, one foot per band, no weights'),
    ('one', 'Stand on ONE foot only. Other foot completely off the mat, and '
            'do not hold on to anything'),
    ('two', 'Both feet back on, same spots, no weights'),
    ('w1',  'Both feet, holding the FIRST weight against your chest'),
    ('w2',  'Both feet, holding BOTH weights'),
    ('w1',  'Both feet, holding just the FIRST weight again'),
    ('two', 'Both feet, no weights'),
    ('off', 'Step OFF the mat completely'),
]


def _hold(readers, channels, args, prompt, n, total):
    input(f"\n  [{n}/{total}] {prompt}.\n        Hold still, then press Enter... ")
    time.sleep(args.settle)
    data, t0, _ = _capture(readers, args.seconds)
    means, wob = {}, 0.0
    for ch in channels:
        vals = data.get(ch, [])
        if not vals:
            print(f"        !! no data for ch {ch}")
            return None, None
        means[ch] = _mean(vals)
        wob = max(wob, _std(vals))
    print(f"        total {sum(means.values()):9.0f}   wobble {wob:5.0f}")
    if wob > args.wobble:
        print(f"        !! you moved a lot during that capture")
    return means, wob


def cmd_linearity(args):
    idx = args.mat - 1
    if not 0 <= idx < len(MAT_CHANNELS):
        raise SystemExit(f"--mat must be 1..{len(MAT_CHANNELS)}")
    channels = MAT_CHANNELS[idx]
    w1, w2 = args.weights

    print(f"\nLinearity test on {MAT_LABELS[idx]} (channels {channels}).")
    print(f"Weights: {w1:g} kg and {w2:g} kg.")
    print("\nHold the weights against your chest and stand upright — leaning")
    print("shifts load off the mat and ruins the reading. Keep your feet in")
    print("exactly the same spots the whole way through.")
    print("\nThe weights go on and come back off again, so any drift over the")
    print("session cancels instead of landing on one measurement.")
    if not args.body_kg:
        print("\n  (pass --body-kg YOUR_WEIGHT for the full-range exponent)")
    input("\nPress Enter to begin... ")

    readers = _start_readers()
    caps = []
    for n, (tag, prompt) in enumerate(SEQUENCE, 1):
        means, _ = _hold(readers, channels, args, prompt, n, len(SEQUENCE))
        if means is None:
            raise SystemExit("capture failed")
        caps.append(means)
    for r in readers:
        r.stop.set()

    def total(i):
        return sum(caps[i].values())

    # Positions in SEQUENCE. Levels are selected by POSITION, not by tag, so
    # that each one's mean capture time is identical — the weight block is a
    # palindrome (two, w1, w2, w1, two) centred on the w2 capture, so any drift
    # that is linear over the block cancels exactly in the differences.
    OFF_A, TWO_A, ONE, TWO_B, W1_A, W2, W1_B, TWO_C, OFF_B = range(9)

    r0 = _mean([total(TWO_B), total(TWO_C)])       # centred on W2
    r1 = _mean([total(W1_A), total(W1_B)])         # centred on W2
    r2 = total(W2)
    base = _mean([total(OFF_A), total(OFF_B)])

    print(f"\n{'=' * 66}\nRESULT\n")
    drift = total(OFF_B) - total(OFF_A)
    print(f"  empty mat moved {drift:+.0f} counts over the session")
    print(f"  (drift plus whatever stretch standing on it left behind)")

    # ── absolute scale ───────────────────────────────────────────────────────
    step1, step2 = r1 - r0, r2 - r1
    print(f"\n  (A) how many counts is a kilogram?")
    lab1, lab2 = f"0 -> {w1:g}kg", f"{w1:g} -> {w1+w2:g}kg"
    print(f"      {lab1:<12} {step1:+8.0f} counts   {step1/w1:6.1f} /kg")
    print(f"      {lab2:<12} {step2:+8.0f} counts   {step2/w2:6.1f} /kg")
    if step1 <= 0 or step2 <= 0:
        print("\n      !! a step went the wrong way. The weights were not landing")
        print("         on the mat, or drift swamped them. Stand more upright")
        print("         and keep the captures brisk, then retry.")
        return
    cpk = (step1 + step2) / (w1 + w2)
    agree = min(step1 / w1, step2 / w2) / max(step1 / w1, step2 / w2)
    print(f"      -> {cpk:.1f} counts/kg   (the two agree to {agree*100:.0f}%)")
    if agree < 0.7:
        print("      !! the two disagree badly, so drift is still leaking in.")
        print("         Treat everything below as rough.")
    print(f"      These 4 kg only move the load by a few percent, so this")
    print(f"      measures scale, not curvature. Curvature comes from (B).")

    # ── full range ───────────────────────────────────────────────────────────
    net = r0 - base
    print(f"\n  (B) full-range exponent")
    print(f"      body weight reads {net:.0f} counts above empty")
    if args.body_kg:
        implied = net / cpk
        k = args.body_kg / implied
        print(f"      at {cpk:.1f} counts/kg that implies {implied:.0f} kg, "
              f"against your actual {args.body_kg:g} kg")
        print(f"      -> reading ~ force^{k:.2f}")
    else:
        k = None
        print("      re-run with --body-kg to turn this into an exponent")

    # ── cross-check ──────────────────────────────────────────────────────────
    b0 = caps[OFF_A]
    stood = max(channels, key=lambda c: caps[ONE][c] - b0[c])
    n_one = caps[ONE][stood] - b0[stood]
    n_two = _mean([caps[TWO_A][stood], caps[TWO_B][stood]]) - b0[stood]
    print(f"\n  (C) cross-check — one foot doubles the force on ch{stood}")
    print(f"      one foot {n_one:8.0f}      two feet {n_two:8.0f}")
    k2 = None
    if n_two > 0 and n_one > 0:
        ratio = n_one / n_two
        k2 = math.log(ratio, 2)
        print(f"      ratio {ratio:.2f}  (2.00 if linear)  -> force^{k2:.2f}")
        print("      Indicative only: balancing on one foot changes how that")
        print("      foot presses, and this cannot separate that out.")

    # ── verdict ──────────────────────────────────────────────────────────────
    print(f"\n  {'-' * 62}")
    ks = [x for x in (k, k2) if x is not None]
    if not ks:
        print("  No exponent available. Re-run with --body-kg.")
        return
    if len(ks) == 2 and abs(ks[0] - ks[1]) > 0.15:
        print(f"  (B) and (C) disagree ({ks[0]:.2f} vs {ks[1]:.2f}). Something is")
        print("  off — most likely drift, or leaning while holding the weights.")
        print("  Re-run before trusting either.")
        return
    kk = _mean(ks)
    print(f"  VERDICT: reading ~ force^{kk:.2f}")
    if abs(kk - 1.0) < 0.08:
        print("  Linear. CoP and load-fraction maths can use readings directly.")
    else:
        print(f"  NOT linear. Load fractions would be biased "
              f"{'toward the centre' if kk < 1 else 'toward the extremes'}.")
        print(f"  Correct with reading^{1/kk:.2f} before any CoP maths.")
        print("  Slope colours, the arrow and symmetry scoring are unaffected:")
        print("  they compare directions, or equal against equal.")


def _mean_dicts(dicts):
    return {k: _mean([d[k] for d in dicts]) for k in dicts[0]}


# ── analyse ───────────────────────────────────────────────────────────────────

def _pct(xs, p):
    t = sorted(xs)
    return t[min(len(t) - 1, int(len(t) * p))]


def _load_csv(path):
    """{ch: [(t, value)]} sorted by time."""
    series = collections.defaultdict(list)
    with open(path) as fh:
        for row in csv.DictReader(fh):
            series[int(row['channel'])].append((float(row['t_seconds']),
                                                int(row['value'])))
    for ch in series:
        series[ch].sort()
    return series


def _resample(pairs, dt):
    """Mimic the UI: every dt seconds take the most recent value. The UI polls a
    latest-value dict, so this is what its slope window actually contains."""
    out, i, t, end = [], 0, pairs[0][0], pairs[-1][0]
    while t <= end:
        while i + 1 < len(pairs) and pairs[i + 1][0] <= t:
            i += 1
        out.append(pairs[i][1])
        t += dt
    return out


def _slope_series(vals, n, dt):
    """BandCanvas._compute_slope run over a whole recording."""
    half = n // 2
    sep = (n - half) * dt
    return [(sum(vals[i - half:i]) / half
             - sum(vals[i - n:i - n + half]) / half) / sep
            for i in range(n, len(vals) + 1)]


def _arrow_mags(series, channels, n, dt):
    """Arrow length over the recording, for one mat's (TL, TR, BL, BR)."""
    res = {c: _resample(series[c], dt) for c in channels}
    cut = min(len(v) for v in res.values())
    sl = {c: _slope_series(res[c][:cut], n, dt) for c in channels}
    m = min(len(v) for v in sl.values())
    out = []
    for i in range(m):
        tl, tr, bl, br = (sl[c][i] for c in channels)
        vx, vy = (tr + br) - (tl + bl), (tl + tr) - (bl + br)
        out.append((vx * vx + vy * vy) ** 0.5)
    return out


def cmd_analyse(args):
    series = _load_csv(args.csv)
    if not series:
        raise SystemExit(f"no rows in {args.csv}")
    chs = sorted(series)
    span = max(t for t, _ in series[chs[0]]) - min(t for t, _ in series[chs[0]])
    rate = len(series[chs[0]]) / span
    dt_ui = UPDATE_MS / 1000.0
    mat = args.mat - 1
    chans = MAT_CHANNELS[mat]

    print(f"\n{args.csv}: {len(chs)} channels, {span:.1f}s at {rate:.0f} frames/s")
    print(f"tuning for {MAT_LABELS[mat]} (channels {chans})")

    # ── 1. is the spread fast jitter or slow wander? ──────────────────────────
    print("\n" + "=" * 72)
    print("1. jitter vs drift")
    print("=" * 72)
    print(f"{'ch':>4} {'total std':>10} {'sample-to-sample':>18} {'biggest step':>14}")
    print("-" * 50)
    fastest = 0.0
    for ch in chs:
        v = [x for _, x in series[ch]]
        d = [v[i + 1] - v[i] for i in range(len(v) - 1)]
        fast = _std(d) / (2 ** 0.5)
        fastest = max(fastest, max(abs(x) for x in d))
        print(f"{ch:>4} {_std(v):>10.1f} {fast:>18.1f} "
              f"{max(abs(x) for x in d):>14}")
    print("\n  A sample-to-sample figure far below the total std means the sensor")
    print("  is precise and the spread is the baseline wandering, not noise.")
    print("  Averaging cannot remove a wander; only a longer window dilutes it.")

    # ── 2. does it settle, or keep moving? ────────────────────────────────────
    print("\n" + "=" * 72)
    print("2. is the drift stationary, or is there a warm-up?")
    print("=" * 72)
    nblk = max(2, int(span // 10))
    print(f"{'block':>12} " + " ".join(f"ch{c:<5}" for c in chans))
    print("-" * (13 + 8 * len(chans)))
    blocks = []
    for b in range(nblk):
        lo, hi = span * b / nblk, span * (b + 1) / nblk
        row = []
        for c in chans:
            vals = [v for t, v in series[c] if lo <= t < hi]
            row.append(_mean(vals) if vals else 0.0)
        blocks.append(row)
    base = blocks[0]
    for b, row in enumerate(blocks):
        cells = " ".join(f"{row[i] - base[i]:+7.1f}" for i in range(len(chans)))
        print(f"{b*span/nblk:5.0f}-{(b+1)*span/nblk:4.0f}s {cells}")
    moves = [abs(blocks[-1][i] - blocks[0][i]) for i in range(len(chans))]
    first = [abs(blocks[1][i] - blocks[0][i]) for i in range(len(chans))]
    later = [abs(blocks[b + 1][i] - blocks[b][i])
             for b in range(1, nblk - 1) for i in range(len(chans))]
    print(f"\n  end-to-end movement: {max(moves):.0f} counts over {span:.0f}s"
          f"  ({max(moves) * 60 / span:.0f} counts/min)")
    if later and max(first) > 3 * _mean(later):
        print(f"  !! the first block moves {max(first):.0f} counts vs "
              f"{_mean(later):.0f} typical later — looks like a warm-up.")
        print("     Let the mats sit before capturing a baseline.")
    else:
        print("  no warm-up transient — the drift rate looks steady throughout.")

    # ── 3. how much of the wander is shared across a mat? ─────────────────────
    print("\n" + "=" * 72)
    print("3. is the drift shared between bands?")
    print("=" * 72)
    vals = {c: [v for _, v in series[c]] for c in chs}

    def corr(a, b):
        k = min(len(a), len(b))
        a, b = a[:k], b[:k]
        ma, mb = _mean(a), _mean(b)
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((x - mb) ** 2 for x in b)
        if va <= 0 or vb <= 0:
            return 0.0
        return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb) ** 0.5

    for i, cs in enumerate(MAT_CHANNELS):
        if not all(c in vals for c in cs):
            continue
        within = [corr(vals[a], vals[b])
                  for j, a in enumerate(cs) for b in cs[j + 1:]]
        k = min(len(vals[c]) for c in cs)
        common = [sum(vals[c][x] for c in cs) / len(cs) for x in range(k)]
        raw = _mean([_std(vals[c][:k]) for c in cs])
        res = _mean([_std([vals[c][x] - common[x] for x in range(k)]) for c in cs])
        print(f"  {MAT_LABELS[i]:<6} r={_mean(within):+.3f} between its bands   "
              f"std {raw:5.1f} -> {res:4.1f} once the mat's common mode is "
              f"removed ({100 * (1 - res / raw):.0f}%)")
    cross = []
    for i, cs in enumerate(MAT_CHANNELS):
        for j, ds in enumerate(MAT_CHANNELS):
            if j <= i:
                continue
            cross += [corr(vals[a], vals[b]) for a in cs for b in ds
                      if a in vals and b in vals]
    if cross:
        print(f"  between different mats: r={_mean(cross):+.3f}")
        print("\n  High within-mat correlation means differences between bands on")
        print("  one mat cancel most of the drift. Low between-mat correlation")
        print("  means that cancellation does NOT extend across mats.")

    # ── 4. slope noise per band, and the deadband it implies ──────────────────
    print("\n" + "=" * 72)
    print(f"4. slope noise at the UI's {1/dt_ui:.0f} fps, "
          f"{SLOPE_N}-sample ({SLOPE_N*dt_ui:.2f}s) window")
    print("=" * 72)
    p999 = {}
    print(f"{'ch':>4} {'std':>8} {'p99':>8} {'p99.9':>8} {'max':>8}")
    print("-" * 42)
    for ch in chs:
        sl = _slope_series(_resample(series[ch], dt_ui), SLOPE_N, dt_ui)
        a = [abs(x) for x in sl]
        p999[ch] = _pct(a, 0.999)
        print(f"{ch:>4} {_std(sl):>8.0f} {_pct(a,0.99):>8.0f} {p999[ch]:>8.0f} "
              f"{max(a):>8.0f}")
    print()
    for i, cs in enumerate(MAT_CHANNELS):
        if not all(c in p999 for c in cs):
            continue
        w = max(p999[c] for c in cs)
        flag = "   <-- in use" if i == mat else ""
        now = "" if w <= SLOPE_DEADBAND else "   !! above the current deadband"
        print(f"  {MAT_LABELS[i]:<6} worst p99.9 = {w:>4.0f} counts/s  "
              f"-> SLOPE_DEADBAND {int(round(w*1.15/10))*10}{flag}{now}")

    # ── 5. window length: sensitivity against lag ─────────────────────────────
    print("\n" + "=" * 72)
    print("5. what a different window length would buy")
    print("=" * 72)
    print(f"{'window':>9} {'samples':>9} {'band p99.9':>12} {'arrow p99.9':>13}"
          f" {'lag':>7}")
    print("-" * 55)
    for secs in (0.25, 0.5, 0.7, 1.0, 1.5, 2.0, 3.0):
        n = max(4, int(round(secs / dt_ui)))
        band = max(_pct([abs(x) for x in
                         _slope_series(_resample(series[c], dt_ui), n, dt_ui)],
                        0.999) for c in chans)
        arrow = _pct(_arrow_mags(series, chans, n, dt_ui), 0.999)
        print(f"{secs:>8.2f}s {n:>9} {band:>12.0f} {arrow:>13.0f} "
              f"{secs/2:>6.2f}s")
    print("\n  More seconds is the only lever that works (see 6). But a window")
    print("  longer than the movement averages that movement away too, so match")
    print("  it to the timescale you want to see, not to the lowest noise.")

    # ── 6. does the raw frame rate help? ──────────────────────────────────────
    print("\n" + "=" * 72)
    print("6. would feeding the full frame rate in help?")
    print("=" * 72)
    dt_raw = 1.0 / rate
    for label, dt, secs in ((f"UI {1/dt_ui:.0f} fps", dt_ui, SLOPE_N * dt_ui),
                            (f"raw {rate:.0f} fps", dt_raw, SLOPE_N * dt_ui)):
        n = max(4, int(round(secs / dt)))
        band = max(_pct([abs(x) for x in
                         _slope_series(_resample(series[c], dt), n, dt)],
                        0.999) for c in chans)
        arrow = _pct(_arrow_mags(series, chans, n, dt), 0.999)
        print(f"  {label:<12} {n:>4} samples over {secs:.2f}s   "
              f"band {band:>5.0f}   arrow {arrow:>5.0f}")
    print("\n  Equal numbers mean more samples buy nothing: the wander, not the")
    print("  per-sample noise, sets the floor. Only lengthen the window.")

    # ── 7. the arrow, and what it should be set to ────────────────────────────
    print("\n" + "=" * 72)
    print(f"7. arrow on {MAT_LABELS[mat]}")
    print("=" * 72)
    mags = _arrow_mags(series, chans, SLOPE_N, dt_ui)
    worst_band = max(p999[c] for c in chans)
    print(f"  length: median {_pct(mags,0.5):.0f}  p99 {_pct(mags,0.99):.0f}  "
          f"p99.9 {_pct(mags,0.999):.0f}  max {max(mags):.0f} counts/s")
    print(f"  a single band on the same mat: p99.9 {worst_band:.0f} counts/s")
    if _pct(mags, 0.999) < worst_band:
        print("  -> the arrow is QUIETER than the bands it is built from, because")
        print("     the shared drift cancels when opposite pairs are subtracted")
    for name, val in (("current", ARROW_MIN),
                      ("suggested", int(round(_pct(mags, 0.999) * 1.15 / 10)) * 10)):
        bad = 100.0 * sum(1 for x in mags if x >= val) / len(mags)
        print(f"  ARROW_MIN {name:>9} = {val:>4}  ->  {bad:.2f}% of frames "
              f"would show a false arrow")

    print("\n" + "=" * 72)
    print("SUGGESTED SETTINGS")
    print("=" * 72)
    for i, cs in enumerate(MAT_CHANNELS):
        if all(c in p999 for c in cs):
            print(f"  {MAT_LABELS[i]}: SLOPE_DEADBAND "
                  f"{int(round(max(p999[c] for c in cs)*1.15/10))*10}")
    print(f"  ARROW_MIN = "
          f"{int(round(_pct(mags,0.999)*1.15/10))*10}   (for {MAT_LABELS[mat]})")



# ── entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    n = sub.add_parser('noise', help='measure resting jitter and frame rate')
    n.add_argument('--seconds', type=float, default=60.0)
    n.add_argument('--csv', help='also write every frame to this file')
    n.add_argument('--loaded', action='store_true',
                   help='record while standing on the mat, not empty')
    n.set_defaults(func=cmd_noise)

    l = sub.add_parser('linearity', help='is the reading proportional to force?')
    l.add_argument('--mat', type=int, default=2, help='which mat (1-based)')
    l.add_argument('--weights', type=float, nargs=2, default=[2.0, 2.0],
                   metavar=('KG1', 'KG2'), help='the two known masses, in kg')
    l.add_argument('--body-kg', type=float, dest='body_kg',
                   help='your body weight, for the full-range exponent')
    l.add_argument('--seconds', type=float, default=5.0, help='capture length')
    l.add_argument('--settle', type=float, default=2.0,
                   help='pause after you press Enter before capturing')
    l.add_argument('--wobble', type=float, default=120.0,
                   help='warn if a channel moves more than this during capture')
    l.set_defaults(func=cmd_linearity)

    a = sub.add_parser('analyse', help='re-analyse a CSV from `noise --csv`')
    a.add_argument('csv', help='the file written by `noise --csv`')
    a.add_argument('--mat', type=int, default=2,
                   help='which mat to tune thresholds for (1-based)')
    a.set_defaults(func=cmd_analyse)

    args = ap.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == '__main__':
    main()
