# infra/ tests

Repository + adapter tests. SQLite repository tests exercise the real
`infra/db/sqlite_repositories.py` classes against a real (temp-file or
in-memory) SQLite database. Adapter tests exercise
`infra/adapters/{video_generator,image_generator,mockup_generator}/adapter.py`
against the real sibling engine repositories where feasible — see the
per-engine adapter docstrings for which methods are real vs. stubbed in V1.
