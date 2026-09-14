# Measuring the Mat — Noise, Drift and Linearity

> Scope: `mat_log.py`, a standalone bench tool. It does not open the UI and does
> not depend on it beyond reusing the same `$SILINO` parser, so what it measures
> is exactly what `mat_ui.py` sees.

```bash
pip install pyserial                      # not stdlib

python mat_log.py noise --seconds 60 --csv noise.csv     # empty mat
python mat_log.py noise --loaded --csv held.csv          # standing still on it
python mat_log.py analyse noise.csv --mat 2              # re-analyse, no hardware
python mat_log.py linearity --mat 2                      # guided, ~4 minutes
```

`analyse` reads a CSV and needs no mats attached, so a recording can be
re-examined any number of times without standing on anything.

---

## 1. What the first recording found

60 s of empty mat, all three ports, 100 frames/s, **zero checksum rejects across
18,000 frames**. Three results, all of which changed how the thresholds should
be set.

### 1.1 It is drift, not jitter

| | measured |
| --- | --- |
| Sample-to-sample change | **0.8 – 2.7 counts** (largest single step 4–15) |
| Spread over 60 s | 17 – 45 counts |

The sensor is very precise from one reading to the next. Essentially all of the
"±50" in the hardware notes is the **baseline wandering**, not measurement
noise. There are no jumps or glitches in the record.

This matters because the two have opposite remedies. Averaging kills white
noise; it does nothing to a wander. Only a longer observation window dilutes a
wander.

### 1.2 The drift is shared between the bands of a mat

| | correlation |
| --- | --- |
| Between bands of the **same** mat | **+0.80 to +0.95** |
| Between **different** mats | −0.11 (independent) |

Removing each mat's common mode takes out 61–80 % of the wander.

> **Differences between bands on one mat are far cleaner than any single band.**
> That cancellation does **not** extend across mats.

The arrow already exploits this, because both its axes are differences between
opposite pairs. On an empty mat the arrow is *quieter* than the bands it is
built from — 106 counts/s against 130 for a single band on the same mat.

For the three-mat future this cuts the other way: a load split between a foot on
mat 1 and a foot on mat 3 gets no cancellation at all, and carries the full
independent drift of both.

### 1.3 Window duration is the only lever that works

| Window | Samples | Band p99.9 | Arrow p99.9 | Lag |
| --- | --- | --- | --- | --- |
| 0.25 s | 8 | 269 | 275 | 0.12 s |
| 0.50 s | 15 | 167 | 160 | 0.25 s |
| **0.70 s** (current) | 21 | 130 | 106 | 0.35 s |
| 1.00 s | 30 | 91 | 59 | 0.50 s |
| 1.50 s | 45 | 52 | 36 | 0.75 s |
| 3.00 s | 91 | 25 | 14 | 1.50 s |

Feeding the full 100 fps stream in at the *same* window duration changes
nothing — 69 samples over 0.69 s gives band 130 / arrow 107 against 21 samples'
130 / 106. **More samples buy nothing; more seconds buy a lot.**

Noise falls faster than `1/√T`, so the wander is smooth and band-limited rather
than a pure random walk. Between 0.70 s and 1.50 s the arrow improves ~3×.

The catch: a window longer than the movement you want to see averages that
movement away too. **Match the window to the timescale of the movement you care
about** — roughly 1–1.5 s suits the deliberate weight shifts in a held pose,
while catching fast wobble wants a shorter one.

### 1.4 No warm-up, but no settling either

Per-10-second block means wander up and down over a range of ~47 counts with no
monotonic settling and no start-up transient. End to end, ~37 counts/min.

There is no "let it warm up for five minutes and it stops moving". A baseline
goes stale gradually and should be recaptured per session.

---

## 2. `noise` — recording

Empty, for the drift figures:

```bash
python mat_log.py noise --seconds 60 --csv noise.csv
```

Stay off the floor nearby; footfall carries through to the sensors.

**Loaded, which is the recording still missing.** Every number above describes
an *empty* mat, but the deadband has to clear the noise **during a pose**, and
creep under load is a different regime from empty drift:

```bash
python mat_log.py noise --loaded --seconds 60 --csv held.csv
python mat_log.py analyse held.csv --mat 2
```

Stand on the mat and hold as still as you can. If the numbers come back much
worse than the empty case, the thresholds have to be sized off *that*, not off
the empty mat.

### Frame rate

The tool reports frames/s per port and warns if a port is slower than the UI
polls. The slope window counts **UI frames, not sensor samples** — `_poll` reads
whatever is latest every 33 ms, so a slow port means the same value is re-read
and every slope is under-reported. Currently all three ports run at 100 fps
against a 30 fps poll, so the UI is discarding frames, which §1.3 shows costs
nothing.

---

## 3. `linearity` — is the reading proportional to force?

### 3.1 Two independent properties

| Property | Meaning | Status |
| --- | --- | --- |
| **matched** | equal force on two bands gives equal readings | confirmed on the bench |
| **linear** | double the force gives double the reading | **unknown** |

A sensor can be perfectly matched and badly nonlinear. Matched is enough for
symmetry work — comparing left against right, or one side of a pose against the
other, puts equal against equal and any response curve treats both the same. It
is *not* enough for "you are 62 % on your front foot".

### 3.2 The test

Body weight does not change when you move it around, so if the sensor is linear
**the total across the mat is the same however the load is distributed.**

Three configurations — both feet on one band, one foot on each of two, spread
across four — each preceded by a fresh empty-mat baseline, because you are asked
to step off first. That stops the leftover stretch from one configuration
carrying into the next. The set runs twice with the second pass reversed, so
drift over the session shows up as disagreement between passes rather than
silently landing on whichever configuration went last.

### 3.3 Reading the result

| Totals | Verdict | Consequence |
| --- | --- | --- |
| agree within ~10 % | linear enough | CoP and load fractions can use readings directly |
| spreading load **raises** the total | compressive (`k < 1`) | ratios biased **toward the centre** |
| spreading load **lowers** the total | expansive (`k > 1`) | the opposite bias |

It also fits a rough exponent: if a band reads `force^k`, spreading the same
weight over `N` bands gives a total proportional to `N^(1−k)`, so a line through
(log bands, log total) has slope `1 − k`. Treat it as an indication — the band
count comes from how concentrated the measured load was, which is itself
affected by `k`. A real calibration needs known weights.

### 3.4 Verified against a simulation

| Simulated | Totals reported | Recovered `k` | Verdict |
| --- | --- | --- | --- |
| `force^1.00` | 706 / 706 / 701 | 1.01 | "linear enough" |
| `force^0.70` | 706 / 868 / 1062 | 0.70 | "NOT linear" |

---

## 4. If it turns out nonlinear

Nothing already built breaks:

- **Slope colours are unaffected** — they report direction, and a monotonic
  response curve cannot change the sign of a change.
- **The arrow is mostly unaffected**, for the same reason; only the relative
  lengths of arrows at different load levels would skew.
- **Symmetry scoring is unaffected** — equal against equal.
- **Only absolute load fractions are biased**, correctable with
  `reading^(1/k)` once `k` is known properly.

Which argues for building the reference-free stability and symmetry scores
first: they need `matched`, which is confirmed, and not `linear`, which is not.
