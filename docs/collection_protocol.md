# Collection Protocol — First Yoga Dataset

> Six conditions, ~30 minutes per subject. Designed around what the hardware can
> actually measure, not around what a yoga class would pick.

---

## 1. The six conditions

| # | Condition | Contact | What it gives us |
| --- | --- | --- | --- |
| 1 | **Quiet standing** (Tadasana) | 2 feet, together | The reference. Every score is relative to this |
| 2 | **Tree, left foot** | 1 foot | Maximum load contrast against #1 |
| 3 | **Tree, right foot** | 1 foot | Bilateral pair with #2 |
| 4 | **Warrior 2, left forward** | 2 feet, wide | Front/back + left/right asymmetry, spans mats |
| 5 | **Warrior 2, right forward** | 2 feet, wide | Bilateral pair with #4 |
| 6 | **Chair** (Utkatasana) | 2 feet, together | Front/back shift, and it fatigues |

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

### Deliberately excluded

- **Down dog / anything hands-and-feet** until the physical mat arrangement is
  settled. Three mats side by side make a wide strip; down dog needs length.
- **Anything a beginner cannot hold for 20 s.** Falls are not data.
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

**Empty-mat baseline, 30 s, nobody near the mats — at the start *and* the end.**

This is the single most important line in this document. Drift is environmental
and varies session to session ([`findings.md`](findings.md) §6.5), so a baseline
is only valid for its own session. It costs 30 seconds and without it no
position-based metric is ever possible. The end baseline also measures how much
stretch the session left behind.

**Quiet standing, 60 s**, before the poses. That is the person's own sway floor,
which is what thresholds should be set from rather than a fixed constant
([`findings.md`](findings.md) §8.1).

### Per hold

- **30 s**, or 20 s minimum if they cannot manage it. Mark the real start and end.
- **3 holds per condition.** Gives within-subject variance.
- **Step off for 30 s between holds.** Creep does not reset while loaded; running
  holds back to back means each inherits the last one's drift.

### Standardise, or it becomes noise

- **Foot position** — tape marks on the mat, same for everyone where possible.
  Our baseline is only valid for a fixed foot position.
- **Gaze** — eyes open, fixed point at eye level. Gaze changes balance more than
  most people expect.
- **Arms in tree** — hands at the chest, not overhead. Easier and more repeatable.
- **Mat arrangement** — do not move the mats mid-session, or between subjects.

---

## 3. Marks

Use the label field in the recording bar. One mark at the start of each hold
with the condition name, one labelled `end` when they come out:

```
tree_L      → hold starts
end         → hold ends
tree_L      → next rep
```

`python recorder.py merge recordings/<session>.csv` then produces the flat table
with a `label` column ([`recording_format.md`](recording_format.md) §3).

Suggested names: `standing`, `tree_L`, `tree_R`, `warrior2_L`, `warrior2_R`,
`chair`, `warrior2_L_fault`, plus `baseline_empty` and `baseline_standing`.

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
| Can you tell the six conditions apart at all, by eye, on the charts? | If not, no model will either |
| Per-port frame counts in the sidecars ≈ `rate_hz × duration` | The 100 Hz grid was not undersampling |
| Does sway rank the way experience predicts? | Sanity check on the stability measure |

If those pass, scale to 12. If the bilateral pairs disagree, fix that first —
everything downstream inherits it.

---

## 6. Time budget

| | |
| --- | --- |
| Consent, weigh, measure, brief | 5 min |
| Empty baseline + quiet standing | 2 min |
| 6 conditions × 3 holds × (30 s + 30 s rest) | 18 min |
| End baseline, notes | 2 min |
| **Per subject** | **~30 min** |
| 12 subjects | ~6 hours, spread over sessions |
