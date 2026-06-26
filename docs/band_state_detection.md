# Band Pressure-State Detection — Problem & Method

> Scope: the per-channel "is this band empty or pressed?" logic in `mat_ui.py`
> (`BandCanvas`). This documents the problem we hit and the detection method we
> currently use to solve it.

---

## 1. The problem

Each mat band reports a raw count that we need to classify as **empty** (nothing
on it) or **pressed** (weight applied). This turned out to be much harder than a
fixed threshold, for three compounding reasons.

### 1.1 No absolute "empty" value

The empty reading is not a fixed number. A band sitting idle hovers around some
value (e.g. **~6000**) with **±50 jitter**, but that resting level differs per
channel and per session.

> A fixed rule like `pressed = raw > 6300` is meaningless when "empty" itself
> isn't a known constant.

### 1.2 Baseline drift over time

Worse, the empty level **drifts** while the mat is untouched — it does not stay
around one value but slowly creeps (e.g. `6000 → 6150 → 6300`). Any fixed
threshold is eventually crossed by the drifting empty signal alone, producing a
**phantom "pressed"** on an empty mat.

### 1.3 Release doesn't return to the original level

After weight is removed, the band does **not** settle back to its original empty
value. Stepping off can leave it resting at **6100–6300** instead of 6000. So the
post-release "empty" is a *new floor*, higher than the pre-press one — and the
detector must accept that new floor as empty rather than reading the offset as
residual pressure.

### Summary of the constraints

| Constraint | Consequence |
|---|---|
| No fixed empty value | Threshold must be **relative** to a live baseline |
| Baseline drifts while empty | Baseline must **self-update** to track drift |
| Drift must not eat a real press | Baseline update must be **gated** — frozen while pressed |
| Release settles at a new floor | Detector must **re-anchor** to the new resting level |

---

## 2. Approaches we tried

We iterated through several designs; each fixed one issue and exposed the next.

### Attempt A — Fixed zero-offset

Capture the **first reading** as `zero`; report `net = raw − zero`. "Empty" =
`net ≈ 0`.

- ✅ Removes the per-channel offset; every band starts at 0.
- ❌ The fixed zero goes stale as the empty level drifts → `net` creeps up →
  false pressed. (Fails on **1.2**.)

### Attempt B — One-sided EMA + hysteresis

Let `zero` be a slow EMA that updates **only while empty**, with two thresholds
(enter loaded high, leave loaded low) to avoid chatter.

- ✅ Tracks drift; load can't contaminate the baseline.
- ❌ When the release settles **above** the old baseline, the level stays in the
  "loaded" band, so the baseline never resumes tracking → **stuck pressed**.
  (Fails on **1.3** — level alone can't tell "new rest" from "real load".)

### Attempt C — Asymmetric EMA + release-slope gate

Chase fast at rest, crawl slowly while loaded, and force fast tracking on a sharp
**downward slope** (the release event).

- ✅ Solves the stuck-after-release case — the release *event* tells the tracker
  to trust the new floor, which the level alone cannot.
- ⚠️ Two interacting α speeds plus a margin — correct but fiddly to reason about.

### Attempt D — Explicit state machine *(current)*

Replace the level/EMA juggling with a small, explicit two-state machine driven by
**edges (slope)**, not absolute level. This is the method described below.

---

## 3. Current method — empty/pressed state machine

Each channel is either `EMPTY` or `PRESSED`. Transitions are triggered by how
fast the raw signal is moving; the **state** (not the level) decides the colour.

```
            slope ≥ ONSET_SLOPE
        ┌───────────────────────────┐
        │                           ▼
   ┌─────────┐                 ┌──────────┐
   │  EMPTY  │                 │ PRESSED  │
   │ (white) │                 │  (red)   │
   └─────────┘                 └──────────┘
        ▲                           │
        └───────────────────────────┘
   slope ≤ RELEASE_SLOPE  → re-seed baseline := raw
```

### 3.1 Rules

- **Baseline (`zero`)** is seeded from the first reading.
- **While `EMPTY`** the baseline slowly tracks the signal
  (`zero += (raw − zero) × DRIFT_ALPHA`), so slow drift is absorbed and
  `net = raw − zero` stays near 0.
- **A sudden rise** (`slope ≥ ONSET_SLOPE`) → `PRESSED`. The baseline is then
  **frozen**, so a held pose keeps showing its full load instead of fading.
- **A sudden drop** (`slope ≤ RELEASE_SLOPE`) → `EMPTY`, and the baseline is
  **re-seeded to the current reading** — this anchors to the new resting floor
  and is what prevents the phantom-pressure-after-release problem.

### 3.2 Multi-sample slope

A single-sample slope misses **gradual presses**: if weight ramps on over several
frames (each step < `ONSET_SLOPE`), the onset never trips and the drift tracker
quietly absorbs the press. To fix this, slope is measured over a short window:

```
slope = raw_now − raw[SLOPE_WINDOW samples ago]
```

so a slow ramp still accumulates past the threshold.

### 3.3 Parameters (top of `mat_ui.py`)

| Constant | Default | Meaning |
|---|---:|---|
| `SLOPE_WINDOW` | `5` | Samples back used to measure slope. Higher = catches slower presses, adds detection lag. |
| `ONSET_SLOPE` | `200` | Rise over the window that flips `EMPTY → PRESSED`. |
| `RELEASE_SLOPE` | `-200` | Drop over the window that flips `PRESSED → EMPTY`. |
| `DRIFT_ALPHA` | `0.30` | Baseline tracking speed while `EMPTY`. |

### 3.4 Manual re-zero

Pressing **`r`** re-seeds every channel's baseline to its current reading and
forces it back to `EMPTY`. Use it at startup (the first sample is captured
mid-jitter) or if a channel was loaded when the app launched.

---

## 4. Worked example

Stream: rest → gradual press (~+120/sample) → hold → release to a higher floor.

| raw | slope (window) | state | baseline | net |
|----:|---------------:|:------|---------:|----:|
| 6000 | 0 | empty | 6000 | 0 |
| 6005 | 5 | empty | 6001 | 4 |
| 6120 | 120 | empty | 6037 | 83 |
| 6240 | **240** | **pressed** | 6098 (frozen) | 142 |
| 6480 | 490 | pressed | 6098 | 382 |
| 6840 | 600 | pressed | 6098 | 742 |
| 6850 | 130 | pressed | 6098 | 752 |
| 6200 | **−640** | **empty** | 6200 (re-seed) | 0 |
| 6205 | −645 | empty | 6201 | 4 |

- The gradual ramp trips onset via the **windowed** slope (240), where a
  single-sample slope (120) would not have.
- The hold keeps `net` high because the baseline is **frozen**.
- The release re-anchors the baseline to the **new floor** (6200); `net` returns
  to ~0 with no phantom pressure.

---

## 5. Known limitation & next step

**Very gradual** presses or releases whose windowed slope never reaches the
threshold can still be misclassified (a slow lean-on absorbed as drift, or a slow
lift-off briefly staying pressed). At ~30 fps a normal foot landing/lift trips the
window comfortably; if slow transitions become a problem, the options are:

- widen `SLOPE_WINDOW` (more lag), or
- add a **level-based onset** (flip to `PRESSED` when `raw` exceeds the baseline
  by a margin, regardless of slope) while keeping the slope gate for release.

---

## 6. Diagnostics

The main window includes a **raw-signal line chart** (right panel) plotting the
un-zeroed values of the active mat's 4 channels over ~10 s. Use it to read the
actual per-press slope magnitude and tune the thresholds above.
