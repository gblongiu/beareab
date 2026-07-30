import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from build_site import stage_site  # noqa: E402


class BuildSiteTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "repository"
        self.root.mkdir()
        for relative in ("404.html", "CNAME", "index.html", "robots.txt", "sitemap.xml"):
            (self.root / relative).write_text(relative, encoding="utf-8")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_staging_uses_explicit_pages_and_referenced_assets_only(self):
        (self.root / "assets").mkdir()
        (self.root / "assets" / "cover.jpg").write_bytes(b"cover")
        (self.root / "assets" / "unused.jpg").write_bytes(b"unused")
        (self.root / "index.html").write_text(
            '<img src="/assets/cover.jpg" alt="">',
            encoding="utf-8",
        )
        (self.root / "music").mkdir()
        (self.root / "music" / "index.html").write_text("music", encoding="utf-8")
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "private.py").write_text("private", encoding="utf-8")
        (self.root / "README.md").write_text("private", encoding="utf-8")

        output = Path(self.temporary_directory.name) / "artifact"
        count, _ = stage_site(self.root, output)

        self.assertEqual(count, 7)
        self.assertTrue((output / "assets" / "cover.jpg").is_file())
        self.assertFalse((output / "assets" / "unused.jpg").exists())
        self.assertTrue((output / "music" / "index.html").is_file())
        self.assertFalse((output / "scripts").exists())
        self.assertFalse((output / "README.md").exists())

    def test_staging_refuses_to_replace_repository_root(self):
        with self.assertRaises(ValueError):
            stage_site(self.root, self.root)

    def test_staging_rejects_missing_referenced_asset(self):
        (self.root / "index.html").write_text(
            '<img src="/assets/missing.jpg" alt="">',
            encoding="utf-8",
        )

        output = Path(self.temporary_directory.name) / "artifact"
        with self.assertRaisesRegex(ValueError, "missing assets"):
            stage_site(self.root, output)


if __name__ == "__main__":
    unittest.main()
