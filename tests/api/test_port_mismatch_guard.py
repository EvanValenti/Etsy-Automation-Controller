"""Tests for the startup port-mismatch warning.

Why this exists: `uvicorn api.main:app` -- the obvious short start command --
binds uvicorn's own default port 8000, while the Web UI calls 8123. The
backend then starts cleanly, prints "Uvicorn running on
http://127.0.0.1:8000", never raises, and never dies, while the frontend
reports "Backend not responding". That combination reads as backend
instability, and it recurred repeatedly even though README.md documented
the correct command and warned about this precise trap.

So the check is code now, not prose. These tests pin the invocations it
must catch and, just as importantly, the ones it must stay quiet about --
a warning that cried wolf on a correct start command would be worse than
none.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.main import CANONICAL_API_PORT, describe_port_mismatch  # noqa: E402

UVICORN_EXE = r"E:\Vilicity\automation-controller\.venv\Scripts\uvicorn.exe"


class TestWarnsOnTheInvocationsThatBreakTheWebUI:
    def test_uvicorn_cli_with_no_port_flag(self):
        """The exact command that caused this investigation."""
        warning = describe_port_mismatch([UVICORN_EXE, "api.main:app"])

        assert warning is not None
        assert "8000" in warning
        assert str(CANONICAL_API_PORT) in warning

    def test_uvicorn_cli_with_a_different_explicit_port(self):
        warning = describe_port_mismatch([UVICORN_EXE, "api.main:app", "--port", "9000"])

        assert warning is not None
        assert "9000" in warning

    def test_equals_form_of_the_port_flag(self):
        warning = describe_port_mismatch([UVICORN_EXE, "api.main:app", "--port=9000"])

        assert warning is not None
        assert "9000" in warning

    def test_a_non_numeric_port_is_treated_as_unknown_not_crashed_on(self):
        warning = describe_port_mismatch([UVICORN_EXE, "api.main:app", "--port", "not-a-number"])

        assert warning is not None

    def test_the_warning_names_a_command_that_fixes_it(self):
        """An operator reading this in a terminal needs the next action, not
        just the diagnosis."""
        warning = describe_port_mismatch([UVICORN_EXE, "api.main:app"])

        assert "python -m api.main" in warning


class TestStaysQuietWhenTheInvocationIsCorrect:
    def test_uvicorn_cli_with_the_canonical_port(self):
        argv = [UVICORN_EXE, "api.main:app", "--app-dir", ".", "--host", "127.0.0.1", "--port", str(CANONICAL_API_PORT)]

        assert describe_port_mismatch(argv) is None

    def test_equals_form_of_the_canonical_port(self):
        assert describe_port_mismatch([UVICORN_EXE, "api.main:app", f"--port={CANONICAL_API_PORT}"]) is None

    def test_python_m_api_main_pins_the_port_itself(self):
        """main() chooses the port, so argv carries none and none is needed."""
        assert describe_port_mismatch([r"E:\Vilicity\automation-controller\.venv\Scripts\python.exe", "-m", "api.main"]) is None

    def test_a_test_runner_is_not_a_server_invocation(self):
        """pytest imports this module; it must not log a port warning."""
        assert describe_port_mismatch(["pytest", "tests/api"]) is None
        assert describe_port_mismatch([r"C:\python.exe", "-m", "pytest", "-q"]) is None


@pytest.mark.parametrize(
    "argv",
    [
        ["uvicorn", "api.main:app"],
        ["uvicorn.exe", "api.main:app"],
        [r"C:\venv\Scripts\uvicorn.exe", "api.main:app", "--reload"],
    ],
)
def test_recognizes_the_uvicorn_cli_however_it_is_spelled(argv):
    assert describe_port_mismatch(argv) is not None
