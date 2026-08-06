# Shared Runtime (multi-computer job history)

Lets two independent local clones of this repo (e.g. a desktop and a
laptop) see the same completed-job history, approved outputs, and
productivity metrics, through a synced folder such as OneDrive.

**Source code is never involved.** This is a runtime-data feature only —
each machine keeps its own Git clone, its own `.venv`, and does its own
processing entirely locally. Nothing here changes what gets committed to
either repository.

## What is / isn't synchronized

**Shared (published after a job succeeds):**
- The job's own manifest (for the Mockup Generator: its real
  `manifest.json`, including `run_succeeded`)
- A small `status.json` (completion time, engine, which machine
  published it)
- The final approved asset files
- A representative preview image
- One immutable metrics record per job (see below)

**Never shared — stays local only:**
- Active/in-progress working folders
- Extracted ZIP contents, caches, `.venv`, model binaries
- Failed or partial runs (a run whose manifest reports
  `run_succeeded: false` is never published)
- Anything not explicitly listed above (source input ZIPs, intermediate
  diagnostics, etc.)

The live working directory a generator writes to is **never** itself a
synced folder — publishing is always a deliberate copy of already-
finished output into the shared root, one job at a time.

## Configuring `VILICITY_RUNTIME_ROOT`

One environment variable, checked once per resolution:

```
VILICITY_RUNTIME_ROOT = <a folder path>
```

If unset (or blank), the Controller uses a safe local-only default —
`automation-controller/var/vilicity_runtime/` — which behaves exactly
like a single-machine setup and is never synced anywhere.
`var/` is already covered by this repo's `.gitignore`.

**Desktop and laptop setup (independently, one time each):** point both
machines at the *same* OneDrive-backed folder.

Windows (PowerShell), persists across terminal sessions:
```powershell
setx VILICITY_RUNTIME_ROOT "$env:USERPROFILE\OneDrive\Vilicity Runtime"
```

Anywhere else (`.env`, a shell profile, etc.), the value is just a path —
e.g. `~/OneDrive/Vilicity Runtime` (a leading `~` is expanded).

Optional: `VILICITY_MACHINE_ID` overrides the OS hostname used to keep
this machine's published job identifiers distinct from another machine's
(see Deduplication below). Only needed if you want a friendlier/more
stable name than the computer's hostname.

Verify either machine's current configuration:
```
GET /runtime/config
-> {"runtime_root": "...", "configured": true, "machine_id": "..."}
```

## Shared folder structure

```
<runtime root>/
├── jobs/<engine_id>/<job_key>/
│     manifest.json   -- the engine's own manifest, copied verbatim
│     status.json     -- job_id, engine_id, completed_at, run_succeeded, published_by_machine
│     COMPLETE        -- written LAST; a directory without this is not yet fully published
├── approved-assets/<engine_id>/<job_key>/   -- the final generated files
├── previews/<engine_id>/<job_key>/          -- one representative preview image
└── metrics/<engine_id>/<job_key>.json       -- one immutable record (see below)
```

Every job gets its own directory named `<job_key>`, so multiple generator
types, and multiple jobs from the same generator, never collide.

## How a job gets published

Publishing is **explicit and on-demand** (not automatic on every job
completion, and not something a generator does itself):

```
POST /runtime/jobs/{job_id}/publish
```

This is deliberately the least-coupled of the options considered: the
Controller reads a Job's already-computed `result_summary` and copies
already-finished files the generator already wrote locally — no
generator is changed, and no generator has any awareness that OneDrive
exists.

**Atomic publish**, so a shared job is either fully there or not visible
at all — never half-copied:
1. Copy everything into a temp directory (`<job_key>.tmp-<random>`)
2. Copy assets, write the preview, write the metrics record
3. Verify every expected file actually landed at its destination (right
   name, right size)
4. Rename the temp directory to its final name and write `COMPLETE`
   inside it **last**

A reader only ever trusts a `jobs/<engine_id>/<job_key>/` directory that
contains `COMPLETE`. Anything else (no marker yet, a `.tmp-*` name) is
treated as "not published yet," never as broken.

**Currently wired up for the Mockup Generator only** — it already has a
versioned, engine-owned `manifest.json` with a `run_succeeded` flag,
which is exactly the authoritative completion signal this feature needs.
Adding the AI Image Generator or Video Generator later is a mechanical
repeat of the same one-function pattern (see `_EXTRACTORS` in
`infra/runtime_publisher.py`), deliberately not done in this pass.

## Deduplication and conflicts

A generator's own run identifier (e.g. the Mockup Generator's `run_id`,
`run-2026-07-27-001`) is only unique *per machine, per day* — two
computers can independently produce the identical one. The shared
`job_key` is therefore `<run_id>__<machine_id>`, so two machines never
collide on the same key.

- Publishing the same `job_key` again with **identical** content →
  reported as `already_published`, nothing is re-copied.
- Publishing the same `job_key` with **different** content (a genuine
  conflict) → the original is left completely untouched; the new attempt
  is written to a separate `<job_key>__conflict-<random>` directory so a
  human can reconcile both, rather than either being silently discarded.

## Metrics

One append-only JSON record per completed job (never a single mutable
counter):

```json
{
  "schema_version": 1,
  "job_id": "run-2026-07-27-001__desktop",
  "module": "etsy-mockup-generator",
  "completed_at": "2026-07-27T18:04:12+00:00",
  "run_succeeded": true,
  "assets_generated": 20,
  "automation_seconds": 240.0,
  "estimated_manual_minutes": 64.0,
  "estimated_minutes_saved": 5
}
```

`GET /runtime/metrics` re-derives totals fresh from every record under
`metrics/` every time it's called — never from a stored running total —
deduplicated by each record's own `job_id` field. Two machines publishing
into the same root, or the same job being visible from both, always
converges on the same numbers with no double-counting.

This is intentionally separate from the existing single-machine
`GET /metrics/lifetime` (backed by a local SQLite ledger, see
`infra/lifetime_metrics.py`) — that one keeps working exactly as before,
unaffected by any of this.

## If OneDrive is offline or still syncing

Every read (`GET /runtime/jobs`, `GET /runtime/metrics`) tolerates a
missing root, an empty root, and partially-synced files:
- A missing/unreachable root → empty results, not an error.
- A job directory without `COMPLETE` → skipped, not reported as broken.
- A zero-byte or unparseable JSON file (the classic "OneDrive placeholder
  hasn't finished downloading" state) → skipped, not crashed on.

## Privacy / what never reaches GitHub

The shared runtime root — wherever `VILICITY_RUNTIME_ROOT` points, and
the local `var/vilicity_runtime/` default — is **never** part of either
Git repository. `var/` is already excluded by this repo's `.gitignore`
before this feature existed; a `VILICITY_RUNTIME_ROOT` pointed outside
the repo (the real-world OneDrive case) is naturally outside Git's
working tree entirely. No production asset, job history, or metrics
record from this feature is ever committed.
