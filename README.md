# Solaris — Heat Safety Companion

A conversational AI assistant that helps outdoor/gig workers — construction
workers, specifically — manage heat risk during their shift. Built overnight
for **ForgeAI** at VIT Gravitas '26.

## What it does

1. **Onboarding (one-time).** A worker tells Solaris their work type,
   typical shift hours, and location.
2. **Daily check-in.** The worker messages the bot. Solaris fetches today's
   real weather (temperature, humidity) for their location, reasons over
   that plus their profile, and returns a risk level (low / moderate / high
   / extreme), a break-and-hydration schedule tuned to today's severity and
   their work type, and a clear escalation warning on dangerous days.
3. **Follow-up conversation.** The worker can ask follow-up questions
   ("I can't take a break right now, what's the minimum?") and Solaris
   answers using today's actual severity and the specific question, not
   generic advice.
4. **Symptom escalation.** If the worker describes heat-illness symptoms
   (dizziness, confusion, not sweating, etc.), Solaris gives clear, basic
   first-aid guidance — stop working, find shade, hydrate, seek help if
   severe. General safety information only, not a medical diagnosis.

## Architecture

```
app/
├── main.py                 FastAPI app, mounts routers
├── config.py                env-based settings
├── models.py                  Pydantic request/response/profile schemas
├── routes/
│   ├── onboarding.py           POST /onboarding
│   └── chat.py                  POST /chat
└── services/
    ├── weather.py                Open-Meteo geocoding + current weather
    ├── llm.py                     prompt construction + structured-reply parsing
    ├── prism.py                    the one LLM call (Groq via OpenAI SDK), PRISM tracing seam
    └── store.py                     in-memory worker profiles + conversation history
scripts/
└── run_scenarios.py         repeatable scenario battery for PRISM before/after comparisons
docs/
└── prism_evaluation.md      working log for the Observe/Improve/Prove evidence
tests/
    test_onboarding.py, test_chat.py
```

Every LLM call — onboarding-time or follow-up — goes through
`app/services/prism.py`. That's the one seam where tracing is wired in, so
changing PRISM's credentials or host never touches route or prompt code.

## PRISM integration

Onboarded via prism.blockconvey.com/onboarding. The real integration is a
**side-channel trace POST**, not a proxy the LLM call routes through (an
earlier version of this doc guessed at a "zero-code proxy" model before
onboarding — that guess was wrong): `prism.py` calls Groq directly, then
best-effort POSTs one trace per reply to `{PRISMTRACE_HOST}/api/traces`
with header `X-PRISMtrace-Key`. A missing `PRISMTRACE_API_KEY` or a failed
POST never affects the chat reply.

- `agent_id` is a hardcoded constant, `"solaris-heat-safety"`, sent as an
  extra field on each trace (not in PRISM's documented schema, but
  harmless if ignored).
- `session_id` is `"<worker_id>:<YYYY-MM-DD>"` — stable per worker per day,
  so a whole day's conversation traces as one PRISM trajectory.
- Set `PRISMTRACE_API_KEY` / `PRISMTRACE_PROJECT_ID` / `PRISMTRACE_HOST` in
  `.env` to enable tracing; leave `PRISMTRACE_API_KEY` blank to disable it.
- **Not yet verified**: the credential handshake and a live trace haven't
  actually been confirmed against PRISM's dashboard. See `CLAUDE.md` for
  why and what's left to check.
- `scripts/run_scenarios.py` replays a fixed set of onboarding +
  conversation scenarios (mild day, extreme heat + symptoms, "can't take a
  break" follow-up) against the running API. Run it before and after a
  fix to get directly comparable PRISM traces — see
  `docs/prism_evaluation.md` for the log template.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
cp .env.example .env          # then fill in GROQ_API_KEY (and PRISM_* once available)
```

### LLM: Groq

Solaris calls [Groq](https://console.groq.com) through the OpenAI Python SDK
(`base_url=https://api.groq.com/openai/v1`). Get a key at
https://console.groq.com/keys and set:

- `GROQ_API_KEY` - required.
- `GROQ_MODEL` - defaults to `openai/gpt-oss-120b`, Groq's own recommended
  replacement for the now-deprecated `llama-3.3-70b-versatile`. It's one
  of the few models on Groq with native strict structured-output support,
  which the app relies on for a parseable `risk_level` / `escalate` /
  `message` reply - check
  https://console.groq.com/docs/structured-outputs before switching models,
  and https://console.groq.com/docs/rate-limits for current free-tier
  limits per model.

When a rate limit or a slow/unavailable model is hit, the chat shows a
readable "try again" message instead of an error.

Run the API:

```bash
uvicorn app.main:app --reload
```

Docs at `http://127.0.0.1:8000/docs`.

## Try it

```bash
curl -X POST http://127.0.0.1:8000/onboarding \
  -H "Content-Type: application/json" \
  -d '{
        "worker_id": "worker-1",
        "work_type": "construction - rebar",
        "work_start": "07:00",
        "work_end": "16:00",
        "location": "Chennai"
      }'

curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"worker_id": "worker-1", "message": "Checking in for today"}'
```

Or run the full scenario battery:

```bash
python scripts/run_scenarios.py
```

## Tests

```bash
pytest
```

`test_chat.py` monkeypatches the weather and LLM calls, so tests run
without real API keys or network access.

## Notes / current scope

- Worker profiles and conversation history are in-memory only (reset on
  restart) — deliberate, for fast iteration overnight. Swap
  `app/services/store.py` for real persistence later without touching
  routes.
- `worker_id` is any string the client sends at onboarding (name, phone,
  employee code) — no auth, this is a hackathon demo.
- Weather comes from Open-Meteo (free, no API key): geocoding + current
  conditions, cached per location per day.
