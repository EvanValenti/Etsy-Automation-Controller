"""Controller-owned flat view of an AI Image Generator job's images.

Why this exists
---------------
The two engines write their deliverables in genuinely different shapes:

    Mockup Generator    output/run-<date>/assets/*.png
                        every deliverable flat in one folder, manifest.json
                        deliberately one level up.

    AI Image Generator  outputs/approved/<category>/<concept>/image_001.png
                        one image per concept package, each sitting beside
                        its generation_metadata.json and
                        prompt_package_snapshot.json.

"Open Latest Mockup Outputs" therefore lands the operator on a grid of
selectable thumbnails, while no folder on disk exists that holds more than
one AI image. Opening the collection folder shows concept FOLDERS, so
picking 3-5 images for a video still means a round trip per image.

This module builds the folder that was missing: one flat directory of a
job's images, which "Open Latest AI Image Outputs" opens instead. The
result is a thumbnail grid the operator can rubber-band select and drag in
one gesture -- the same gesture the Mockup button already affords.

What it does NOT do
-------------------
It never writes into an engine repo, never modifies or deletes an original
image, and produces nothing the engine will ever read back. This is a
presentation convenience owned entirely by the Controller; deleting
STAGING_ROOT at any time is safe and costs only the next rebuild.

Hardlinks where possible, copies where not
------------------------------------------
Hardlinking (os.link) would be ideal: directory entries instead of
duplicated PNGs, and no way for a link to drift from the image it points
at, because there is only one set of bytes.

It is attempted first, but it is NOT what happens on this deployment. The
Vilicity repos live on E:, which is exFAT -- a filesystem with no hardlink
support at all, where os.link raises WinError 1 ("Incorrect function").
So on this machine every entry is a real copy, and that is the expected
path rather than a rare fallback. The link attempt is kept for repos that
do sit on NTFS, and the fallback is per-file so one unlinkable image never
costs the operator the rest of the set.

Because copies are the norm here, the view is CACHED rather than rebuilt
blindly -- see below. Rebuilding a 40-image job on every click would copy
tens of megabytes to show a folder that had not changed.

Freshness without churn
-----------------------
Staleness matters: approving or rejecting an image between two clicks
could otherwise leave a copy of something the operator no longer considers
approved, and a wrong image dragged into a listing is a silent production
error.

So the source set is fingerprinted (relative path, size, mtime) and the
fingerprint stored beside -- deliberately NOT inside -- the flat folder.
A click whose fingerprint matches reuses the folder untouched; any
difference rebuilds it from scratch. That keeps the folder correct while
copying only when something actually changed.

The fingerprint lives at `<job>.manifest.json`, a sibling of `<job>/`,
precisely so the folder the operator opens contains images and nothing
else. Putting it inside would reintroduce exactly the stray-JSON problem
this whole change exists to remove.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

# automation-controller/var/staging/ai_image_flat_view -- sibling of the
# other engines' staging roots and of automation_controller.db, never
# inside any engine repo. See video_generator_staging.STAGING_ROOT.
STAGING_ROOT = Path(__file__).resolve().parents[2] / "var" / "staging" / "ai_image_flat_view"

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"})

# job_name reaches us from a route parameter. The caller has already
# checked it resolves inside the engine's jobs/ directory, but this path is
# built independently and gets its own guard rather than trusting that one:
# a directory we are about to rmtree() should never be assembled from
# unsanitised input.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_dir_name(job_name: str) -> str:
    cleaned = _UNSAFE.sub("_", job_name).strip("._") or "job"
    return cleaned[:120]


def _link_or_copy(source: Path, destination: Path) -> None:
    """Hardlink `source` to `destination`, falling back to a copy.

    Falls back per-file rather than aborting: one image on a different
    volume shouldn't cost the operator the other nineteen.
    """
    try:
        os.link(source, destination)
    except (OSError, NotImplementedError, AttributeError):
        shutil.copy2(source, destination)


def _flat_name(image: Path, source_root: Path, used: set[str]) -> str:
    """Name this image by the concept it came from.

    Every concept package names its image `image_001.png`, so flattening
    on the original filenames would collide on the very first pair. Naming
    by concept folder instead -- imported_003.png, ai_01.png -- both avoids
    the collision and tells the operator which concept they are looking at,
    which the flat view would otherwise throw away.
    """
    relative = image.relative_to(source_root)
    concept = relative.parent.name if relative.parent != Path(".") else ""
    stem = concept or image.stem
    # A concept holding several images keeps them distinguishable.
    candidate = f"{stem}{image.suffix.lower()}"
    if candidate in used:
        candidate = f"{stem}_{image.stem}{image.suffix.lower()}"
    counter = 2
    while candidate in used:
        candidate = f"{stem}_{counter}{image.suffix.lower()}"
        counter += 1
    used.add(candidate)
    return candidate


def _fingerprint(images: list[Path], source_root: Path) -> list[list[object]]:
    """Identify the source set precisely enough to detect any change that
    should invalidate the view: a different image, a re-generated one, or
    one approved/rejected since the last click.

    Size plus mtime rather than a content hash -- hashing 40 multi-megabyte
    PNGs to decide whether to copy them would cost more than the copy.
    """
    entries: list[list[object]] = []
    for image in images:
        try:
            stat = image.stat()
        except OSError:
            continue
        entries.append([image.relative_to(source_root).as_posix(), stat.st_size, stat.st_mtime_ns])
    return entries


def _read_manifest(path: Path) -> list[list[object]] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Missing or corrupt reads as "rebuild", which is always safe.
        return None


def _write_manifest(path: Path, fingerprint: list[list[object]]) -> None:
    try:
        path.write_text(json.dumps(fingerprint), encoding="utf-8")
    except OSError:
        # Losing the manifest only costs an unnecessary rebuild next time.
        pass


def build_flat_image_view(job_name: str, source_root: Path) -> Path:
    """Build and return a flat folder of every image under `source_root`.

    `source_root` is the collection folder already resolved by the caller
    (e.g. outputs/approved/ai_product_mockups). Images are gathered
    recursively so both the one-image-per-concept layout and any future
    flat layout produce the same result.

    Returns the flat directory. Raises nothing for an empty source -- an
    empty folder is a truthful answer ("this job has no approved images
    yet") and the caller has already handled the no-output-at-all case
    with a 409.
    """
    safe = _safe_dir_name(job_name)
    destination = STAGING_ROOT / safe
    # Sibling, not child: the folder the operator opens must hold images
    # and nothing else.
    manifest_path = STAGING_ROOT / f"{safe}.manifest.json"

    images = sorted(
        (p for p in source_root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES),
        key=lambda p: str(p).lower(),
    )
    fingerprint = _fingerprint(images, source_root)

    if destination.is_dir() and _read_manifest(manifest_path) == fingerprint:
        # Nothing approved, rejected or regenerated since the last click.
        # Reuse rather than re-copy: on exFAT this is the difference
        # between opening instantly and copying tens of megabytes first.
        return destination

    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)

    used: set[str] = set()
    for image in images:
        try:
            _link_or_copy(image, destination / _flat_name(image, source_root, used))
        except OSError:
            # One unreadable file (locked by another process, permissions)
            # must not deny the operator the rest of the set.
            continue

    _write_manifest(manifest_path, fingerprint)
    return destination
