import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from responsive_images import (  # noqa: E402
    ImageFamily,
    build_manifest,
    verify_manifest,
)


class ResponsiveImageManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        (self.root / "assets" / "optimized").mkdir(parents=True)
        (self.root / "assets" / "source.jpg").write_bytes(b"canonical source")
        (self.root / "assets" / "optimized" / "cover-480.avif").write_bytes(
            b"avif derivative"
        )
        (self.root / "assets" / "optimized" / "cover-480.webp").write_bytes(
            b"webp derivative"
        )
        self.families = [
            ImageFamily("assets/source.jpg", "cover", (480,)),
        ]

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write_manifest(self):
        manifest = build_manifest(self.root, self.families)
        (self.root / "assets" / "optimized" / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_manifest_binds_sources_and_derivatives(self):
        self.write_manifest()
        self.assertEqual(verify_manifest(self.root, self.families), [])

        (self.root / "assets" / "source.jpg").write_bytes(b"changed source")
        self.assertIn(
            "responsive-image manifest does not match its source and derivative hashes",
            verify_manifest(self.root, self.families),
        )

    def test_unexpected_derivative_is_rejected(self):
        self.write_manifest()
        (self.root / "assets" / "optimized" / "orphan-480.avif").write_bytes(
            b"orphan"
        )
        failures = verify_manifest(self.root, self.families)
        self.assertTrue(
            any("unexpected responsive-image derivative" in item for item in failures)
        )


if __name__ == "__main__":
    unittest.main()
