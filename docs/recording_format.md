# Session Recording — Format and Use

> Scope: `recorder.py` and the recording bar in `mat_ui.py`. This is the format
> every collected session will be in, so changing it later is expensive.

---

## 1. Using it

Run `python mat_ui.py`. The panel at the bottom has two rows.

**Session fields** — subject, weight, height, experience, and a note box. The
first four are remembered in `mat_ui_settings.json` (local, gitignored — it holds
body weights) and restored on the next launch.

**Pose buttons** — one per protocol condition. Keys **1–9** start a hold for
that pose and **Space** ends it. Starting a new pose while one is running ends
the current one first. Each button shows completed reps against the target, and
those counts are also remembered per subject.

**Timer card** (top right):

| Readout | Shows |
| --- | --- |
| **Session** | time since Record, red while recording |
| **Hold** | pose, rep, and elapsed vs target — turns green at the target |
| **Rest** | after a hold ends, time since — turns green at 30 s |

| Key | Does |
| --- | --- |
| `1`–`9` | start a hold |
| `Space` | end the hold |
| `m` | note, using the text in the note box (or Enter in that box) |
| `r` | re-zero the aligned chart |

Hotkeys are ignored while a text box has focus. **Closing the window while
recording stops the recording first**, so the events file and sidecar are never
lost.

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

### `<name>_events.csv` — the labels

`Time`, `elapsed_s`, `pose`, `rep`, `label` — one row per mark.

Starting a hold writes a mark with the pose and rep and an empty label; ending
it writes the same pose and rep with label `end`. A note writes its text as the
label, carrying the current pose and rep if a hold is running. Files recorded
before the pose or rep fields existed lack those columns and still merge, with
them empty.

Labels live **outside** the signal file. In the bicep pipeline `Reps`, `RIR` and
`actions` are columns bolted onto the signal, which means re-segmenting forces
regenerating everything downstream and fixing one label means rewriting a signal
file. Keeping them separate costs nothing now and avoids that.

### `<name>.json` — the metadata

Subject, start and stop time, sample rate, column list, the mat→channel map,
row count, event count, **the git SHA of the code that recorded it**, and
per-port frame counts and checksum rejects.

Session metadata in a sidecar rather than repeated on every row is the one place
this departs from the old pipeline. It is also the shape a database would ingest
later.

---

## 3. Getting the marks back onto the signal

Marks are **not** a column in the signal file. To produce one:

```bash
python recorder.py merge recordings/subj_20260917_173508.csv
```

That writes `..._labelled.csv` — the same rows plus `pose`, `rep` and `label`
columns.

A mark applies **from its own timestamp until the next one**, so a held pose is
the span between two marks. A mark labelled `end` or `-` closes the current span
without opening a new one.

```
  0.0s  ·
  0.0s  empty   #1
  1.3s  ·
  1.7s  tree_L  #1
  2.8s  ·
  2.8s  tree_R  #1
  3.2s  ·
```

Storing them apart and joining on demand is deliberate: re-labelling never
rewrites a signal file, two people can label the same session independently so
agreement can be measured, and tools that want one flat table still get one.

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
records each port's true frame count, so the assumption is **checkable**:

```json
"ports": {"COM7": {"frames": 5998, "bad_checksum": 0, "status": "ok"}}
```

If a port's frame count is far from `rate_hz × duration_s`, the grid was
undersampling it and the recording should be treated with suspicion.

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
| Marks | both captured, with their labels and times |
| `rows_with_gaps` | 0 |

---

## 6. Known limits

- **One grid for all three mats.** Fine while they all run at 100 Hz. If a
  future device runs at a different rate it needs its own stream, not a column
  in this one.
- **No video or EMG.** Those need a sync event recorded on every stream; see the
  data platform discussion.
- **Marks are instants, not spans.** A pose hold is the gap between two marks.
  If spans turn out to be the common case, that wants a start/stop pair rather
  than a single key.
