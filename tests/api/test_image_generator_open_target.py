"""Which folder "Open Latest AI Image Outputs" lands the operator in.

Regression cover for a live bug: the button opened
`outputs/approved/ai_product_mockups/imported_005/` -- one concept package
holding a single image plus `generation_metadata.json` and
`prompt_package_snapshot.json`. That is the wrong depth (the job's other
approved images sit one level up, invisible) and the wrong content (JSON as
the primary thing on screen). Picking 3-5 images for a video meant walking
up and back down once per image.

These tests pin the two shapes the resolver has to tell apart, because they
are genuinely different on disk:

  AI Image Generator   outputs/approved/<category>/<concept>/image_001.png
                       -- one image per concept package, written by the
                       engine's image_review.py, each beside its metadata.

  Mockup Generator     output/run-<date>/assets/*.png
                       -- every deliverable flat in one folder, with
                       manifest.json deliberately one level up.

The rule is the same for both and stated in terms of content, not layout:
land on the folder an operator selects images FROM, and never on one whose
contents are mostly developer metadata.
"""

from pathlib import Path

from api.image_generator_routes import _descend_to_image_folder, _is_assets_folder


def _concept_package(root: Path, name: str, image: str = "image_001.png") -> Path:
    """One approved concept as the engine actually writes it: a single
    image outnumbered by its own metadata."""
    pkg = root / name
    pkg.mkdir(parents=True)
    (pkg / image).write_bytes(b"\x89PNG")
    (pkg / "generation_metadata.json").write_text("{}", encoding="utf-8")
    (pkg / "prompt_package_snapshot.json").write_text("{}", encoding="utf-8")
    return pkg


class TestAiImageShape:
    def test_stops_at_collection_not_inside_a_concept_package(self, tmp_path: Path) -> None:
        """The bug. Descending picked one concept by mtime and hid the rest."""
        category = tmp_path / "approved" / "ai_product_mockups"
        for i in (1, 2, 3):
            _concept_package(category, f"imported_00{i}")

        assert _descend_to_image_folder(tmp_path / "approved") == category

    def test_result_exposes_no_metadata_files(self, tmp_path: Path) -> None:
        """"Never expose metadata folders": whatever we open, the files
        directly visible in it must not be developer material."""
        category = tmp_path / "approved" / "ai_product_mockups"
        for i in (1, 2):
            _concept_package(category, f"imported_00{i}")

        landed = _descend_to_image_folder(tmp_path / "approved")
        visible_files = [e.name for e in landed.iterdir() if e.is_file()]
        assert visible_files == []

    def test_every_approved_image_is_one_level_below_the_result(self, tmp_path: Path) -> None:
        """All five approved images stay reachable, not just the newest."""
        category = tmp_path / "approved" / "ai_product_mockups"
        for i in range(1, 6):
            _concept_package(category, f"imported_00{i}")

        landed = _descend_to_image_folder(tmp_path / "approved")
        assert len(list(landed.glob("*/*.png"))) == 5

    def test_single_concept_job_still_does_not_open_the_package(self, tmp_path: Path) -> None:
        """A job with one approved concept is the tempting case to descend
        into -- all its images ARE in there. It still must not, because the
        package is mostly metadata."""
        category = tmp_path / "approved" / "ai_product_mockups"
        pkg = _concept_package(category, "ai_01")
        (pkg / "system_prompt_snapshot.txt").write_text("x", encoding="utf-8")
        (pkg / "user_prompt_snapshot.txt").write_text("x", encoding="utf-8")

        assert _descend_to_image_folder(tmp_path / "approved") == category


class TestMockupShape:
    def test_descends_into_a_flat_assets_folder(self, tmp_path: Path) -> None:
        """The behaviour the AI Image button is being matched to: a run root
        holding assets/ next to manifest.json opens assets/, not the root."""
        run = tmp_path / "run-2026-07-28-001"
        assets = run / "assets"
        assets.mkdir(parents=True)
        for i in range(19):
            (assets / f"back-{i:02d}.png").write_bytes(b"\x89PNG")
        (run / "manifest.json").write_text("{}", encoding="utf-8")

        assert _descend_to_image_folder(run) == assets

    def test_an_assets_folder_is_returned_unchanged(self, tmp_path: Path) -> None:
        assets = tmp_path / "assets"
        assets.mkdir()
        for i in range(3):
            (assets / f"{i}.png").write_bytes(b"\x89PNG")

        assert _descend_to_image_folder(assets) == assets


class TestIsAssetsFolder:
    def test_images_must_outnumber_everything_else(self, tmp_path: Path) -> None:
        """The discriminator between the two shapes. Both contain images, so
        presence cannot separate them -- proportion can."""
        package = _concept_package(tmp_path, "imported_001")  # 1 image, 2 JSON
        assert not _is_assets_folder(package)

        deliverables = tmp_path / "assets"
        deliverables.mkdir()
        for i in range(4):
            (deliverables / f"{i}.png").write_bytes(b"\x89PNG")
        (deliverables / "notes.json").write_text("{}", encoding="utf-8")
        assert _is_assets_folder(deliverables)

    def test_extension_matching_is_case_insensitive(self, tmp_path: Path) -> None:
        folder = tmp_path / "assets"
        folder.mkdir()
        (folder / "a.PNG").write_bytes(b"\x89PNG")
        (folder / "b.JPEG").write_bytes(b"\xff\xd8")
        assert _is_assets_folder(folder)

    def test_empty_folder_is_not_an_assets_folder(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        assert not _is_assets_folder(empty)


class TestDegradesSafely:
    def test_no_images_anywhere_returns_a_folder_rather_than_raising(self, tmp_path: Path) -> None:
        """Landing a level too high beats an error dialog."""
        chain = tmp_path / "a" / "b" / "c"
        chain.mkdir(parents=True)
        assert _descend_to_image_folder(tmp_path) == chain

    def test_metadata_only_tree_never_reports_it_found_images(self, tmp_path: Path) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "notes.json").write_text("{}", encoding="utf-8")
        assert not _is_assets_folder(_descend_to_image_folder(tmp_path))

    def test_depth_is_bounded(self, tmp_path: Path) -> None:
        """An unexpected layout must terminate, not loop."""
        deep = tmp_path
        for i in range(30):
            deep = deep / f"level_{i}"
        deep.mkdir(parents=True)
        assert _descend_to_image_folder(tmp_path).is_dir()
