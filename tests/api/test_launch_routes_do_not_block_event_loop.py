"""Launch routes must not run on the asyncio event loop.

The bug this locks out, measured on a real render before it was fixed:

    t=0.9s   GET /health ->    0ms   (idle)
    t=1.0s   POST /video-generator/jobs begins (11.8s render)
    t=1.4s   GET /health -> TIMED OUT after the client's full 8s
    t=9.7s   GET /health -> 3047ms
    t=12.8s  render ends
    t=13.0s  GET /health ->   16ms

Nothing was wrong with the job -- it succeeded, and its page loaded fine
afterwards. The problem was purely that `launch_video_job` was an
`async def` calling ExecutionCoordinator.evaluate(), which blocks for the
entire FFmpeg run. FastAPI runs a sync `def` handler in a threadpool, but
an `async def` handler runs ON the event loop, so that one blocking call
starved every other request -- /health included -- and the Web UI reported
"Request timed out" / "API Unreachable" during a perfectly healthy job.

The fix is simply that these handlers are sync. That is easy to undo by
accident (adding `async` to a handler looks like an improvement), so it is
asserted here rather than left to a comment.
"""

from __future__ import annotations

import ast
import inspect
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.image_generator_routes import create_job as image_create_job  # noqa: E402
from api.main import create_job as generic_create_job  # noqa: E402
from api.mockup_generator_routes import launch_batch_job, launch_preview_job  # noqa: E402
from api.video_generator_routes import launch_video_job  # noqa: E402

# Every handler that calls ExecutionCoordinator.evaluate(), i.e. every
# handler that can block for the length of a real engine run.
BLOCKING_LAUNCH_HANDLERS = [
    pytest.param(launch_video_job, id="video-generator/jobs"),
    pytest.param(launch_preview_job, id="mockup-generator/jobs/preview"),
    pytest.param(launch_batch_job, id="mockup-generator/jobs/{id}/batch"),
    pytest.param(image_create_job, id="image-generator/jobs"),
    pytest.param(generic_create_job, id="POST /jobs"),
]


@pytest.mark.parametrize("handler", BLOCKING_LAUNCH_HANDLERS)
def test_launch_handler_is_sync_so_fastapi_runs_it_in_a_threadpool(handler):
    assert not inspect.iscoroutinefunction(handler), (
        f"{handler.__name__} is `async def`, so FastAPI will run it on the event loop. "
        "It calls ExecutionCoordinator.evaluate(), which blocks for the whole engine run "
        "and will freeze every concurrent request (including /health) for its duration. "
        "Make it a sync `def` and read uploads with `upload.file.read()`."
    )


@pytest.mark.parametrize("handler", BLOCKING_LAUNCH_HANDLERS)
def test_launch_handler_contains_no_await_expression(handler):
    """An awaited upload read is the thing that tempts a handler back into
    `async def`; the sync `.file.read()` on the same SpooledTemporaryFile
    returns the identical bytes from the threadpool thread.

    Parsed rather than string-matched: a substring search also hits the
    word inside comments explaining the fix, which is how this test first
    failed against correct code.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(handler)))
    awaits = [node for node in ast.walk(tree) if isinstance(node, ast.Await)]

    assert not awaits, (
        f"{handler.__name__} contains an `await` expression, which forces it to be `async def`. "
        "Use the sync equivalent (e.g. `upload.file.read()` instead of an awaited `upload.read()`)."
    )
