# Slope Direction Display — Problem & Method

> Scope: the per-band red/green colouring in `mat_ui.py` (`BandCanvas._slope`,
> `BandCanvas._apply_slope`). Companion to
> [`band_state_detection.md`](band_state_detection.md), which covers the
> per-channel empty/pressed question.

---

## 1. Why rate of change, not level

`band_state_detection.md` §1 lists three properties that make an absolute
reading almost unusable: there is no fixed "empty" value, the empty level drifts
while untouched, and after a release a band settles on a *new, higher* floor
rather than its original one.

Every one of those is a statement about the **level**. None of them is a
statement about the **direction of movement**.

> It does not matter what a band is resting at, or that it never came back to
> where it started. Only which way it is moving right now.

That is what this view shows, and it needs no baseline, no zeroing and no
calibration step.

## 2. The trait this exposes

Bands under similar load change at a similar rate. So a whole-body movement has
a signature that is visible across the four bands at once:

| Movement | Front pair (TL, TR) | Back pair (BL, BR) |
| --- | --- | --- |
| Tilt onto the toes | rising, similar rate | falling, similar rate |
| Rock back onto the heels | falling | rising |
| Press down evenly | rising, all four alike | rising, all four alike |
| Hold still | flat | flat |

The *pairing* is the information. Two bands moving together at the same rate say
the load is being transferred as a unit; a single band moving alone says
something local is happening.

---

## 3. Method

### 3.1 Slope estimate

A plain endpoint difference (`newest − oldest`) rides on two individual samples
and inherits the full jitter of both. Instead the window is split in half and the
means are differenced:

```python
delta = mean(newest half) − mean(oldest half)
slope = delta / (samples between the two half-centres × dt)      # counts/s
```

With `SLOPE_N = 21` (~0.7 s at 30 fps) each half averages 10 samples, cutting the
noise on each term by √10 before they are subtracted. Reported in **counts per
second**, so the threshold means something physical and is independent of the
frame rate.

### 3.2 Deadband

Noise alone produces a non-zero slope, so a bare `slope > 0 ? red : green` test
would flicker red/green continuously on a motionless band. Anything below
`SLOPE_DEADBAND` is therefore drawn flat (grey), and the band is only coloured
once it is moving faster than the noise floor.

### 3.3 Intensity

The background tint scales with `|slope|` up to `SLOPE_FULL`, so bands changing
at the same rate render at the same intensity. That is deliberate: comparing
rates *between* bands is the entire point of the view, and matching colours make
the comparison immediate without reading the numbers.

The numeric slope (`+532/s`) is printed in the band's top-right corner when it is
above the deadband, for when the exact figure is wanted.

---

## 4. Tuning constants

| Constant | Default | Meaning |
| --- | --- | --- |
| `SLOPE_N` | 21 | samples in the slope window (~0.7 s at 30 fps) |
| `SLOPE_DEADBAND` | 200 | counts/s below which a band reads flat |
| `SLOPE_FULL` | 1200 | counts/s at which the tint reaches full intensity |
| `SLOPE_UP` | `#FF3B30` | red — value rising |
| `SLOPE_DOWN` | `#22C55E` | green — value falling |

`SLOPE_DEADBAND` is the one to adjust against real hardware, and it trades
directly against `SLOPE_N`:

- **flickering on a still mat** → raise `SLOPE_DEADBAND`, or raise `SLOPE_N` to
  average over more samples
- **slow movements not registering** → lower `SLOPE_DEADBAND`
- **colour lags the movement** → lower `SLOPE_N` (and expect to raise the
  deadband to compensate, since a shorter window is noisier)

Press `r` to clear every band's slope window and restart from flat.

---

## 5. Measured behaviour

`DemoDriver` is scripted rather than random and plays three cases at once, with
±50 counts of jitter matching the resting jitter in the hardware notes:

| | scenario | expected |
| --- | --- | --- |
| Mat 1 | tilting front↔back, 5 s cycle | front pair and back pair coloured oppositely, swapping each half-cycle |
| Mat 2 | empty | all four flat |
| Mat 3 | stood on, held still | all four flat |

Observed at a quarter-cycle into the tilt:

```
mat1: TL +236/s  TR +240/s  |  BL -207/s  BR -205/s
mat1: TL -481/s  TR -358/s  |  BL +289/s  BR +333/s     (half a cycle later)
```

The pairs track each other to within a few percent, and reverse together.

**False positives:** across 60 s (1818 frames) on the eight bands that never
move, 7 band-frames read non-flat — **0.05 %**, all single-frame and all at the
lowest tint intensity. Note this is against σ = 50 jitter, which is a pessimistic
reading of the "±50" in the hardware notes; if that figure is peak-to-peak the
real false-positive rate will be far lower.

---

## 6. What this does not do

- **No sense of magnitude.** A band under 700 counts of load and one under 70
  colour identically if they are changing at the same rate. Direction only.
- **Flat is ambiguous.** A motionless empty band and a motionless loaded band
  both read grey. Distinguishing them is `band_state_detection.md`'s job.
- **No pairing logic.** The four bands are coloured independently; recognising
  "front pair rising together" is left to the eye, not computed.
