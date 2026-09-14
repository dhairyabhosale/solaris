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
    ├── llm.py                     prompt construction + Gemini call
    ├── prism.py                    routes every LLM call through PRISM tracing
    └── store.py                     in-memory worker profiles + conversation history
scripts/
└── run_scenarios.py         repeatable scenario battery for PRISM before/after comparisons
docs/
└── prism_evaluation.md      working log for the Observe/Improve/Prove evidence
tests/
    test_onboarding.py, test_chat.py
```

Every LLM call — onboarding-time or follow-up — goes through
`app/services/prism.py`. That's the one seam where tracing is wired in, and
where `session_id` / `agent_id` are attached, so switching PRISM on/off or
changing its proxy details never touches route or prompt code.

## PRISM integration

- `agent_id` is a hardcoded constant: `"solaris-heat-safety"`.
- `session_id` is `"<worker_id>:<YYYY-MM-DD>"` — stable per worker per day,
  so a whole day's conversation traces as one PRISM session.
- PRISM's zero-code proxy model works by pointing the LLM client at their
  proxy URL instead of calling Gemini directly; the proxy forwards the
  request and logs the exchange. Until we have Block Convey's exact proxy
  docs, `PRISM_ENABLED=false` in `.env` makes `prism.py` call Gemini
  directly, so the app runs standalone. Flip it to `true` and fill in
  `PRISM_PROXY_URL` / `PRISM_API_KEY` / `PRISM_PROJECT_ID` once we have
  them — nothing else in the app changes.
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
cp .env.example .env          # then fill in GEMINI_API_KEY (and PRISM_* once available)
```

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
