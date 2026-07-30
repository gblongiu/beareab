import struct
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from validate_site import (  # noqa: E402
    DuplicateKeyError,
    SiteValidator,
    canonical_url_for_file,
    image_dimensions,
    load_json_text,
    parse_html,
    parse_srcset,
    valid_w3c_datetime,
)


class UrlAndMarkupTests(unittest.TestCase):
    def test_canonical_urls_follow_pages_routing(self):
        self.assertEqual(canonical_url_for_file("index.html"), "https://beareab.com/")
        self.assertEqual(
            canonical_url_for_file("about.html"),
            "https://beareab.com/about.html",
        )
        self.assertEqual(
            canonical_url_for_file("music/psion/index.html"),
            "https://beareab.com/music/psion/",
        )

    def test_srcset_parser_preserves_urls_and_descriptors(self):
        self.assertEqual(
            parse_srcset("/small.avif 480w, /large.avif 960w"),
            [("/small.avif", "480w"), ("/large.avif", "960w")],
        )
        with self.assertRaises(ValueError):
            parse_srcset("/image.avif 480w unexpected")

    def test_html_parser_captures_duplicate_attributes_and_json_ld(self):
        page = parse_html(
            "example.html",
            """<!doctype html>
            <html lang="en"><head><title> Example title </title>
            <script type="application/ld+json">{"@context":"https://schema.org"}</script>
            </head><body><main id="main" id="duplicate"><h1>Example</h1></main></body></html>""",
        )
        self.assertEqual(page.titles, [("Example title", 2)])
        self.assertEqual(len(page.json_ld), 1)
        self.assertEqual(page.duplicate_attributes[0][:2], ("main", "id"))

    def test_json_duplicate_keys_are_rejected(self):
        with self.assertRaises(DuplicateKeyError):
            load_json_text('{"same": 1, "same": 2}')

    def test_json_ld_rejects_double_fragments_and_album_duration(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw = """<!doctype html>
            <html lang="en"><head><title>Example title</title>
            <script type="application/ld+json">
              {
                "@context": "https://schema.org",
                "@type": "MusicAlbum",
                "@id": "https://beareab.com/#track#recording",
                "duration": "PT1M"
              }
            </script></head><body><main><h1>Example</h1></main></body></html>"""
            (root / "index.html").write_text(raw, encoding="utf-8")
            validator = SiteValidator(root)
            page = parse_html("index.html", raw)

            validator._validate_json_ld(page, False, "")

            self.assertEqual(
                {issue.code for issue in validator.issues},
                {"JSONLD_ID_FRAGMENT", "JSONLD_MUSIC_ALBUM_DURATION"},
            )

    def test_w3c_datetime_validation(self):
        self.assertTrue(valid_w3c_datetime("2026-07-27"))
        self.assertTrue(valid_w3c_datetime("2026-07-27T12:34:56Z"))
        self.assertFalse(valid_w3c_datetime("2026-02-30"))
        self.assertFalse(valid_w3c_datetime("July 27, 2026"))


class ImageHeaderTests(unittest.TestCase):
    def setUp(self):
        image_dimensions.cache_clear()
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        image_dimensions.cache_clear()
        self.temporary_directory.cleanup()

    def test_png_dimensions(self):
        path = self.root / "image.png"
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + b"\x00\x00\x00\rIHDR"
            + struct.pack(">II", 1200, 630)
        )
        self.assertEqual(image_dimensions(str(path)), (1200, 630))

    def test_gif_dimensions(self):
        path = self.root / "image.gif"
        path.write_bytes(b"GIF89a" + struct.pack("<HH", 320, 240))
        self.assertEqual(image_dimensions(str(path)), (320, 240))

    def test_avif_ispe_dimensions(self):
        path = self.root / "image.avif"
        path.write_bytes(
            struct.pack(">I", 20)
            + b"ispe"
            + b"\x00\x00\x00\x00"
            + struct.pack(">II", 960, 960)
        )
        self.assertEqual(image_dimensions(str(path)), (960, 960))


if __name__ == "__main__":
    unittest.main()
