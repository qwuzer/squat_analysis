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

## 1. What the recordings found

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
**This reverses once someone stands on it** (§1.5): load-dependent creep
decorrelates the bands and the cancellation is largely lost.

For the three-mat future this cuts the other way: a load split between a foot on
mat 1 and a foot on mat 3 gets no cancellation at all, and carries the full
independent drift of both.

### 1.3 Window duration is the only lever that works

> Measured on an **empty** mat. §1.5 repeats this with someone standing
> on it, where the gains are much smaller.


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

### 1.5 Standing on the mat is a different — and harder — problem

Everything above describes an **empty** mat. A second 60 s recording taken while
standing still on Mat 2 (`noise --loaded`) changes the conclusions, and it is
the one the thresholds are now set from.

| Mat 2 | Empty | Standing on it |
| --- | --- | --- |
| Sample-to-sample change | 0.8–0.9 | 1.2–1.3 |
| Largest single step | 4–5 | 14–17 |
| Correlation between its bands | **+0.95** | **+0.46** |
| Common mode removable | 80 % | 33 % |
| Single band, slope p99.9 | 130 | 170 |
| **Arrow p99.9** | **106** | **218** |

Two things happen at once when someone stands on it.

**The bands stop drifting together.** Creep scales with load, so four bands
carrying four different loads creep at four different rates. The correlation
that made the arrow quiet on an empty mat collapses from +0.95 to +0.46, and
only a third of the wander is common mode instead of four fifths.

**The arrow therefore loses its advantage.** Empty, it measured *quieter* than
the bands it is built from (106 vs 130) because the shared drift cancelled.
Loaded, it is *noisier* than a single band (218 vs 170) — it combines four
now-independent signals and their noise adds instead of cancelling.

> Tuning from the empty recording would have set `ARROW_MIN = 120`. The real
> floor with someone on the mat is 218. That setting would have flickered
> constantly in use.

**Some of that floor is not noise at all.** A person standing still still sways;
the median arrow length barely moves between the two recordings (33 → 41) while
the tail grows sharply (p99 87 → 173), which is the signature of intermittent
postural corrections rather than a raised noise floor. That has a consequence
worth stating plainly:

- For the **arrow**, sway is a nuisance to be thresholded away.
- For a **stability score**, sway is the entire signal.

The same measurement wants opposite treatment depending on which feature is
reading it, so the stability score must not simply inherit `ARROW_MIN`.

**Longer windows help much less under load.** Empty, 0.70 s → 3.00 s improved
the arrow 7.6×. Loaded it improves only 2× (218 → 107), because body sway has
real low-frequency content that no amount of window dilutes.

| Window | Band p99.9 | Arrow p99.9 | Lag |
| --- | --- | --- | --- |
| 0.70 s | 170 | 218 | 0.35 s |
| **1.00 s** | **121** | **169** | **0.50 s** |
| 1.50 s | 75 | 129 | 0.75 s |
| 3.00 s | 60 | 107 | 1.50 s |

### 1.6 Settings, and why they are conservative

```python
SLOPE_N        = 30    # 1.0 s window
SLOPE_DEADBAND = 170
ARROW_MIN      = 200
```

1.0 s is the knee of the curve: most of the available improvement, half a second
of lag, and still shorter than the deliberate weight shifts it needs to see.

The margins are deliberately not tight, because **the drift is not reproducible
between sessions.** Mats 1 and 3 were unloaded in both recordings and still
changed substantially — Mat 1's spread fell 44 → 21 counts while Mat 3's rose
17 → 31. Whatever drives the wander is environmental, not a fixed property of
each mat, so a threshold fitted exactly to one recording would be wrong on
another day.

### 1.7 The experiment that would separate sensor from body

Put a **dead weight** on the mat — a heavy bag, some plates, anything that does
not fidget — and record 60 s of that:

```bash
python mat_log.py noise --loaded --seconds 60 --csv deadweight.csv
python mat_log.py analyse deadweight.csv --mat 2
```

A dead weight gives pure load-dependent sensor creep. A standing person gives
creep **plus** postural sway. The difference between the two recordings is the
sway, which is both the thing the arrow must ignore and the thing a stability
score must measure. It is also the only way to know which of the two currently
sets the floor.

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

```bash
python mat_log.py linearity --mat 2 --weights 2 2 --body-kg 65
```

### 3.1 Two independent properties

| Property | Meaning | Status |
| --- | --- | --- |
| **matched** | equal force on two bands gives equal readings | confirmed on the bench |
| **linear** | double the force gives double the reading | **unknown** |

A sensor can be perfectly matched and badly nonlinear. Matched is enough for
symmetry work — comparing left against right puts equal against equal, and any
response curve treats both the same. It is *not* enough for "you are 62 % on
your front foot".

### 3.2 Why the obvious test does not work

The first version of this test compared the mat total across three stances and
checked it stayed constant. **That test cannot detect anything**, because a band
integrates pressure over its whole area:

> Moving a foot from one band to another leaves the pressure *under that foot*
> unchanged. Only which band counts it changes. So the mat total stays put
> however nonlinear the sensor is.

Seeing the response curve requires changing the force on a **fixed contact
patch**, which body weight alone cannot do. It needs a known weight.

### 3.3 Why drift, not the sensor, is the hard part

The first attempt at this spread nine captures over about three minutes and
failed outright — one of the weight steps came out **negative**.

The session drifted **+1118 counts** across the mat, roughly 280 per channel, or
~90 counts/min/channel. That is more than double the 43 counts/min an empty mat
shows, because the mat was *loaded* for most of the session and creep under load
is much larger than empty drift.

> 4 kg is worth about 110 counts. The drift was 20× that.

A palindrome cancels drift that is **linear** in time. Creep is not linear — it
decays — so enough residue survived to flip the sign of the smaller step.

### 3.4 The protocol

**Setup:** put both weights on a chair or stool beside you at about hand height.
You must be able to pick them up and put them down without moving your feet or
bending over.

**Phase 1 — chopped, for counts/kg.** Stand on the mat and alternate the weights
on and off six times, a few seconds each:

```
off · ON · off · ON · off · ON · off · ON · off · ON · off · ON · off
```

Each "on" is differenced against the mean of the "off" captures **either side of
it**, so drift that is linear across that one ~10-second cycle cancels exactly.
Six cycles are averaged, and their spread is reported — agreement across cycles
is the evidence that drift was controlled.

**Phase 2 — short, for the absolute numbers.** This is the part that needs a
real empty-mat baseline, so it is kept to five quick captures:

```
step off · two feet · ONE foot · two feet · step off
```

### 3.5 Three results

**(A) Counts per kilogram**, from phase 1. No baseline needed — it is a
difference between two loaded captures. Gives the absolute scale, so everything
can be stated in kg.

It does **not** measure curvature. 4 kg moves the total load by a few percent,
over which even a strongly compressive sensor changes slope by under 1 %.

**(B) The full-range exponent.** This is where the lever arm is. If
`reading ∝ force^k`, measuring counts/kg at the top of the range and dividing
body weight in counts by it gives `W/k`, not `W`:

```
k = (your real body weight) / (the weight the mat implies)
```

An implied weight larger than the real one means compressive.

**(C) Cross-check.** Standing on one foot doubles the force on that band at the
same contact area, so its reading should double. Indicative only — balancing on
one foot changes how the foot presses, and the test cannot separate that out.

(B) and (C) are independent. If they differ by more than 0.15 the tool gives no
verdict and says to re-run phase 2, since (C) needs only a few seconds of
baseline while (B) spans the whole phase.

### 3.6 Verified against a simulation

Simulated as an integrating band (`reading = c · force^k` per foot) with creep
at the rate the real mat showed — 90 counts/min/channel while loaded:

| Simulated | (A) counts/kg | (B) | (C) | Verdict |
| --- | --- | --- | --- | --- |
| `force^1.00` | 30.9 ± 0.6 | 1.00 | 0.98 | **0.99** — "linear" |
| `force^0.70` | 21.5 ± 0.6 | 0.70 | 0.69 | **0.69** — correct with `reading^1.44` |

The six cycles agree to about 2 % under drift that defeated the previous
protocol entirely.

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
