# Pressure Mat — Findings, Settings and Open Questions

**Last updated:** 2026-09-14 · Update this file whenever a new measurement lands.

This is the living summary. For detail see
[`band_state_detection.md`](band_state_detection.md),
[`slope_direction_display.md`](slope_direction_display.md) and
[`measurement_tools.md`](measurement_tools.md).

---

## 1. What we are building

Real-time feedback for yoga from pressure mats. The end goal is to score
**balance** and **stability** during a held pose, and to guide someone toward a
target pose.

Poses are mostly static. That single fact drives most of the design decisions
below.

## 2. The hardware

| | |
| --- | --- |
| Mats | 3, side by side. Each has 4 sensor bands in a 2×2 grid (12 channels total) |
| Layout | 2 rows × n columns overall |
| Link | One serial port per mat, 1.5 Mbaud, `$SILINO` sentences with an NMEA checksum |
| Frame rate | 100 frames/s per port. Zero checksum rejects in 18,000 frames |
| Resolution | ~14-bit. A resting band reads about 6000 counts |

**Bands are not point sensors.** Each one integrates pressure across its whole
area. It can tell you *how much* load it carries, never *where on itself* that
load sits. Two feet inside one band cannot be told apart.

## 3. The core problem: a reading has no fixed zero

Three properties, all confirmed on the bench:

1. There is no fixed "empty" value. It differs per channel and per session.
2. The empty level drifts while the mat is untouched.
3. After a release the band settles on a **new, higher** floor, not its original
   one.

All three describe the **level**. None describes the **direction of change**.

> A band's resting value is unreliable. The direction it is moving right now is
> not.

So the system measures **rate of change**, and that removes the need for any
baseline, zeroing or calibration step.

## 4. What we measure, and why

| Metric | Answers | Why we need it | Needs a baseline? | Status |
| --- | --- | --- | --- | --- |
| **Band slope** | Which way is this band's load moving? | Immune to every problem in §3 | No | Built |
| **Weight-shift arrow** | Which way is the person shifting? | Turns four bands into one readable direction | No | Built |
| **Stability / sway** | How much did they wobble during the hold? | A balance score that needs no coach — less wobble is objectively better | No | Planned |
| **Symmetry** | Is left/right even? Does one side match the other? | Reference-free correctness check. Works because the bands are matched | Yes | Planned |
| **Load fraction** | What percentage is on the front foot? | The number instructors actually cue | Yes, and a linear sensor | Blocked (§9.1) |
| **Contact set** | Which bands is the person standing on? | Tells which foot is where; also a validity check for left/right | Yes | Planned |

Two points that are easy to miss:

- **The arrow shows movement, not position.** It goes blank when the person
  holds still — which is exactly when a pose is being scored. A held lean needs
  the *position* metrics, and those need a baseline.
- **Stability needs no reference at all.** Zero wobble is objectively good. A
  coach recording is only needed to say what a pose's target *distribution*
  should be, which is a convention, not physics.

## 5. How we measure

### 5.1 The slope estimator

Split the sample window in half and difference the two means:

```
slope = (mean of newest half − mean of oldest half) / (seconds between their centres)
```

Reported in **counts per second**. A plain `newest − oldest` would ride on two
individual samples and inherit the noise of both. Averaging each half first
rejects far more of it.

### 5.2 How thresholds are set

Not from a formula. We record 60 seconds of a real mat, run the real estimator
over the recording, and take the **99.9th percentile** of the result. That is
the level the display must sit above to stay quiet, measured rather than
assumed.

### 5.3 The tools

```bash
python mat_log.py noise --seconds 60 --csv noise.csv    # empty mat
python mat_log.py noise --loaded --csv held.csv         # standing on it
python mat_log.py analyse held.csv --mat 2              # re-analyse, no hardware
python mat_log.py linearity --mat 2                     # guided, ~4 minutes
```

`analyse` works on a saved CSV, so any recording can be re-examined without
touching the mats.

---

## 6. Findings

Three 60-second recordings so far: one empty (**A**), two standing on Mat 2
(**B**, **C**).

### 6.1 The sensor is precise. The drift is slow.

| | Mat 2 |
| --- | --- |
| Change between consecutive samples | **0.8 – 1.3 counts** |
| Spread over 60 seconds | 19 – 42 counts |
| Largest single-sample step | 4 – 17 counts (no glitches) |

The "±50" in the hardware notes is **baseline wander, not measurement noise**.
This matters because the two have opposite remedies: averaging removes white
noise and does nothing to a wander. Only a longer window dilutes a wander.

### 6.2 Drift is shared within a mat, not between mats

| | Recording A (empty) | C (standing) |
| --- | --- | --- |
| Correlation between bands of one mat | +0.95 | +0.72 |
| Removable as common mode | 80 % | 54 % |
| Correlation between different mats | −0.11 | +0.21 |

Bands on the same mat drift **together**. So differences between them cancel
most of the drift, which is why the arrow is quieter than the bands it is built
from (65 vs 84 counts/s in recording C).

That cancellation does **not** extend across mats. When a pose spans two mats,
the load split between them carries the full independent drift of both.

### 6.3 Standing on the mat changes the numbers, and the person dominates them

| Mat 2 | B (standing) | C (standing) |
| --- | --- | --- |
| Sample-to-sample change | 1.2 – 1.3 | **1.2 – 1.3** |
| Arrow floor (p99.9) | **218** | **65** |
| Correlation between bands | +0.46 | +0.72 |

Same mat, same person, same setup. The sensor's precision is *identical* between
the two. Everything else improved 2–3×.

> The floor on a loaded mat is the person's own postural sway, not the sensor.
> In B they fidgeted; in C they stood still.

This has a consequence that is easy to get wrong:

- For the **arrow**, sway is a nuisance to threshold away.
- For a **stability score**, sway is the entire signal.

The two features must not share a threshold.

### 6.4 Window length is the only lever that works

Recording C, Mat 2:

| Window | Band p99.9 | Arrow p99.9 | Lag |
| --- | --- | --- | --- |
| 0.25 s | 286 | 311 | 0.12 s |
| 0.70 s | 127 | 105 | 0.35 s |
| **1.00 s** | **84** | **65** | **0.50 s** |
| 1.50 s | 53 | 39 | 0.75 s |
| 3.00 s | 23 | 24 | 1.50 s |

Feeding the full 100 fps stream in at the same window duration changes nothing
(84 → 82). **More samples buy nothing. More seconds buy a lot.**

The catch: a window longer than the movement averages that movement away too.
Match the window to the timescale you want to see, not to the lowest noise.

### 6.5 Drift never settles, and there is no warm-up

30–43 counts per minute, wandering up and down with no monotonic trend and no
start-up transient. There is no "let it warm up and it stops moving". A baseline
goes stale gradually and must be recaptured each session.

---

## 7. Current settings

All in `mat_ui.py`. Tuned from recordings **B and C**, not from the empty mat.

| Constant | Value | Meaning |
| --- | --- | --- |
| `UPDATE_MS` | 33 | UI poll interval (~30 fps) |
| `SLOPE_N` | 30 | Slope window in samples (1.0 s) |
| `SLOPE_DEADBAND` | 170 | counts/s below which a band reads flat |
| `SLOPE_FULL` | 1200 | counts/s at which the band tint saturates |
| `ARROW_MIN` | 200 | counts/s below which the arrow reads "still" |
| `ARROW_FULL` | 2400 | counts/s that fills the arrow circle |
| `ARROW_MAT` | 1 | Mat 2 — the mat currently in use |

**Why 1.0 s:** the knee of the §6.4 curve. Most of the available improvement,
half a second of lag, still shorter than the weight shifts it must detect.

**Why the thresholds are loose.** Recording C alone suggests `ARROW_MIN = 70`.
Recording B demanded 250. The current 200 covers both. Tuning to the quieter
recording would flicker constantly on a fidgetier day.

---

## 8. Problems we face

| # | Problem | Impact |
| --- | --- | --- |
| 8.1 | **The noise floor is the person, and it varies 3× between sessions** | No fixed threshold is correct for everyone on every day |
| 8.2 | **Mat 1 is about 2× noisier than Mat 2** in every recording (181 vs 84 counts/s at a 1.0 s window) | One global deadband cannot serve all three mats |
| 8.3 | **The arrow goes blank when the person holds still** | It cannot score a held pose, only transitions |
| 8.4 | **Drift cancellation stops at the mat boundary** | Cross-mat poses (warrior 2, lunge) lose it |
| 8.5 | **Bands integrate over their area** | Two feet in one band are indistinguishable; left/right becomes unmeasurable |
| 8.6 | **Sensor linearity is unknown** | Blocks every load-fraction metric |
| 8.7 | **The slope window counts UI frames, not sensor samples** | Harmless at 100 fps, but a slow port would silently under-report every slope |
| 8.8 | **The file header contradicts the code** on which port feeds which channels | We may be labelling the wrong mat. Not yet checked against the wiring |

## 9. What we are missing

### 9.1 Measurements not yet taken

| Measurement | Why it matters |
| --- | --- |
| **Linearity test** (`mat_log.py linearity`) | The bands are known to be *matched* (equal force gives equal readings) but not known to be *linear* (double force gives double reading). Matched is enough for symmetry. Linear is required before "62 % on your front foot" means anything |
| **Dead-weight recording** | A dead weight gives pure sensor creep. A standing person gives creep **plus** sway. The difference isolates the sway, which tells us which one sets the floor |
| **Port-to-mat wiring check** | Resolves 8.8 |

### 9.2 Features not yet built

| Feature | Depends on |
| --- | --- |
| **Per-session calibration** — 10 seconds of quiet standing, measure that person's own floor, set the threshold from it | Nothing. This is the answer to 8.1, and the same 10 seconds gives the stability score its zero point |
| **Stability score** — sway per second over the hold | Nothing. Reference-free |
| **Per-mat deadbands** | Nothing. Answers 8.2 |
| **Empty-mat baseline capture** | Nothing. Unlocks every position-based metric |
| **Symmetry score** | Baseline capture |
| **Contact segmentation** — which bands belong to which foot | Baseline for "which bands are loaded"; slope correlation for "which bands move together" |
| **Global coordinates** | Band positions in cm. The current ±1-per-mat scale does not compose across mats |
| **Target comparison** | Everything above, plus a reference performance |

### 9.3 Suggested order

1. **Per-session calibration** — fixes the biggest problem (8.1) and costs nothing.
2. **Stability score** — reference-free, works for any pose, reuses step 1.
3. **Linearity test** — cheap, and decides whether load fractions are viable.
4. **Empty-mat baseline** — unlocks position metrics.
5. **Symmetry score** — the first reference-free *correctness* metric.

Steps 1–2 need no new hardware knowledge. Step 3 is 4 minutes of bench work.

---

## 10. Things we decided against, and why

| Rejected | Reason |
| --- | --- |
| **Slow-tracking (drifting) reference** | It absorbs drift, but a sustained lean is exactly what a yoga pose is. The reference would erase the signal |
| **Feeding 100 fps into the slope window** | Measured: no improvement at all (§6.4) |
| **Tuning thresholds from an empty mat** | Would have set `ARROW_MIN = 120` against a real in-use floor of 218 |
| **Smallest-enclosing-circle sway area** | Decided by 2–3 extreme points, grows with hold duration, and the published formula is not scale-invariant. A sway *rate* is duration-independent and outlier-tolerant |
| **Global CoP position for wide poses** | Sway area scales with stance width, so two people with different stances are not comparable. Load *fraction* between feet is stance-invariant |
