# Session Recording — Format and Use

> Scope: `recorder.py` and the recording bar in `mat_ui.py`. This is the format
> every collected session will be in, so changing it later is expensive.

---

## 1. Using it

Run `python mat_ui.py`. The bar above the status line has everything:

| Control | Does |
| --- | --- |
| **subject** | names the session and goes in the sidecar |
| **label** | the text a mark will carry (e.g. `warrior2`) |
| **● Record** | start / stop |
| **Mark** or `m` | drop a timestamped label at this instant |

`r` still re-zeroes the aligned chart. Both hotkeys are ignored while a text box
has focus, so typing a label does not trigger them.

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

`Time`, `elapsed_s`, `label` — one row per mark.

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

## 3. How sampling works, and what it costs

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

## 4. Verified

A demo session recorded through the real UI:

| Check | Result |
| --- | --- |
| Row rate | 100.0 rows/s against 100 Hz configured |
| `Time` parsed by the bicep parser | 267 / 267, no NaN |
| Wall clock vs `elapsed_s` | disagree by 10 ms over the session |
| Marks | both captured, with their labels and times |
| `rows_with_gaps` | 0 |

---

## 5. Known limits

- **One grid for all three mats.** Fine while they all run at 100 Hz. If a
  future device runs at a different rate it needs its own stream, not a column
  in this one.
- **No video or EMG.** Those need a sync event recorded on every stream; see the
  data platform discussion.
- **Marks are instants, not spans.** A pose hold is the gap between two marks.
  If spans turn out to be the common case, that wants a start/stop pair rather
  than a single key.
