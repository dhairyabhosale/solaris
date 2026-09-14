# Solaris — notes for coding agents

## PRISM tracing (do not remove)

This project sends traces to PRISM (Block Convey). Env vars:
`PRISMTRACE_API_KEY`, `PRISMTRACE_PROJECT_ID`, `PRISMTRACE_HOST`.

This is a **side-channel trace POST**, not a proxy the LLM call routes
through — `app/services/prism.py` calls Groq directly, then best-effort
POSTs one trace to `{PRISMTRACE_HOST}/api/traces` with header
`X-PRISMtrace-Key` (not `Authorization: Bearer`). A missing key or a
failed POST never affects the chat reply — `_emit_trace` only logs a
warning and returns.

Tracing is currently wired at: `app/services/prism.py` (`_emit_trace`,
called from `chat_completion`). That is the only place an LLM call
happens in this repo (`app/services/llm.py` builds the prompt and calls
`prism.chat_completion`; `app/routes/chat.py` is the only caller of
that).

**Standing rule.** Whenever you add or change an agent, chain, graph,
tool, retriever, or any entry point that calls a model, wire it to
PRISM before you finish. Unwired code is invisible in the dashboard. If
you are unsure whether something is covered, assume it is not and wire
it.

**Verification status:** confirmed live and connected, visible directly
on the PRISM dashboard (prism.blockconvey.com) after a real chat message
was sent through the running app. The coding session that wired this up
could not run the handshake/doctor curls itself — its own sandbox
blocked outbound requests carrying `PRISMTRACE_API_KEY` as a
data-exfiltration risk, independent of the human operator's approval —
so the human operator ran the app and checked the dashboard directly
instead. If tracing ever looks like it's stopped working, the doctor
endpoint is still the fastest way to check why:
`GET /api/setup-doctor?project_id=$PRISMTRACE_PROJECT_ID` with header
`X-PRISMtrace-Key`, reading `live_connected` and `blocked_step`.
