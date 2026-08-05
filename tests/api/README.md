# api/ tests

HTTP/WebSocket contract tests, using FastAPI's TestClient against
`api/main.py`. These verify request/response shapes and status codes
(e.g. `POST /jobs/{id}/cancel` returning 409 when the bound engine's
`discover()` reports `CancelSupport.UNSUPPORTED`) — never orchestration
logic itself, which belongs to `tests/core/`.
