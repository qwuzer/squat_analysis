# Collection Protocol

> Six poses on three mats, each done correctly three times and with two
> scripted errors. The subject-facing version, with drawings, is the guide
> 瑜伽壓力墊實驗：受試者動作指引; the order, targets and footprints the app
> runs are in `protocol.py`. Replaces the Oct 2026 single-mat protocol
> (standing, tree, warrior 2, chair, plank, eyes closed).

---

## 1. The poses

Mats run front to back: **Mat 1 front, Mat 2 middle, Mat 3 back**. Each has four
bands — left and right, front and back. "Left" is as seen facing the front.

| Pose | Where | Bands it should load | Error A | Error B |
| --- | --- | --- | --- | --- |
| Mountain | middle mat | all four on the middle mat | weight on heels | weight on toes |
| Tree R, then L | middle mat, standing foot only | the standing foot's column, front and back | weight on toes | weight on heel |
| Crescent lunge R, then L | front foot on the front mat, back foot on the back mat | front foot's column on the front mat; back foot's **front** band only (heel up) | weight in back leg | back heel down (adds the back band) |
| Downward dog | hands on the front mat, row 1 or 2 by height; feet on the back mat | both hand bands of that row; both **front** bands of the back mat — heels off | too much weight in hands | uneven hands (staff pick the side) |
| Forearm plank | forearms on the front mat, toes on the back mat | all four on the front mat; both front bands of the back mat | hips piked | hips sagging (mild only) |
| Bridge | feet on the front mat, shoulders on the middle mat | all four on the front mat; both back bands of the middle mat | weight on one foot (staff pick the side) | weight on toes |

The plank toe band and the bridge shoulder band are first guesses, to be
confirmed in the pilot.

**Per pose-side:** 3 correct holds of 30 s, then error A and error B once,
20 s each. Errors are performed only when told, in the guide's words.

---

## 2. What to record per subject

### Before they step on

| | Why |
| --- | --- |
| **Anonymous ID** (`S01`, not a name) | Everything keys on this. Same ID on both days |
| **Session** 1 or 2 | Two sessions on different days, for drift between days |
| **Body weight, kg** | Weigh them — do not ask |
| **Height, cm** | Decides the dog hand row; stance scales with it |
| **Yoga experience** (none / some / regular) | |
| **Barefoot** | Standardise it. Socks change everything |

### Standardise, or it becomes noise

- **Placement** — tape marks on the bands for every pose, the same for
  everyone. A hand or foot in a gap between bands is not measured.
- **Gaze** — eyes open, a fixed point at eye level.
- **Mat arrangement** — never move the mats mid-session or between subjects.
- **Camera** — side view, the whole row of mats and the whole body in frame.

---

## 3. Running a session in the app

The operator runs the laptop; the subject only follows instructions. The app
walks through the script, so there is no label to choose.

1. Fill in **subject ID, session, weight, height, experience**.
2. Have the subject **off all three mats** — step off to the side; the row is
   over 1.5 m long — and press **Record**. An empty hold starts.
3. Wait for **EMPTY · READY** (7 s of clean mat). Read out the instruction on
   screen and have the subject get into the pose.
4. Watch the **footprint**. Green everywhere it should be, nothing orange: the
   subject is on the right bands. Red dashed `missing` or orange `extra`: fix
   the placement first.
5. Once they are **settled**, press **Space**. The hold timer runs; it beeps at
   the target (once for a correct hold, twice for an error).
6. Press **Space**, *then* say "release". An empty hold starts at once; they
   step off. Wait for ready again, and so on.

**The first step-off from each mat matters most** (after the first mountain;
after the first lunge for the front and back mats). Whatever the mat reads
then becomes its empty reading for the session, because the middle mat can
keep most of the load (`findings.md` 8.12). Make sure the subject is fully off
— both feet on the floor beside the row — before the app reads ready.

**If they fall or put a foot down for good:** press **X** — the hold is kept
as discarded and the step stays to redo. A brief touch of the raised foot in
tree: press **T** and let them carry on. Discarded a good hold, or noticed a
problem only after Space: **X X** between poses discards the hold just saved.

**Space before the mat is ready** asks for a second press. Use it only when
the check is wrong (a band reads loaded with nobody on it) — say so in the
notes; that hold's empty reading is then the previous one.

For dog B and bridge A the staff pick the side: press **S** to flip it before
starting; the hold is saved as `dog_B_L` / `dog_B_R`.

Press **Stop** at the end. A graph and one video frame per hold are written
into the session folder a few seconds later (`recording_format.md` §2).

---

## 4. Time budget

| | |
| --- | --- |
| Consent, weigh, measure, brief | 5 min |
| 8 pose-sides × (3 × 30 s + 2 × 20 s) of holds | 17 min |
| 40 empty holds × ~10–12 s | 7–8 min |
| Instructions, getting into poses, rest between poses | ~10 min |
| **Per session** | **~40 min** |

---

## 5. Order of work

**Run three pilot subjects through the whole protocol first, then stop and
analyse.** Check before scaling up:

| Check | Why |
| --- | --- |
| Footprint `ok` in the holds files for most correct holds | The placements and the plank / bridge bands are right |
| Each error moves its expected ratio beyond the subject's own spread across correct holds | The error is observable; otherwise replace it (plank sag → uneven forearms) |
| Tree R and tree L mirror each other | Hardware and layout validity |
| Per-port frame counts in the sidecars ≈ `rate_hz × duration` | The 100 Hz grid was not undersampling |
