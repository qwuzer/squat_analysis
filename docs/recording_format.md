# Session Recording — Format and Use

> Scope: `recorder.py` and the recording bar in `mat_ui.py`. This is the format
> every collected session will be in, so changing it later is expensive.

---

## 1. Using it

Run `python mat_ui.py`. The panel at the bottom has two rows.

**Session fields** — subject, weight, height, experience. Remembered in
`mat_ui_settings.json` (local, gitignored — it holds body weights) and restored
on the next launch.

**Pose buttons** — one per protocol condition. Keys **1–9 and 0** start a hold for
that pose and **Space** ends it. Starting a new pose while one is running ends
the current one first. Each button shows how many holds of that pose are done
against the target (`tree L 2/3`); those counts are remembered per subject.
They are an operator aid only — they are not written to the data.

**Timer card** (top right):

| Readout | Shows |
| --- | --- |
| **Session** | time since Record, red while recording |
| **Hold** | pose, which hold of the target, and elapsed vs target — turns green at the target |
| **Rest** | after a hold ends, time since — turns green at 15 s |

| Key | Does |
| --- | --- |
| `1`–`9`, `0` | start a hold |
| `Space` | end the hold |
| `r` | re-zero the aligned chart |

Hotkeys are ignored while a text box has focus. **Closing the window while
recording stops the recording first**, so the hold in progress and the sidecar
are never lost.

Files land in `recordings/`, which is gitignored.

---

## 2. What gets written

Three files per session, named `<subject>_<YYYYmmdd>_<HHMMSS>`.

### `<name>.csv` — the signal

| Column | |
| --- | --- |
| `Time` | wall clock, `H-MM-SS.fff` |
| `elapsed_s` | seconds since recording started, 4 dp |
| `ch0` … `ch11` | one column per channel, raw counts |

`Time` deliberately matches the format the bicep pipeline's
`time_str_to_seconds()` already parses, so existing tooling reads these files
without modification. `elapsed_s` is there because it is what any analysis
actually wants, and because comparing the two exposes clock problems.

### `<name>_holds.csv` — the labels

One row per hold:

```
pose,start_s,end_s
empty,0.0076,0.8198
tree_L,1.1341,2.0612
tree_R,2.0657,2.5951
```

`start_s` and `end_s` are on the **same clock as `elapsed_s` in the signal
file** — both start at 0 when Record is pressed. So a hold is exactly the signal
rows with `start_s <= elapsed_s < end_s`. Subject and body measurements are in
the sidecar, so they are not repeated per hold.

Each row is appended the moment its hold ends, so a crash mid-session loses at
most the hold in progress.

**Why there is no rep column.** A hold's number — the 2nd tree L, say — is
recoverable by sorting a subject's holds of that pose by start time. It will be
needed (repeatability across holds 1–3 is the ICC metric), but it is derived,
not stored.

**These are raw button times.** A hold starts when the operator presses the key,
i.e. when the subject is *told* to get into the pose, and ends when they are told
to come out. So the first second or two is the transition in, and the last moment
is the transition out — both fast, large movements. Feature extraction should
use a trimmed window, `[start_s + trim, end_s − trim]`; the trim is a
feature-extraction parameter to tune, which is exactly why it is not baked in
here.

Labels live **outside** the signal file. In the bicep pipeline `Reps`, `RIR` and
`actions` are columns bolted onto the signal, which means re-segmenting forces
regenerating everything downstream and fixing one label means rewriting a signal
file. Keeping them separate costs nothing and avoids that.

### `<name>.json` — the metadata

Subject, start and stop time, sample rate, column list, the mat→channel map,
row count, hold count, subject info (weight, height, experience), **the git
SHA of the code that recorded it**, and
per-port frame counts and checksum rejects.

Session metadata in a sidecar rather than repeated on every row is the one place
this departs from the old pipeline. It is also the shape a database would ingest
later.

---

## 3. Getting the holds back onto the signal

```bash
python recorder.py merge recordings/S04_20261001_140319.csv
```

That writes `..._labelled.csv` — the same rows plus one `pose` column: the pose of
the hold a row falls in, or empty between holds.

```
  0.01s  empty
  0.82s  ·          (between holds — resting, stepping off)
  1.14s  tree_L
  2.07s  tree_R
  2.60s  ·
```

Recordings made before the holds file existed have an `_events.csv` log
instead; `merge` reads those too.

---

## 4. How sampling works, and what it costs

Each mat emits at ~100 Hz on its own clock, and the reader threads keep only the
latest value per channel. The recorder samples that shared state on a **fixed
100 Hz grid**.

So the occasional sample is read twice or skipped, because the mats' clocks and
the grid are independent. For postural data — whose content sits below a few Hz,
and whose sample-to-sample change we measured at 0.8–2.7 counts
([`measurement_tools.md`](measurement_tools.md) §1.1) — that is immaterial.

The alternative, logging every frame in long format, is lossless but does not
match what downstream tooling expects. The compromise is that the sidecar
records each port's true frame count **from Record to Stop**, so the assumption
is **checkable**:

```json
"ports": {"COM7": {"frames": 5998, "bad_checksum": 0, "status": "ok"}}
```

If a port's frame count is far from `rate_hz × duration_s`, the grid was
undersampling it and the recording should be treated with suspicion.

Recordings made before 2026-10-01 counted from app launch instead, so their
frame counts are too high and this check does not apply to them. The signal
itself is still checkable: `rows / duration` and the longest run of identical
rows per port.

`rows_with_gaps` counts grid ticks where some channel had no value yet — nonzero
only at the very start, or if a port drops out mid-session.

---

## 5. Verified

A demo session recorded through the real UI:

| Check | Result |
| --- | --- |
| Row rate | 100.0 rows/s against 100 Hz configured |
| `Time` parsed by the bicep parser | 267 / 267, no NaN |
| Wall clock vs `elapsed_s` | disagree by 10 ms over the session |
| Holds | written in order, each ending after it starts; merged spans match |
| `rows_with_gaps` | 0 |

---

## 6. Known limits

- **One grid for all three mats.** Fine while they all run at 100 Hz. If a
  future device runs at a different rate it needs its own stream, not a column
  in this one.
- **No video or EMG.** Those need a sync event recorded on every stream; see the
  data platform discussion.
- **No way to discard a bad hold.** If a subject falls 4 s in, the hold is
  still written and still counts on the button. Delete the row from
  `_holds.csv` by hand for now.
