"""The flat image view "Open Latest AI Image Outputs" opens.

Covers the two properties the feature exists for -- the folder contains
every image and nothing else -- plus the two that keep it honest: it must
notice when the approved set changes, and it must not re-copy megabytes
when it hasn't.

These run against a temporary STAGING_ROOT, never the real one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.storage import ai_image_flat_view as flat


@pytest.fixture(autouse=True)
def _isolated_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flat, "STAGING_ROOT", tmp_path / "staging")


def _concept(root: Path, name: str, image_bytes: bytes = b"\x89PNG-a") -> Path:
    """One approved concept as image_review.py writes it: a single image
    outnumbered by its own metadata."""
    pkg = root / name
    pkg.mkdir(parents=True)
    (pkg / "image_001.png").write_bytes(image_bytes)
    (pkg / "generation_metadata.json").write_text("{}", encoding="utf-8")
    (pkg / "prompt_package_snapshot.json").write_text("{}", encoding="utf-8")
    return pkg


class TestContents:
    def test_gathers_every_image_from_every_concept(self, tmp_path: Path) -> None:
        source = tmp_path / "ai_product_mockups"
        for i in range(1, 6):
            _concept(source, f"imported_00{i}")

        view = flat.build_flat_image_view("dragon", source)

        assert len(list(view.glob("*.png"))) == 5

    def test_contains_no_metadata(self, tmp_path: Path) -> None:
        """The entire point: an operator opening this must see images only."""
        source = tmp_path / "ai_product_mockups"
        _concept(source, "imported_001")
        _concept(source, "imported_002")

        view = flat.build_flat_image_view("dragon", source)

        assert [e.name for e in view.iterdir() if e.suffix != ".png"] == []

    def test_names_images_by_concept_so_they_do_not_collide(self, tmp_path: Path) -> None:
        """Every package calls its image image_001.png, so flattening on the
        original filename would collide on the first pair and silently lose
        images. Naming by concept also tells the operator what they're
        looking at."""
        source = tmp_path / "ai_product_mockups"
        _concept(source, "imported_001", b"first")
        _concept(source, "imported_002", b"second")

        view = flat.build_flat_image_view("dragon", source)

        assert sorted(p.name for p in view.iterdir()) == ["imported_001.png", "imported_002.png"]
        assert {p.read_bytes() for p in view.iterdir()} == {b"first", b"second"}

    def test_handles_several_images_in_one_concept(self, tmp_path: Path) -> None:
        source = tmp_path / "cat"
        pkg = _concept(source, "ai_01")
        (pkg / "image_002.png").write_bytes(b"second")

        view = flat.build_flat_image_view("job", source)

        assert len(list(view.glob("*.png"))) == 2

    def test_ignores_non_image_files_entirely(self, tmp_path: Path) -> None:
        source = tmp_path / "cat"
        pkg = _concept(source, "ai_01")
        (pkg / "system_prompt_snapshot.txt").write_text("x", encoding="utf-8")

        view = flat.build_flat_image_view("job", source)

        assert [p.name for p in view.iterdir()] == ["ai_01.png"]

    def test_manifest_is_a_sibling_never_inside_the_folder(self, tmp_path: Path) -> None:
        """Putting the fingerprint inside would reintroduce exactly the
        stray-JSON problem this change removes."""
        source = tmp_path / "cat"
        _concept(source, "ai_01")

        view = flat.build_flat_image_view("dragon", source)

        assert not any(p.suffix == ".json" for p in view.iterdir())
        assert (view.parent / "dragon.manifest.json").is_file()


class TestFreshness:
    def test_rebuilds_when_an_image_changes(self, tmp_path: Path) -> None:
        source = tmp_path / "cat"
        pkg = _concept(source, "ai_01", b"original")
        view = flat.build_flat_image_view("job", source)
        assert (view / "ai_01.png").read_bytes() == b"original"

        (pkg / "image_001.png").write_bytes(b"regenerated-larger")
        view = flat.build_flat_image_view("job", source)

        assert (view / "ai_01.png").read_bytes() == b"regenerated-larger"

    def test_drops_an_image_that_was_un_approved(self, tmp_path: Path) -> None:
        """A copy of something no longer approved is a silent production
        error -- the wrong image dragged into a listing."""
        source = tmp_path / "cat"
        _concept(source, "ai_01")
        rejected = _concept(source, "ai_02")
        flat.build_flat_image_view("job", source)

        import shutil as _shutil

        _shutil.rmtree(rejected)
        view = flat.build_flat_image_view("job", source)

        assert sorted(p.name for p in view.iterdir()) == ["ai_01.png"]

    def test_picks_up_a_newly_approved_image(self, tmp_path: Path) -> None:
        source = tmp_path / "cat"
        _concept(source, "ai_01")
        flat.build_flat_image_view("job", source)

        _concept(source, "ai_02")
        view = flat.build_flat_image_view("job", source)

        assert len(list(view.glob("*.png"))) == 2


class TestCaching:
    def test_unchanged_source_reuses_the_folder_without_recopying(self, tmp_path: Path) -> None:
        """On exFAT every entry is a real copy, so an unnecessary rebuild
        costs tens of megabytes for a folder that did not change."""
        source = tmp_path / "cat"
        _concept(source, "ai_01")

        view = flat.build_flat_image_view("job", source)
        first = (view / "ai_01.png").stat().st_mtime_ns

        view = flat.build_flat_image_view("job", source)

        assert (view / "ai_01.png").stat().st_mtime_ns == first

    def test_corrupt_manifest_rebuilds_rather_than_failing(self, tmp_path: Path) -> None:
        source = tmp_path / "cat"
        _concept(source, "ai_01")
        view = flat.build_flat_image_view("job", source)
        (view.parent / "job.manifest.json").write_text("not json", encoding="utf-8")

        view = flat.build_flat_image_view("job", source)

        assert (view / "ai_01.png").is_file()
        assert json.loads((view.parent / "job.manifest.json").read_text(encoding="utf-8"))


class TestSafety:
    def test_never_modifies_the_source(self, tmp_path: Path) -> None:
        source = tmp_path / "cat"
        pkg = _concept(source, "ai_01")
        before = sorted(p.name for p in pkg.iterdir())

        flat.build_flat_image_view("job", source)

        assert sorted(p.name for p in pkg.iterdir()) == before
        assert (pkg / "image_001.png").is_file()

    def test_job_name_cannot_escape_the_staging_root(self, tmp_path: Path) -> None:
        """This path is used to build a directory that gets rmtree'd."""
        source = tmp_path / "cat"
        _concept(source, "ai_01")

        view = flat.build_flat_image_view("../../etc/evil", source)

        assert flat.STAGING_ROOT.resolve() in view.resolve().parents

    def test_empty_source_yields_an_empty_folder_not_an_error(self, tmp_path: Path) -> None:
        source = tmp_path / "cat"
        source.mkdir()

        view = flat.build_flat_image_view("job", source)

        assert view.is_dir()
        assert list(view.iterdir()) == []
