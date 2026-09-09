# Balance Score & Contact Detection — Problem & Method

> Scope: the per-mat "which bands are they standing on, and which way are they
> leaning?" logic in `mat_ui.py` (`MatState`, `BalanceStrip`). Companion to
> [`band_state_detection.md`](band_state_detection.md), which covers the
> per-channel empty/pressed question.

---

## 1. What we are actually measuring

The target is **static yoga poses**, held for 30–60 s. Two metrics matter first:

| Metric | Question | Signal |
| --- | --- | --- |
| **Balance** | Have they shifted left or right since settling into the pose? | Centre-of-pressure ratio |
| **Stability** | Are they jittering while trying to hold still? | Variance of that ratio over time |

Balance comes first because stability is nearly free once balance exists — the
jitter score is just the rolling standard deviation of the balance number.

---

## 2. The drift problem is smaller than it looks

`band_state_detection.md` §1.2–1.3 documents the two drift behaviours: the
baseline creeps while empty, and after a release the band settles on a *new,
higher* floor rather than the original one.

Neither is fatal here, for two separate reasons.

### 2.1 We never release during a pose

The non-return-after-release artifact is the *load-removal* branch of the
hysteresis curve. In a held pose the person steps on once and stays on. That
branch is never traversed while we are measuring.

### 2.2 A ratio cancels proportional creep

What *does* apply is creep under sustained load: a band held at constant force
keeps sagging for tens of seconds. Crucially, creep is roughly **proportional to
load** — a band carrying twice the weight sags roughly twice as much. So if both
sides sag by the same fraction `k`:

```
(k·R − k·L) / (k·R + k·L)  ≡  (R − L) / (R + L)
```

The `k` cancels top and bottom. **The balance number is unchanged.**

> This is why the raw "raw − initial" chart is the *worst* place to judge whether
> drift will hurt us: it displays drift in its rawest form, and that is exactly
> the component the ratio discards.

Measured on the scripted demo (`DemoDriver`, which models 15 % creep over the
first 30 s):

| t | total net load | balance `x` |
| --- | --- | --- |
| 6 s | 2411 | +0.021 |
| 20 s | 2283 | +0.346 |
| 45 s | 2149 | +0.271 |

Total net load sags **−11 %** across the hold. The balance figure tracks the
scripted ±0.35 lean throughout, with no drift correction of any kind.

### 2.3 Consequence: freeze the reference, don't track it

A slowly-adapting reference (an EMA that absorbs drift) was considered and
**rejected for yoga**. A sustained lean is precisely the thing we want to detect,
and a tracking reference would decay it toward zero — the artifact and the signal
would be removed together. Since the ratio already handles creep, the reference
is frozen at pose entry. Hold durations of 30–60 s bound whatever creep remains.

---

## 3. Contact detection: slope finds the moment, level finds the state

The intuition "a large jump means they stepped on, a large drop means they
stepped off" is right — but only about the **transition**.

> Ten seconds into a still hold, every band has slope ≈ 0. Loaded and unloaded
> bands are indistinguishable by slope. Slope cannot answer "which bands are
> they on."

So the two questions are answered by different means:

| Question | Method |
| --- | --- |
| *When* did they step on? | Instantaneous aggregate jump above the rolling empty baseline |
| *Which* bands are they on? | Level above that baseline, latched once the aggregate stops moving |

### 3.1 The state machine

```
EMPTY ──(aggregate jump)──> ADJUSTING ──(aggregate stops moving)──> HOLDING
  ^                                                                    │
  └──────────────────(aggregate returns to baseline)───────────────────┘
```

- **`B0`** — per-channel rolling mean while `EMPTY`. Re-seeded from scratch on
  every step-off, so the new resting floor (§2, and `band_state_detection.md`
  §1.3) is adopted as the new empty rather than read as residual pressure.
- **`B1`** — snapshot taken once the aggregate has been quiet for `MIN_SETTLE_S`.
  `net = B1 − B0`. Snapshotting mid-wobble would bake the wobble into the
  reference, which is why the settle test exists.
- **contact set** — bands with `net ≥ CONTACT_FRAC × max(net)`. A **relative**
  test, so it needs no per-body-weight tuning.

> **Onset must compare the *instantaneous* aggregate against the *rolling*
> `B0_agg`.** Comparing rolling mean to rolling mean gives Δ≈0 and the transition
> never fires. (Same trap as the FSM in the original squat analyser.)

### 3.2 Keeping the step out of its own baseline

By the time the onset threshold is crossed, the ramp-up samples are already
inside the `B0` buffer. On the transition, `B0` is therefore computed from the
**oldest half** of the buffer only (`MatState._baseline(oldest_half=True)`),
which excludes the ramp.

---

## 4. The balance number

Channels are ordered `(TL, TR, BL, BR)` per `MAT_CHANNELS`:

```python
x = ((net[TR] + net[BR]) − (net[TL] + net[BL])) / total    # +1 = right
y = ((net[TL] + net[TR]) − (net[BL] + net[BR])) / total    # +1 = top
```

Normalised to −1…+1, then reported **relative to its value at settle**. Nobody
stands symmetric; their own settled posture is the only meaningful zero.

Per-band nets are clamped at 0 — a band can read slightly below `B0` as load
moves off it, and a negative term distorts the ratio.

---

## 5. The observability constraint

Bands are not point sensors. Each integrates pressure over its whole area.

> **If both feet land inside the same band, left/right balance is physically
> unmeasurable.** Weight moving between two points inside one band produces no
> change in that band's output.

This makes the contact set a **validity check**, not bookkeeping. `valid_lr` is
true only when the contact set straddles the left/right boundary:

```python
valid_lr = bool({TL, BL} & contact) and bool({TR, BR} & contact)
```

When it is false the strip shows *"contact on one side only — reposition feet"*
rather than a number that cannot mean anything. Confirm foot placement against
the lit bands before trusting any balance reading.

---

## 6. Tuning constants

All in the `per-mat balance / contact detection` block in `mat_ui.py`.

| Constant | Default | Meaning |
| --- | --- | --- |
| `B0_WINDOW` | 60 | samples of empty-mat baseline (~2 s at 30 fps) |
| `SETTLE_WINDOW` | 30 | samples of aggregate history for the settle test (~1 s) |
| `ONSET_AGG` | 300 | instantaneous aggregate above `B0_agg` → stepped on |
| `OFFSET_AGG` | 150 | and back below this → stepped off (hysteresis gap) |
| `SETTLE_STD` | 60 | aggregate std below this → settled, safe to snapshot `B1` |
| `MIN_SETTLE_S` | 1.0 | minimum time in `ADJUSTING` before `B1` can be captured |
| `CONTACT_FRAC` | 0.15 | band is "in contact" at this fraction of the peak band |
| `BAL_OK` / `BAL_WARN` | 0.08 / 0.20 | green / amber / red thresholds on the strip |

`ONSET_AGG` and `SETTLE_STD` are the two most likely to need adjusting against
real hardware — they are in raw counts summed across a mat's four channels.

---

## 7. Verifying without hardware

`DemoDriver` is scripted rather than random, and plays all three cases at once:

| | scenario | expected |
| --- | --- | --- |
| Mat 1 | steps on at t=4 s, then leans left↔right (12 s period, ±0.35) | reaches `HOLDING`, all 4 bands in contact, `valid_lr=True` |
| Mat 2 | never occupied | stays `EMPTY` — onset must not false-trigger on noise |
| Mat 3 | both feet in the left column | reaches `HOLDING`, contact on left bands only, `valid_lr=False` |

Run `python mat_ui.py` with `pyserial` absent (or `HAS_SERIAL` forced false) to
watch it. Press `r` at any time to force every mat back to `EMPTY` and re-take
`B0`/`B1`.

---

## 8. What this does not do yet

- **No stability score.** The rolling std of `x` over ~2 s is the natural next
  step and needs no new plumbing.
- **No front/back readout.** `y` is computed and stored but not displayed.
- **Re-baselining on repositioning is manual** (`r`). If the user shifts their
  feet mid-session, `B1` is stale and the balance origin is wrong. Detecting a
  reposition automatically is unsolved.
