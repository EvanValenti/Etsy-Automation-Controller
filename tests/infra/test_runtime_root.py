"""Tests for infra/runtime_root.py -- the one configuration mechanism
(VILICITY_RUNTIME_ROOT) and the safe local default when it's absent.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from infra.runtime_root import is_using_configured_root, machine_id, resolve_runtime_root  # noqa: E402


class TestResolveRuntimeRoot:
    def test_defaults_to_a_local_var_folder_when_unset(self, monkeypatch):
        monkeypatch.delenv("VILICITY_RUNTIME_ROOT", raising=False)

        root = resolve_runtime_root()

        assert root.name == "vilicity_runtime"
        assert root.parent.name == "var"
        assert is_using_configured_root() is False

    def test_uses_the_env_var_verbatim_when_set(self, monkeypatch, tmp_path):
        configured = tmp_path / "OneDrive" / "Vilicity Runtime"
        monkeypatch.setenv("VILICITY_RUNTIME_ROOT", str(configured))

        assert resolve_runtime_root() == configured
        assert is_using_configured_root() is True

    def test_expands_a_leading_tilde(self, monkeypatch):
        monkeypatch.setenv("VILICITY_RUNTIME_ROOT", "~/OneDrive/Vilicity Runtime")

        root = resolve_runtime_root()

        assert "~" not in str(root)
        assert str(root).endswith("Vilicity Runtime")

    def test_blank_env_var_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("VILICITY_RUNTIME_ROOT", "   ")

        assert is_using_configured_root() is False
        assert resolve_runtime_root().name == "vilicity_runtime"


class TestMachineId:
    def test_env_override_wins_over_hostname(self, monkeypatch):
        monkeypatch.setenv("VILICITY_MACHINE_ID", "my-laptop")
        assert machine_id() == "my-laptop"

    def test_unsafe_characters_are_sanitized(self, monkeypatch):
        monkeypatch.setenv("VILICITY_MACHINE_ID", "Alex's Desktop (Office)!!")
        result = machine_id()
        assert result == "Alex-s-Desktop-Office"

    def test_falls_back_to_hostname_when_unset(self, monkeypatch):
        monkeypatch.delenv("VILICITY_MACHINE_ID", raising=False)
        result = machine_id()
        assert result  # some non-empty, filesystem-safe value
        assert all(c.isalnum() or c in "_-" for c in result)
