#!/usr/bin/env python3
"""Build responsive artwork and verify its source-bound integrity manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIRECTORY = ROOT / "assets" / "optimized"
MANIFEST_PATH = OUTPUT_DIRECTORY / "manifest.json"
RECIPE = "imagemagick-v1:webp-q78:avif-q45:heic-speed6:strip"


@dataclass(frozen=True)
class ImageFamily:
    source: str
    basename: str
    widths: tuple[int, ...]


def image_families(root: Path = ROOT) -> list[ImageFamily]:
    downloads = [
        ImageFamily(
            source=path.relative_to(root).as_posix(),
            basename=path.stem,
            widths=(480, 700),
        )
        for path in sorted((root / "assets" / "downloads").glob("*.jpg"))
    ]
    editorial = [
        ImageFamily("assets/headshot.jpg", "headshot", (480, 960)),
        ImageFamily(
            "assets/ucygrx-nadir.jpg",
            "ucygrx-nadir-project",
            (480, 960),
        ),
        ImageFamily(
            "assets/theimitationzone.jpeg",
            "the-imitation-zone",
            (480, 960),
        ),
        ImageFamily("assets/psion-display.jpg", "psion-display", (480, 960)),
        ImageFamily(
            "assets/beareab-eyesplice-display.jpg",
            "eyesplice-display",
            (480, 960),
        ),
        ImageFamily("assets/solace-display.jpg", "solace-display", (480, 960)),
    ]
    return downloads + editorial


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def output_paths(root: Path, family: ImageFamily) -> list[Path]:
    output = root / "assets" / "optimized"
    return [
        output / f"{family.basename}-{width}.{extension}"
        for width in family.widths
        for extension in ("avif", "webp")
    ]


def build_manifest(
    root: Path = ROOT,
    families: list[ImageFamily] | None = None,
) -> dict[str, object]:
    families = image_families(root) if families is None else families
    entries: list[dict[str, object]] = []
    for family in families:
        source = root / family.source
        if not source.is_file():
            raise FileNotFoundError(f"responsive-image source is missing: {family.source}")

        outputs: dict[str, str] = {}
        for output in output_paths(root, family):
            if not output.is_file():
                relative = output.relative_to(root).as_posix()
                raise FileNotFoundError(f"responsive-image derivative is missing: {relative}")
            outputs[output.relative_to(root).as_posix()] = sha256_file(output)

        entries.append(
            {
                "basename": family.basename,
                "outputs": outputs,
                "source": family.source,
                "source_sha256": sha256_file(source),
                "widths": list(family.widths),
            }
        )

    return {
        "version": 1,
        "recipe": RECIPE,
        "families": entries,
    }


def expected_media_paths(
    root: Path = ROOT,
    families: list[ImageFamily] | None = None,
) -> set[Path]:
    families = image_families(root) if families is None else families
    return {
        output.relative_to(root)
        for family in families
        for output in output_paths(root, family)
    }


def verify_manifest(
    root: Path = ROOT,
    families: list[ImageFamily] | None = None,
) -> list[str]:
    manifest_path = root / "assets" / "optimized" / "manifest.json"
    if not manifest_path.is_file():
        return ["assets/optimized/manifest.json is missing"]

    try:
        actual = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"responsive-image manifest is unreadable: {exc}"]

    try:
        expected = build_manifest(root, families)
    except (OSError, FileNotFoundError) as exc:
        return [str(exc)]

    failures: list[str] = []
    if actual != expected:
        failures.append(
            "responsive-image manifest does not match its source and derivative hashes"
        )

    expected_media = expected_media_paths(root, families)
    actual_media = {
        path.relative_to(root)
        for pattern in ("*.avif", "*.webp")
        for path in (root / "assets" / "optimized").glob(pattern)
    }
    for relative in sorted(expected_media - actual_media):
        failures.append(f"responsive-image derivative is missing: {relative.as_posix()}")
    for relative in sorted(actual_media - expected_media):
        failures.append(f"unexpected responsive-image derivative: {relative.as_posix()}")
    return failures


def encode_family(magick: str, root: Path, family: ImageFamily) -> None:
    source = root / family.source
    if not source.is_file():
        raise FileNotFoundError(f"responsive-image source is missing: {family.source}")

    output_directory = root / "assets" / "optimized"
    output_directory.mkdir(parents=True, exist_ok=True)
    for width in family.widths:
        subprocess.run(
            [
                magick,
                str(source),
                "-resize",
                f"{width}x{width}>",
                "-strip",
                "-quality",
                "78",
                str(output_directory / f"{family.basename}-{width}.webp"),
            ],
            check=True,
        )
        subprocess.run(
            [
                magick,
                str(source),
                "-resize",
                f"{width}x{width}>",
                "-strip",
                "-define",
                "heic:speed=6",
                "-quality",
                "45",
                str(output_directory / f"{family.basename}-{width}.avif"),
            ],
            check=True,
        )


def build_images(root: Path = ROOT) -> None:
    magick = shutil.which("magick")
    if not magick:
        raise RuntimeError("ImageMagick is required. Install it and rerun this script.")

    families = image_families(root)
    for family in families:
        encode_family(magick, root, family)

    manifest = build_manifest(root, families)
    manifest_path = root / "assets" / "optimized" / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true", help="encode images and update manifest")
    mode.add_argument("--check", action="store_true", help="verify source and output hashes")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.build:
        try:
            build_images()
        except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print("Responsive AVIF and WebP assets are current.")
        return 0

    failures = verify_manifest()
    if failures:
        print("Responsive-image integrity: FAIL", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print(
            "Run `bash scripts/build_responsive_images.sh`, review the images, and rerun the gate.",
            file=sys.stderr,
        )
        return 1
    print("Responsive-image integrity: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
