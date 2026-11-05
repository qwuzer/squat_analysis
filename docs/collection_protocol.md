# Collection Protocol — First Yoga Dataset

> Seven conditions, ~16 minutes per subject. Designed around what the hardware can
> actually measure, not around what a yoga class would pick.

---

## 1. The conditions

| # | Condition | Contact | What it gives us |
| --- | --- | --- | --- |
| 1 | **Quiet standing** (Tadasana) | 2 feet, together | The reference. Every score is relative to this |
| 1b | **Quiet standing, eyes closed** | 2 feet, together | The validity check — see below |
| 2 | **Tree, left foot** | 1 foot | Maximum load contrast against #1 |
| 3 | **Tree, right foot** | 1 foot | Bilateral pair with #2 |
| 4 | **Warrior 2, left forward** | 2 feet, wide | Front/back + left/right asymmetry, spans mats |
| 5 | **Warrior 2, right forward** | 2 feet, wide | Bilateral pair with #4 |
| 6 | **Chair** (Utkatasana) | 2 feet, together | Front/back shift, and it fatigues |
| 7 | **Forearm plank** | forearms + feet, across mats | The jitter condition — see below |

### Why these

**They differ along the axes our bands can see.** We measured that the bands
localise weakly — one foot on a band raised it only 29% above the two-feet
baseline ([`findings.md`](findings.md) §8.5). So poses that differ by *where*
contact sits within one mat are a poor bet. These differ by **how many support
points**, **left/right split**, and **front/back split** — all gross
distribution changes, which is what weak localisation can still resolve.

**The bilateral pairs are a free validity test.** Tree-left and tree-right
should mirror each other for the same person. If they don't, that is a hardware
or layout problem, not physiology — and we would find out on subject one rather
than after twelve.

**Sway range.** Quiet standing is the floor, tree is the ceiling. Without that
spread a stability score has nothing to discriminate.

**Chair fatigues.** Sway should grow across the hold. That is the only condition
here that tests the trend-within-a-hold metric.

### Why eyes closed

Removing vision reliably increases sway — it is the standard manipulation in
posturography, with a large, known effect. If a stability score does **not** rise
with eyes closed, it is not measuring balance. One minute per subject, within
subject, so it needs no extra people, and it is the most decisive validity test
available.

### Why plank

Plank is the condition most likely to produce **fatigue tremor** — the visible
jitter of a muscle near its limit. That makes it the stability score's best test
after eyes-closed: if the score cannot tell a plank from quiet standing, it is not
measuring steadiness.

Standardise it: **forearm plank**, elbows under shoulders, body straight, forearms
on one mat and feet on another along the row. It is ~1.5 m long, so **check it
fits the layout before the pilot**.

Tremor builds with fatigue, so a fresh subject may show little in 15 s. If the
pilot shows plank barely jittering, plank is the one hold worth lengthening — it
is a single number in `POSES`.

### Deliberately excluded

- **Down dog.** Like plank it needs length along the mats, but its load sits on
  hands and feet at a steep angle that is hard to standardise. Plank covers the
  hands-and-feet family for now.
- **Anything a beginner cannot hold for 15 s.** Falls are not data.
- **Fine variants within a family.** Our 29% figure says we cannot resolve them.

### Optional 7th: a deliberate fault

For warrior 2, add one hold with the weight **too far forward over the front
foot**. Same subject, same session, labelled at capture. That gives a
correctness dimension with no expert rating needed — which is otherwise the
expensive part of any scoring work.

---

## 2. What to record per subject

### Before they step on

| | Why |
| --- | --- |
| **Anonymous ID** (`S01`, not a name) | Everything keys on this |
| **Body weight, kg** | Required to normalise across people, and for any load-fraction work. Weigh them — do not ask |
| **Height, cm** | Stance width scales with it |
| **Yoga experience** (none / some / regular) | Expect it to dominate the stability score |
| **Barefoot** | Standardise it. Socks change everything |

### Every session, without exception

**Empty-mat baseline, 15 s, nobody near the mats — at the start *and* the end.**

This is the single most important line in this document. Drift is environmental
and varies session to session ([`findings.md`](findings.md) §6.5), so a baseline
is only valid for its own session. It costs 15 seconds and without it no
position-based metric is ever possible. The end baseline also measures how much
stretch the session left behind.

**Quiet standing, 30 s**, eyes open then eyes closed, before the poses. That is the person's own sway floor,
which is what thresholds should be set from rather than a fixed constant
([`findings.md`](findings.md) §8.1).

### Per hold

- **15 s**, or 10 s minimum if they cannot manage it.
- **3 holds per condition.** Gives within-subject variance.
- **Step off for 15 s between holds.** Creep does not reset while loaded; running
  holds back to back means each inherits the last one's drift.

Halved from the original 30 s holds to keep a subject to ~15 minutes. The cost:
once the transitions in and out are trimmed, a 15 s hold leaves roughly 11 s of
steady data rather than ~26 s, so per-hold stability estimates are noisier and
repeatability (ICC) will read somewhat lower. Quiet standing at 30 s is still the
standard posturography length and loses nothing.

### Standardise, or it becomes noise

- **Foot position** — tape marks on the mat, same for everyone where possible.
  Our baseline is only valid for a fixed foot position.
- **Gaze** — eyes open, fixed point at eye level. Gaze changes balance more than
  most people expect.
- **Arms in tree** — hands at the chest, not overhead. Easier and more repeatable.
- **Mat arrangement** — do not move the mats mid-session, or between subjects.

---

## 3. Running a session in the app

The operator runs the laptop; the subject only follows instructions.

1. Fill in **subject, weight, height, experience**. They are remembered across
   restarts, so for a returning subject there is nothing to type.
2. Have the subject **off the mat**, then press **Record**. The session clock
   starts and an **empty** hold starts with it.
3. Tell them the pose. Once they are **in it and settled** — final position,
   no longer adjusting their feet — press its **number key**. That ends the
   empty hold and starts the pose.
4. When the hold timer turns green, press **Space**, *then* say "release".
   Space ends the pose and starts the next **empty** hold at once; they step
   off during it. Start the next pose when its 10 s timer is green.

   Holds then contain the pose only — getting on and off falls in the empty
   holds — and every pose has an empty reading right before it. That matters:
   after the first step-off the mat keeps ~2,000 counts it does not give back
   (`findings.md`), so the empty reading from the start is stale for every
   later pose.

   If they fall or put a foot down, press Space at that moment and redo the
   hold.

   | Key | Condition | Target | Reps |
   | --- | --- | --- | --- |
   | 1 | empty mat | 10 s | automatic — no need to press |
   | 2 | quiet standing | 30 s | 1 |
   | 3 / 4 | tree L / R | 15 s | 3 each |
   | 5 / 6 | warrior 2 L / R | 15 s | 3 each |
   | 7 | chair | 15 s | 3 |
   | 8 | warrior 2 L, deliberate fault | 15 s | 1 |
   | 9 | forearm plank | 15 s | 3 |
   | **E** | **eyes closed** on/off — applies to every pose started while on | | |

   **Eyes closed is a switch, not a pose.** Press **E** (or the *Eyes open /
   Eyes closed* button) before starting the pose; the button turns purple and
   the timer says "eyes closed". Poses started while it is on are saved as
   `<pose>_eyes_closed` — `standing_eyes_closed`, `tree_L_eyes_closed` — and
   counted separately on the buttons. It stays on until pressed again, so
   **turn it off afterwards**. Empty holds are never marked.

5. Each button counts its holds (`tree L 2/3`) and turns green when complete,
   so the button row doubles as the session checklist.
6. Press **Stop** (it ends the last empty hold). A report — graph and one
   video frame per hold — is written into the session folder a few seconds
   later (`recording_format.md` §2). Closing the window mid-recording also stops cleanly.

Rep counts are kept per subject across restarts. **reset reps** clears the
current subject's counts if a session is being redone.

Each hold is saved as one row — `pose, start_s, end_s` — in `<session>_holds.csv`.
`python recorder.py merge recordings/<subject>_<date>/<session>.csv` adds a `pose` column to the
signal ([`recording_format.md`](recording_format.md) §2–3). Holds pressed this way
start settled, but a short trim at feature extraction is still worth having.

---

## 4. Video — worth the ten minutes

A phone on a tripod, one angle, whole body in frame.

It is the only way to answer "what actually happened at 14.2 s" when the signal
looks strange, and it is the seed of the multimodal work later.

**To make it alignable: stomp once on the mat at the start and end of each
recording.** A stomp is visible in the video *and* spikes the mat, so it is a
sync event on both streams. A clap only marks the video. Two stomps let you
recover clock offset and drift rather than just offset.

---

## 5. Order of work

**Run two or three subjects through the whole protocol first, then stop and
analyse.** Do not collect twelve people against an unvalidated protocol.

What to check before scaling up:

| Check | Why |
| --- | --- |
| Do the bilateral pairs mirror? | Hardware/layout validity |
| Can you tell the conditions apart at all, by eye, on the charts? | If not, no model will either |
| Per-port frame counts in the sidecars ≈ `rate_hz × duration` | The 100 Hz grid was not undersampling |
| Does sway rank the way experience predicts? | Sanity check on the stability measure |

If those pass, scale to 12. If the bilateral pairs disagree, fix that first —
everything downstream inherits it.

---

## 6. Time budget

| | |
| --- | --- |
| Consent, weigh, measure, brief | 5 min |
| Empty baseline + quiet standing, eyes open and closed | 2 min |
| 19 holds × (15 s + 15 s rest) | 9.5 min |
| End baseline, wrap-up | 1 min |
| **Per subject** | **~16 min** |
| 12 subjects | ~3.5 hours, spread over sessions |
