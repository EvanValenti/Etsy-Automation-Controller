# AI-Assisted Human Framing Test

**Framing experiment · local MediaPipe only · production untouched**

## Does a real face box beat a fixed 75px allowance?

Hybrid V1 (current production) nudges the crop using one nose landmark and a
constant pixel allowance. This test swaps that guess for an actual detected face
box, plus a torso band read directly from shoulder/hip landmarks, and adds a new
safety clamp so the crop can never push into the collar where the print starts.

Six `human_model_front` test images, compared frame for frame against the current
Hybrid V1 output.

### Test setup

| | |
|---|---|
| **Method** | MediaPipe Pose (lite) + MediaPipe Face Detector (BlazeFace short-range), both local/CPU |
| **Fallback** | Hybrid V1 → V2 baseline, unchanged |
| **Images tested** | 6 of 6 `human_model_front` |

### Result summary

| Improved | Equivalent | Worse |
|---|---|---|
| 5 | 1 | 0 |

---

## Frame-by-frame comparison

Each case below compares **Hybrid V1 (production)** against the **AI-assisted
(experiment)** output for the same source image.

### 1. Koi t-shirt — full-body, arms at sides

*No face detected · equivalent*

No face detected in either pass (framed from the shoulders down already, hands in
pockets). Scale and crop line land within a few percent of Hybrid V1 —
essentially a no-op, as expected when there's no face to reason about.

### 2. Mushroom-reading tee — forward-facing

*Face detected · improved*

Hybrid V1 leaves the full face in frame, smiling straight at camera. The
AI-assisted crop reads the actual face box and removes the head entirely, landing
right at the collarbone — shirt graphic is the first thing the eye hits,
waistband gives just enough context.

### 3. "Found one" tee — forward-facing

*Face detected · improved*

Hybrid V1 shows nose-to-chin. AI-assisted crops the whole head. Full graphic and
both lines of copy stay clear of the frame edge in both versions, but the AI crop
reads as a tighter, more deliberate product shot.

### 4. Mushroom tee — 3/4 turned pose

*Face detected · improved*

Angled pose. Hybrid V1's fixed 75px nose allowance still leaves most of the face
visible. The real face box lets the AI crop find the actual jawline and cut
cleanly below it, while the horizontal pose correction (unchanged from Hybrid V1)
keeps the design centered despite the turned shoulders.

### 5. Mushroom tee — forward-facing, sweatshirt

*Face detected · improved*

Same pattern as the two above — full head removed instead of partially shown,
graphic fully clear, more legroom given back to the design.

### 6. Mushroom tee — forward-facing, sweatshirt (indigo)

*Face detected · improved*

The important case. Hybrid V1's baseline crop line runs straight through the
bottom line of copy — "RANDOM PEOPLE" is visibly clipped at the canvas edge. The
torso-band-driven vertical placement plus face correction happens to pull the
whole design back into frame with room to spare.

This is a real Hybrid V1 defect the new method fixes, not just a stylistic
preference.

---

## What the debug overlay shows

Debug renders use: **blue** = Hybrid V1 crop · **red** = AI-assisted crop ·
**green** = estimated shirt/torso region. The 3/4-pose overlay additionally shows
the face box, nose, shoulders, hips, torso band, and both crop lines.

On the indigo sweatshirt, the blue Hybrid V1 crop line runs straight through the
last line of copy — a real clipping defect in current production, not a styling
nitpick. The red AI-assisted line sits below it.

On the turned pose, the green torso band tracks the shoulder-to-hip span even
though the body is angled, which is what lets the horizontal correction keep the
design centered.

---

## Findings

### Face detection

Found a confident face (score ≥ 0.5) in 5 of 6 images. In every one of those 5,
it produced a cleaner, more consistent crop line than Hybrid V1's fixed nose+75px
rule — because it knows where the chin actually is, instead of guessing from a
single point.

### Design visibility

Confirmed fully visible by eye in all 6 AI-assisted outputs. One of the 6 Hybrid
V1 outputs (indigo sweatshirt) clips the bottom line of copy — the AI-assisted
version fixes it via the torso-driven vertical placement, not the top-safety
clamp (that clamp never had to fire on this set).

### Design-detection cross-check

The existing pixel-heuristic design detector (`design_detection_experiment.py`)
was run read-only against both methods, per image. It flagged "not fully
contained" for both Hybrid V1 and AI-assisted on all 6 — but its own bounding
boxes reach up into the collar/shoulder-shading area, which its own docstring
already flags as a known false-positive mode. Not treated as a trustworthy signal
here; visual inspection was.

### Legs / lower body

Reduced in step with the face crop, as a side effect of the same vertical
placement change — not from the torso-height scale boost, which never triggered
on this set (all 6 images kept scale equal to V2/Hybrid V1). That specific lever
is unvalidated by this run.

### Regressions

None observed. The one no-face image produced a near-identical crop to Hybrid V1,
as expected.

---

## Worth integrating later?

**The face-aware vertical crop and the design-top safety clamp: yes.** They're a
strict improvement on this test set and they caught a real bug in current
production framing.

**The torso-height scale boost: not yet.** It never fired on these 6 images, so it
has no evidence behind it either way. Worth testing on a larger, more varied
batch (e.g. the real ZIP in `working/extracted`) before trusting it, or dropping
it if it can't be validated.

---

## Isolation

No production files were touched. At the time of the run, this experiment's
outputs were written to `output/human-ai-assisted-framing-test/` and
`output/human-ai-assisted-framing-test-debug/`, separate from Hybrid V1's own
output folders. Both directories have since been archived (see
*Supporting implementation references* below).

---

## Where this sits in the platform

The Etsy automation platform is a set of independent production tools —
AI image generation, mockup generation, video generation, research and draft
editing — coordinated by the **Automation Controller**, which launches each tool,
supervises its run, and collects approved assets into one per-listing folder.

This case study documents a framing experiment inside the **mockup generation**
tool. The article is platform-level engineering documentation and lives with the
Automation Controller; the code it examines lives in the mockup generator.

**Runtime path:** the Controller invokes the mockup generator through its engine
adapter and mockup routes, which is how the framing behaviour described here
reaches a real listing.

| Layer | Path |
|---|---|
| Controller → engine dispatch | `automation-controller/core/adapters/engine_adapter.py` |
| Controller → mockup generator API routes | `automation-controller/api/mockup_generator_routes.py` |
| Controller → listing asset assembly | `automation-controller/api/listing_workspace_routes.py` |

### Supporting implementation references

These are the mockup-generator files this article examines. They are supporting
references for the case study, not its primary location.

| Role | Path |
|---|---|
| Experiment script (this test) | `etsy-mockup-generator/archive/experiments/human_ai_assisted_framing_test.py` |
| Hybrid V1 framing (production baseline under test) | `etsy-mockup-generator/hybrid_human_framing_v1.py` |
| Shared pose/face detection layer (live production dependency) | `etsy-mockup-generator/pose_face_detection.py` |
| Human framing entry point | `etsy-mockup-generator/human_framing.py` |
| V2 baseline fallback | `etsy-mockup-generator/framing_baseline_v2.py` |
| Design detector used for the read-only cross-check | `etsy-mockup-generator/archive/experiments/design_detection_experiment.py` |
| Archived experiment output | `etsy-mockup-generator/archive/old-outputs/human-ai-assisted-framing-test/` |
| Archived debug overlays | `etsy-mockup-generator/archive/debug-outputs/human-ai-assisted-framing-test-debug/` |

---

**Models:** MediaPipe Pose Landmarker (lite) + MediaPipe Face Detector (BlazeFace short-range)
**Runtime:** both local, CPU-only, no paid APIs, no cloud services
**Canonical location:** `docs/engineering/ai-assisted-human-framing.md` (Automation Controller repository)
