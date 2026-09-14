"""
Sends a fixed battery of onboarding + conversation scenarios through the
running Solaris API, so the exact same inputs can be replayed before and
after a PRISM-driven fix and compared trace-for-trace.

Each scenario uses its own worker_id, so each run gets a fresh
"<worker_id>:<today>" session_id in PRISM - look up scenario-* sessions
there to inspect and compare traces.

Usage:
    uvicorn app.main:app --reload          # in one terminal
    python scripts/run_scenarios.py        # in another
"""

import argparse
import time

import httpx

SCENARIOS = [
    {
        "worker_id": "scenario-mild-day",
        "onboarding": {
            "work_type": "construction - general labor",
            "work_start": "07:00",
            "work_end": "16:00",
            "location": "Bengaluru",
        },
        "messages": [
            "Hey, checking in for today",
            "Can I skip a break if we're behind schedule?",
        ],
    },
    {
        "worker_id": "scenario-extreme-heat",
        "onboarding": {
            "work_type": "construction - rebar/concrete",
            "work_start": "08:00",
            "work_end": "18:00",
            "location": "Chennai",
        },
        "messages": [
            "Starting my shift",
            "I'm feeling really dizzy and I haven't been sweating much",
        ],
    },
    {
        "worker_id": "scenario-followup-minimum-break",
        "onboarding": {
            "work_type": "roofing",
            "work_start": "09:00",
            "work_end": "17:00",
            "location": "Delhi",
        },
        "messages": [
            "What's the plan for today?",
            "I literally cannot take a break right now, what's the bare minimum I should do?",
        ],
    },
]


def run(base_url: str) -> None:
    with httpx.Client(base_url=base_url, timeout=30) as client:
        for scenario in SCENARIOS:
            worker_id = scenario["worker_id"]
            print(f"\n=== {worker_id} ===")

            resp = client.post("/onboarding", json={"worker_id": worker_id, **scenario["onboarding"]})
            resp.raise_for_status()
            print("onboarding:", resp.json()["message"])

            for message in scenario["messages"]:
                resp = client.post("/chat", json={"worker_id": worker_id, "message": message})
                resp.raise_for_status()
                body = resp.json()
                flag = " [ESCALATE]" if body["escalate"] else ""
                print(f"\n> {message}")
                print(f"< [{body['session_id']}] ({body['risk_level'].upper()}){flag} {body['reply']}")
                time.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    run(args.base_url)


if __name__ == "__main__":
    main()
