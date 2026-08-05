# Automation Controller — Web UI

React + TypeScript + Vite. The first client of the Controller Core (design
spec Section 1, "Guiding principles" #4): talks to the Controller
exclusively through the REST API defined in `api/main.py`, never by
importing anything from `core/`/`infra/` directly. No business logic
(status transitions, eligibility, scheduling) is duplicated here — every
piece of derived state (job progress, engine health, queue-wait reason)
is read from the API exactly as the backend computed it.

Run `npm install` then `npm run dev` (expects the Controller API running
at `http://127.0.0.1:8123` — override with `VITE_API_BASE_URL`).

See `src/api/` for the typed REST client (mirrors `api/schemas.py` and the
`core/domain/*` response shapes 1:1 — no parallel frontend models).
