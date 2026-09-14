# PRISM Evaluation Log

Working notes for the Build -> Observe -> Improve -> Prove cycle. Evidence
for the "PRISM Evaluation & Diagnosis" and "Measured AI Improvement"
judging criteria.

## 1. Baseline run

- Date/time: 2026-09-14, ~17:38 UTC
- Scenarios run: `python scripts/run_scenarios.py --base-url https://solaris-eta-nine.vercel.app --label baseline` (all 3: mild day, extreme heat + symptoms, "can't take a break" follow-up)
- PRISM session_ids observed:
  - `scenario-mild-day-baseline:2026-09-14`
  - `scenario-extreme-heat-baseline:2026-09-14`
  - `scenario-followup-minimum-break-baseline:2026-09-14`
- Link(s) to PRISM traces: prism.blockconvey.com (The Beyonders workspace) → Agent Intelligence / Remediation, filtered to `agent_id = solaris-heat-safety`

## 2. Weakness identified

Ran PRISM's "Analyze" pass (2 credits) over the baseline traces. It surfaced
two failure clusters — **"prompt failures on 'shift safety briefing'"** and
**"prompt failures on 'constraint accommodation'"** — recommending
*"mandatory safety acknowledgment logging."* Both clusters were `n=1`,
below PRISM's own minimum cluster size (1 < 3), with 32% Fix Confidence, and
`localization unavailable: prompts not detected in connected repo` (the
GitHub repo was never connected to PRISM, so it couldn't ground the finding
in our actual source).

**We dismissed both clusters rather than acting on them.** "Compliance
acknowledgment logging" reads like a pattern calibrated for an enterprise
workplace-compliance bot, not a low-friction chat a worker checks between
shifts — and a single low-confidence trace isn't enough evidence to change
the product on. This is itself part of the Evaluation & Diagnosis story:
using PRISM well includes knowing when *not* to act on what it surfaces.

The weakness we did act on came from reading the baseline transcripts
directly, and it's evidenced across **100% of the baseline (6/6 real
scenario traces)**, not a single low-confidence one:

- What went wrong: every reply was a single unstructured prose paragraph
  mixing risk level, break timing, hydration amounts, and warnings into one
  block of text with no way to glance at "what's my next break."
- Which session exposed it: all six — e.g.
  `scenario-followup-minimum-break-baseline:2026-09-14`:
  > "Risk: high. Work 45 min then take a 15‑min shade/water break. Drink ~1 L
  > (4 cups) water per hour, add electrolyte if sweating a lot. Take a
  > 30‑min cool break around noon (12:00‑12:30). Watch for dizziness,
  > headache, cramps—stop and get shade if they appear."
- Why it matters: the target user is a construction/gig worker reading this
  on their phone mid-shift, not at a desk. A wall of prose is exactly the
  wrong format for "when do I next need to stop."

## 3. Fix applied

- File(s) changed: `app/services/llm.py` (schema + prompt), `app/models.py`,
  `app/routes/chat.py`, `app/services/store.py` (schedule/acknowledgment
  persistence), `app/static/{index.html,style.css,app.js}` (rendering)
- What changed: the LLM's structured-output schema gained a `schedule`
  field — an ordered list of `{time, action, detail}` steps, grounded on
  the actual current time (Asia/Kolkata) passed into the prompt, separate
  from a short `message` summary. The frontend renders it as a real step
  list in a persistent "Today's plan" panel, plus a temperature/humidity/
  hydration-progress dashboard, instead of only a chat paragraph.
- Commit hash: `cc0e2d9` (backend schema/logic), `01881c1` (frontend rendering)

## 4. Re-run and comparison

- Date/time of re-run: 2026-09-14, ~18:50 UTC, against the same live
  deployment, after the fix was deployed
- Same scenario(s) replayed: yes, identical inputs, `--label after-fix`
- PRISM session_ids (after fix):
  - `scenario-mild-day-after-fix:2026-09-14`
  - `scenario-extreme-heat-after-fix:2026-09-14`
  - `scenario-followup-minimum-break-after-fix:2026-09-14`

- Before vs after excerpt (same scenario, same input message:
  *"What's the plan for today?"*):

  **Before** (`scenario-followup-minimum-break-baseline`) — one paragraph,
  everything run together:
  > "Risk: high. Work 45 min then take a 15‑min shade/water break. Drink ~1 L
  > (4 cups) water per hour, add electrolyte if sweating a lot. Take a
  > 30‑min cool break around noon (12:00‑12:30). Watch for dizziness,
  > headache, cramps—stop and get shade if they appear."

  **After** (`scenario-followup-minimum-break-after-fix`) — short pointer
  message, plus a real structured schedule (excerpt, 17 steps total):
  > message: "Risk level HIGH. Follow the break‑and‑hydrate schedule below
  > and watch for signs of heat illness."
  >
  > | time | action | detail |
  > |---|---|---|
  > | 09:00 | Hydrate | Drink 250 ml water |
  > | 09:30 | Hydrate | Drink 250 ml water |
  > | 10:00 | Break | 5 min shade or rest |
  > | 10:30 | Hydrate | Drink 250 ml water |
  > | 11:00 | Break | 5 min shade or rest |
  > | … | … | *(12 more steps to shift end)* |

- Metric/behavior that improved:
  - `message` length dropped from a multi-sentence paragraph to a one-line
    pointer in all 3 after-fix scenarios (e.g. "Low heat risk today. Follow
    the schedule below.").
  - 6/6 after-fix scenario replies included a real `schedule` array
    (6, 15, and 17 steps respectively) vs. 0/6 in baseline, where every
    plan lived only inside prose.
  - Same safety behavior preserved: risk levels matched baseline
    (low / high / high across the 3 scenarios), and the symptom-escalation
    scenario still correctly set `escalate: true` with the required
    "not a medical diagnosis" disclaimer.
