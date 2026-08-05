# core/ tests

Pure domain/service tests — no FastAPI, no SQLite. Per the design spec
Section 7, dependencies here should be satisfied with fakes/in-memory
repository implementations (e.g. a dict-backed `JobRepository`), never the
real SQLite classes in `infra/db/`. This is what makes it possible to
test the Execution Coordinator's reactive event-handling logic (see
`core/execution/coordinator.py`) by publishing an event and asserting on
which adapter method was called, with no HTTP or DB involved.
