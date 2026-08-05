"""Delivery layer (FastAPI) — thin: request/response mapping, auth, validation.

Design spec Section 1: "FastAPI is an outer delivery shell around
Application Services, not a home for orchestration logic." No route
handler in this package should contain business logic beyond calling into
core/services/ and core/execution/ and mapping the result to a response
schema.
"""
