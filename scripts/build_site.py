#!/usr/bin/env python3
"""Stage a minimal GitHub Pages artifact from the repository.

The source repository intentionally keeps maintenance scripts and contributor
documentation beside the static site. This builder copies public files while
excluding development-only material from the deployment artifact.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path


PUBLIC_ROOT_FILES = {
    "404.html",
    "CNAME",
    "about.html",
    "analytics.js",
    "android-chrome-192x192.png",
    "android-chrome-512x512.png",
    "apple-touch-icon.png",
    "connect.html",
    "discography.html",
    "downloads.html",
    "favicon-16x16.png",
    "favicon-32x32.png",
    "favicon.ico",
    "favicon.png",
    "index.html",
    "leaving-streaming.html",
    "music-catalog.json",
    "music-lyrics.json",
    "music.html",
    "music.js",
    "privacy.html",
    "projects.html",
    "robots.txt",
    "site.webmanifest",
    "sitemap.xml",
    "store.html",
    "style.css",
    "support.html",
}
PUBLIC_LICENSE_FILES = {
    "assets/fonts/OFL-Inter.txt",
    "assets/fonts/OFL-Newsreader.txt",
}
ASSET_REFERENCE = re.compile(r"/assets/[A-Za-z0-9._/-]+")
REFERENCE_SOURCE_SUFFIXES = {".css", ".html", ".js", ".json", ".xml"}
REQUIRED_PUBLIC_FILES = {
    "404.html",
    "CNAME",
    "index.html",
    "robots.txt",
    "sitemap.xml",
}


def public_source_paths(root: Path) -> list[Path]:
    """Return the explicit public manifest plus assets referenced by it."""

    selected = {
        Path(relative)
        for relative in PUBLIC_ROOT_FILES
        if (root / relative).is_file()
    }
    selected.update(
        path.relative_to(root)
        for path in root.glob("music/**/index.html")
        if path.is_file()
    )
    selected.update(
        Path(relative)
        for relative in PUBLIC_LICENSE_FILES
        if (root / relative).is_file()
    )

    referenced_assets: set[Path] = set()
    for relative in sorted(selected):
        if relative.suffix.lower() not in REFERENCE_SOURCE_SUFFIXES:
            continue
        text = (root / relative).read_text(encoding="utf-8")
        for raw_path in ASSET_REFERENCE.findall(text):
            asset = Path(raw_path.lstrip("/"))
            if ".." in asset.parts:
                raise ValueError(f"unsafe public asset reference in {relative}: {raw_path}")
            referenced_assets.add(asset)

    missing_assets = sorted(
        relative for relative in referenced_assets if not (root / relative).is_file()
    )
    if missing_assets:
        raise ValueError(
            "public manifest references missing assets: "
            + ", ".join(str(path) for path in missing_assets)
        )
    selected.update(referenced_assets)
    return sorted(selected)


def stage_site(root: Path, output: Path) -> tuple[int, int]:
    root = root.resolve()
    if output.expanduser().absolute().is_symlink():
        raise ValueError(f"refusing to replace symlinked output: {output}")
    output = output.resolve()
    protected = {Path("/").resolve(), Path.home().resolve(), root}
    if output in protected:
        raise ValueError(f"refusing unsafe output directory: {output}")

    public_sources = public_source_paths(root)

    if output.exists():
        default_output = (root / "_site").resolve()
        if output != default_output:
            raise ValueError(
                f"refusing to replace existing non-default output directory: {output}"
            )
        shutil.rmtree(output)
    output.mkdir(parents=True)

    published = 0
    total_bytes = 0
    for relative in public_sources:
        source = root / relative
        if source.is_symlink():
            raise ValueError(f"public deployment source must not be a symlink: {relative}")
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        published += 1
        total_bytes += source.stat().st_size

    missing = sorted(
        relative for relative in REQUIRED_PUBLIC_FILES if not (output / relative).is_file()
    )
    if missing:
        raise ValueError(
            "staged site is missing required public files: " + ", ".join(missing)
        )
    return published, total_bytes


def parse_args() -> argparse.Namespace:
    repository_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=repository_root,
        help="repository root (defaults to the parent of scripts/)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repository_root / "_site",
        help="staging directory (defaults to _site/)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.root.is_dir():
        print(f"error: repository root is not a directory: {args.root}", file=sys.stderr)
        return 2
    try:
        count, total_bytes = stage_site(args.root, args.output)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"Staged {count} public files ({total_bytes:,} bytes) in "
        f"{args.output.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
