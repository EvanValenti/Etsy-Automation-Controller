"""Pydantic request/response schemas for the REST + WebSocket API surface.

Design spec Section 5. These are transport-layer shapes only — they
translate to/from core/domain/*.py entities in api/main.py's route
handlers; core/ itself has no dependency on Pydantic or FastAPI.

Step 4 (Job domain): CreateJobRequest is the first real request body this
project defines. Responses continue to be the raw core/domain dataclasses
(Job, Engine, ...) returned directly from route handlers — FastAPI's
jsonable_encoder already serializes them correctly (confirmed in Step 1),
so no separate response schema class is needed yet.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateJobRequest(BaseModel):
    """Body for POST /jobs. `config` is opaque JSON as far as this schema
    and the Core are concerned — only the resolved EngineAdapter's
    validate() interprets its contents.
    """

    engine_id: str
    config: dict[str, Any] = Field(default_factory=dict)
    pipeline_run_id: str | None = None
