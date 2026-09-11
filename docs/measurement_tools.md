# Measuring the Mat — Noise and Linearity

> Scope: `mat_log.py`, a standalone bench tool. It does not open the UI and does
> not depend on it beyond reusing the same `$SILINO` parser, so what it measures
> is exactly what `mat_ui.py` sees.

```bash
pip install pyserial          # not stdlib, and not currently installed

python mat_log.py noise        --seconds 60 --csv noise.csv
python mat_log.py linearity    --mat 2
```

---

## 1. Why these two

Every threshold in `mat_ui.py` is sized against an assumed sample jitter of
±50 counts, taken from a hardware note rather than a measurement. And every
future centre-of-pressure or load-fraction number rests on an assumption nobody
has checked: that a band's reading is **proportional to the force on it**.

These are the two measurements that unblock the most downstream work, and
neither takes more than a few minutes.

---

## 2. `noise` — how much does a resting band wobble?

Leave all mats **empty and untouched**, and don't walk near them — footfall
through the floor is visible to these sensors.

```
python mat_log.py noise --seconds 60 --csv noise.csv
```

### What it reports

| Output | Meaning |
| --- | --- |
| per-channel `std` | the number every threshold is derived from |
| per-channel `p2p` | peak-to-peak; the "±50" figure, if that is what it meant |
| `frames/s` per port | the actual frame rate, **not** the 30 fps the UI assumes |
| `rejected by checksum` | frames lost to buffer overrun at 1.5 Mbaud |
| suggested `SLOPE_DEADBAND` / `ARROW_MIN` | sized off the worst channel |

### Reading the result

The `std` vs `p2p` distinction is the point of running this. `SLOPE_DEADBAND`
was set assuming the "±50" meant a standard deviation of 50 — the pessimistic
reading. If it is actually the peak-to-peak *range*, the true standard deviation
is nearer 15–25 and the deadband can come down by half.

That matters directly: the deadband sets the slowest movement the app can see.
At 200 counts/s a full weight transfer has to complete in **under ~3.5 s** to
register at all. Halving it roughly doubles that budget, which is much closer to
how people actually move in a held pose.

The tool prints the suggested constants; paste them into `mat_ui.py` and
re-check against §5 of [`slope_direction_display.md`](slope_direction_display.md).

### The frame-rate warning

The slope window counts **UI frames, not sensor samples**. `_poll` reads whatever
is latest in `_data` every 33 ms. If a port delivers slower than 30 fps the UI
re-reads the same value, the difference between the window halves shrinks, and
every slope is under-reported. The tool warns when it sees this.

---

## 3. `linearity` — is the reading proportional to force?

### 3.1 What is being tested, and why it is not obvious

Two independent properties:

| Property | Meaning | Already known? |
| --- | --- | --- |
| **matched** | equal force on two bands gives equal readings | yes — observed on the bench |
| **linear** | double the force gives double the reading | **unknown** |

A sensor can be perfectly matched and badly nonlinear. Matched is enough for
symmetry work — comparing left against right, or one side of a pose against the
other, puts equal against equal, and any response curve treats both the same.
It is *not* enough for "you are 62% on your front foot", which assumes the
reading is proportional to force.

### 3.2 The test

Body weight does not change when you move it around. So if the sensor is linear,
**the total across the mat is the same no matter how the load is distributed.**

The tool walks you through three configurations:

1. both feet together on a **single** band
2. one foot on each of **two** bands
3. feet apart, weight spread across **four** bands

Each one is preceded by a fresh empty-mat baseline — you are asked to step off
first — so the leftover stretch from the previous configuration does not carry
into the next. It runs the set twice, the second pass in reverse order, so any
drift over the session shows up as disagreement between passes rather than
silently landing on whichever configuration happened to go last.

### 3.3 Reading the result

| Totals | Verdict | Consequence |
| --- | --- | --- |
| agree within ~10 % | linear enough | CoP and load-fraction maths can use readings directly |
| spreading the load **raises** the total | compressive (`k < 1`) | ratios biased **toward the centre** — heavily loaded bands under-read |
| spreading the load **lowers** the total | expansive (`k > 1`) | the opposite bias |

The tool also fits a rough exponent. If a band reads `force^k`, then spreading
the same weight over `N` bands gives a total proportional to `N^(1−k)`, so a
straight line through (log bands, log total) has slope `1 − k`. It reports `k`
and the correcting exponent `1/k`.

Treat that number as an indication, not a calibration: the band count comes from
how concentrated the measured load was, which is itself affected by `k`. A real
calibration needs known weights.

### 3.4 Verified against a simulation

`cmd_linearity` was run against a simulated mat with a known exponent and ±50
counts of jitter:

| Simulated | Totals reported | Recovered `k` | Verdict |
| --- | --- | --- | --- |
| `force^1.00` | 706 / 706 / 701 | 1.01 | "linear enough" |
| `force^0.70` | 706 / 868 / 1062 | 0.70 | "NOT linear" |

---

## 4. If it turns out nonlinear

Not a disaster, and it does not invalidate anything already built:

- **The slope display is unaffected.** It reports direction, and a monotonic
  response curve cannot change the sign of a change.
- **The arrow is mostly unaffected** for the same reason, though the relative
  lengths of arrows at different load levels would be skewed.
- **Symmetry scoring is unaffected** — equal against equal.
- **Only absolute load fractions are biased**, and those can be corrected by
  applying `reading^(1/k)` before the CoP maths, once `k` is known properly from
  known weights.

Which is a good argument for building the reference-free stability and symmetry
scores first: they need `matched`, which is already confirmed, and not `linear`,
which is not.
