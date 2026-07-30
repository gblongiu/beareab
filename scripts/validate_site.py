#!/usr/bin/env python3
"""Deterministic, dependency-free quality gate for the beareab static site.

The validator intentionally uses only the Python standard library so the same
checks run locally, in CI, and against a staged deployment artifact.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import posixpath
import re
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree


SITE_ORIGIN = "https://beareab.com"
SITE_HOST = "beareab.com"
SCHEMA_CONTEXTS = {"https://schema.org", "http://schema.org"}
VOID_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
IDREF_ATTRIBUTES = {
    "aria-activedescendant",
    "aria-controls",
    "aria-describedby",
    "aria-details",
    "aria-errormessage",
    "aria-flowto",
    "aria-labelledby",
    "aria-owns",
    "for",
    "headers",
    "list",
    "popovertarget",
}
URL_ATTRIBUTES = {
    ("a", "href"),
    ("area", "href"),
    ("audio", "src"),
    ("form", "action"),
    ("iframe", "src"),
    ("img", "src"),
    ("input", "src"),
    ("link", "href"),
    ("object", "data"),
    ("script", "src"),
    ("source", "src"),
    ("track", "src"),
    ("video", "poster"),
    ("video", "src"),
}
RASTER_SUFFIXES = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
SKIPPED_SCHEMES = {"data", "mailto", "sms", "tel"}
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
IMAGE_SITEMAP_NS = "http://www.google.com/schemas/sitemap-image/1.1"

# Raw bytes catch accidental source bloat; deterministic gzip-9 bytes are an
# offline transfer-size proxy. These ceilings leave generous room for release
# pages with embedded lyrics while still stopping regressions measured in
# hundreds of kilobytes.
TEXT_FILE_BUDGETS = {
    ".html": (220 * 1024, 40 * 1024),
    ".css": (100 * 1024, 24 * 1024),
    ".js": (80 * 1024, 20 * 1024),
    ".json": (300 * 1024, 80 * 1024),
}
TEXT_AGGREGATE_BUDGETS = {
    ".css": (120 * 1024, 28 * 1024),
    ".js": (160 * 1024, 40 * 1024),
}
OPTIMIZED_IMAGE_BUDGETS = {
    ".avif": 140 * 1024,
    ".webp": 300 * 1024,
}
EAGER_IMAGE_ALLOWANCE = {
    # The homepage's four covers are one composite above-the-fold hero.
    "index.html": 4,
}


class DuplicateKeyError(ValueError):
    """Raised when JSON contains a duplicate object key."""


def reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate key {key!r}")
        result[key] = value
    return result


def load_json_text(raw: str) -> Any:
    return json.loads(raw, object_pairs_hook=reject_duplicate_json_keys)


@dataclass(frozen=True, order=True)
class Issue:
    severity: str
    path: str
    line: int
    code: str
    message: str


@dataclass
class Element:
    tag: str
    attrs: dict[str, str | None]
    raw_attrs: list[tuple[str, str | None]]
    line: int


@dataclass
class HtmlPage:
    path: str
    raw: str
    doctypes: list[tuple[str, int]] = field(default_factory=list)
    elements: list[Element] = field(default_factory=list)
    titles: list[tuple[str, int]] = field(default_factory=list)
    json_ld: list[tuple[str, int]] = field(default_factory=list)
    duplicate_attributes: list[tuple[str, str, int]] = field(default_factory=list)

    def elements_named(self, tag: str) -> list[Element]:
        return [element for element in self.elements if element.tag == tag]

    @property
    def ids(self) -> list[tuple[str, int]]:
        return [
            (element.attrs["id"] or "", element.line)
            for element in self.elements
            if "id" in element.attrs
        ]

    def meta(self, attribute: str, key: str) -> list[tuple[str, int]]:
        attribute = attribute.lower()
        key = key.lower()
        values: list[tuple[str, int]] = []
        for element in self.elements_named("meta"):
            candidate = (element.attrs.get(attribute) or "").lower()
            if candidate == key:
                values.append((element.attrs.get("content") or "", element.line))
        return values

    def links_with_rel(self, relationship: str) -> list[Element]:
        relationship = relationship.lower()
        return [
            element
            for element in self.elements_named("link")
            if relationship in (element.attrs.get("rel") or "").lower().split()
        ]


class SiteHtmlParser(HTMLParser):
    def __init__(self, path: str, raw: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page = HtmlPage(path=path, raw=raw)
        self._title_parts: list[str] | None = None
        self._title_line = 0
        self._json_ld_parts: list[str] | None = None
        self._json_ld_line = 0

    def handle_decl(self, decl: str) -> None:
        self.page.doctypes.append((decl, self.getpos()[0]))

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self._record_start(tag, attrs)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self._record_start(tag, attrs)

    def _record_start(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        line = self.getpos()[0]
        lowered = [(name.lower(), value) for name, value in attrs]
        counts = Counter(name for name, _ in lowered)
        for name, count in counts.items():
            if count > 1:
                self.page.duplicate_attributes.append((tag, name, line))
        attr_map: dict[str, str | None] = {}
        for name, value in lowered:
            attr_map.setdefault(name, value)
        self.page.elements.append(
            Element(tag=tag, attrs=attr_map, raw_attrs=lowered, line=line)
        )

        if tag == "title":
            self._title_parts = []
            self._title_line = line
        if tag == "script" and (attr_map.get("type") or "").lower() == (
            "application/ld+json"
        ):
            self._json_ld_parts = []
            self._json_ld_line = line

    def handle_data(self, data: str) -> None:
        if self._title_parts is not None:
            self._title_parts.append(data)
        if self._json_ld_parts is not None:
            self._json_ld_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title" and self._title_parts is not None:
            title = " ".join("".join(self._title_parts).split())
            self.page.titles.append((title, self._title_line))
            self._title_parts = None
        if tag == "script" and self._json_ld_parts is not None:
            self.page.json_ld.append(
                ("".join(self._json_ld_parts).strip(), self._json_ld_line)
            )
            self._json_ld_parts = None


def parse_html(path: str, raw: str) -> HtmlPage:
    parser = SiteHtmlParser(path, raw)
    parser.feed(raw)
    parser.close()
    return parser.page


def canonical_url_for_file(relative_path: str) -> str:
    if relative_path == "index.html":
        return f"{SITE_ORIGIN}/"
    if relative_path.endswith("/index.html"):
        return f"{SITE_ORIGIN}/{relative_path[:-10]}"
    return f"{SITE_ORIGIN}/{relative_path}"


def is_positive_integer(value: str | None) -> bool:
    return bool(value and re.fullmatch(r"[1-9]\d*", value))


def parse_srcset(value: str) -> list[tuple[str, str | None]]:
    """Parse the site's ordinary srcset form.

    Data URLs are deliberately not supported because commas inside them are
    ambiguous without implementing the full HTML tokenization algorithm.
    The site does not use data URLs for responsive images.
    """

    candidates: list[tuple[str, str | None]] = []
    for raw_candidate in value.split(","):
        tokens = raw_candidate.strip().split()
        if not tokens:
            continue
        if len(tokens) > 2:
            raise ValueError(f"invalid srcset candidate {raw_candidate.strip()!r}")
        candidates.append((tokens[0], tokens[1] if len(tokens) == 2 else None))
    return candidates


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    offset = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while offset + 3 < len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0x01, *range(0xD0, 0xD9)}:
            continue
        if offset + 2 > len(data):
            break
        segment_length = struct.unpack(">H", data[offset : offset + 2])[0]
        if segment_length < 2 or offset + segment_length > len(data):
            break
        if marker in sof_markers and segment_length >= 7:
            height, width = struct.unpack(">HH", data[offset + 3 : offset + 7])
            return width, height
        offset += segment_length
    return None


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk_type = data[12:16]
    payload = data[20:]
    if chunk_type == b"VP8X" and len(payload) >= 10:
        width = int.from_bytes(payload[4:7], "little") + 1
        height = int.from_bytes(payload[7:10], "little") + 1
        return width, height
    if chunk_type == b"VP8 " and len(payload) >= 10:
        if payload[3:6] != b"\x9d\x01\x2a":
            return None
        width, height = struct.unpack("<HH", payload[6:10])
        return width & 0x3FFF, height & 0x3FFF
    if chunk_type == b"VP8L" and len(payload) >= 5 and payload[0] == 0x2F:
        bits = int.from_bytes(payload[1:5], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def _avif_dimensions(data: bytes) -> tuple[int, int] | None:
    # An AVIF's ImageSpatialExtentsProperty (`ispe`) contains full-box flags
    # followed by 32-bit width and height. Select the largest valid extent when
    # auxiliary images are also present.
    dimensions: list[tuple[int, int]] = []
    cursor = 0
    while True:
        marker = data.find(b"ispe", cursor)
        if marker < 0:
            break
        if marker >= 4 and marker + 16 <= len(data):
            box_start = marker - 4
            box_size = int.from_bytes(data[box_start:marker], "big")
            if box_size >= 20 and box_start + box_size <= len(data):
                width = int.from_bytes(data[marker + 8 : marker + 12], "big")
                height = int.from_bytes(data[marker + 12 : marker + 16], "big")
                if width > 0 and height > 0:
                    dimensions.append((width, height))
        cursor = marker + 4
    return max(dimensions, key=lambda pair: pair[0] * pair[1]) if dimensions else None


@lru_cache(maxsize=None)
def image_dimensions(path_string: str) -> tuple[int, int] | None:
    path = Path(path_string)
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".png" and len(data) >= 24 and data[:8] == (
        b"\x89PNG\r\n\x1a\n"
    ):
        return struct.unpack(">II", data[16:24])
    if suffix in {".jpg", ".jpeg"}:
        return _jpeg_dimensions(data)
    if suffix == ".gif" and len(data) >= 10 and data[:6] in {
        b"GIF87a",
        b"GIF89a",
    }:
        return struct.unpack("<HH", data[6:10])
    if suffix == ".webp":
        return _webp_dimensions(data)
    if suffix == ".avif":
        return _avif_dimensions(data)
    return None


class SiteValidator:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.issues: list[Issue] = []
        self.stats: dict[str, int] = defaultdict(int)
        self.files = {
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file()
            and ".git" not in path.relative_to(self.root).parts
            and "_site" not in path.relative_to(self.root).parts
        }
        self.pages: dict[str, HtmlPage] = {}
        self.page_indexability: dict[str, bool] = {}
        self.page_canonicals: dict[str, str] = {}
        self.inbound_links: dict[str, set[str]] = defaultdict(set)
        self._validated_resources: set[tuple[str, str, bool]] = set()

    def error(
        self, code: str, path: str, message: str, line: int | None = None
    ) -> None:
        self.issues.append(Issue("error", path, line or 1, code, message))

    def warning(
        self, code: str, path: str, message: str, line: int | None = None
    ) -> None:
        self.issues.append(Issue("warning", path, line or 1, code, message))

    def run(self) -> None:
        self._parse_pages()
        self._validate_html_documents()
        self._validate_performance_budgets()
        self._validate_references()
        self._validate_styles_and_scripts()
        self._validate_json_files()
        self._validate_music_data()
        self._validate_manifest()
        self._validate_sitemap()
        self._validate_robots()
        self._validate_cname()
        self._validate_orphans()

    def _parse_pages(self) -> None:
        for relative in sorted(
            path for path in self.files if path.lower().endswith(".html")
        ):
            path = self.root / relative
            try:
                raw = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                self.error("HTML_UTF8", relative, f"is not valid UTF-8: {exc}")
                continue
            try:
                self.pages[relative] = parse_html(relative, raw)
            except Exception as exc:  # HTMLParser exceptions are uncommon.
                self.error("HTML_PARSE", relative, f"could not be parsed: {exc}")
        self.stats["html_pages"] = len(self.pages)

    def _single_meta(
        self,
        page: HtmlPage,
        attribute: str,
        name: str,
        required: bool = True,
    ) -> tuple[str, int] | None:
        values = page.meta(attribute, name)
        if len(values) > 1:
            self.error(
                "META_DUPLICATE",
                page.path,
                f"has {len(values)} {attribute}={name!r} metadata entries",
                values[1][1],
            )
        if not values:
            if required:
                self.error(
                    "META_MISSING",
                    page.path,
                    f"is missing metadata {attribute}={name!r}",
                )
            return None
        value, line = values[0]
        if required and not value.strip():
            self.error(
                "META_EMPTY",
                page.path,
                f"has empty metadata {attribute}={name!r}",
                line,
            )
        return value.strip(), line

    def _validate_html_documents(self) -> None:
        title_owners: dict[str, list[str]] = defaultdict(list)
        description_owners: dict[str, list[str]] = defaultdict(list)
        canonical_owners: dict[str, list[str]] = defaultdict(list)

        for relative, page in sorted(self.pages.items()):
            if page.raw.startswith("\ufeff"):
                self.error("HTML_BOM", relative, "must not begin with a UTF-8 BOM")
            if not page.doctypes or page.doctypes[0][0].lower() != "doctype html":
                self.error(
                    "HTML_DOCTYPE", relative, "must begin with <!doctype html>"
                )
            if len(page.doctypes) > 1:
                self.error(
                    "HTML_DOCTYPE_DUPLICATE",
                    relative,
                    "contains multiple doctypes",
                    page.doctypes[1][1],
                )

            for tag, attribute, line in page.duplicate_attributes:
                self.error(
                    "HTML_ATTRIBUTE_DUPLICATE",
                    relative,
                    f"<{tag}> repeats the {attribute!r} attribute",
                    line,
                )

            html_elements = page.elements_named("html")
            if len(html_elements) != 1:
                self.error(
                    "HTML_ROOT",
                    relative,
                    f"must contain exactly one <html>; found {len(html_elements)}",
                )
            elif not (html_elements[0].attrs.get("lang") or "").strip():
                self.error(
                    "HTML_LANG",
                    relative,
                    "<html> must declare a non-empty lang attribute",
                    html_elements[0].line,
                )

            charsets = [
                element
                for element in page.elements_named("meta")
                if "charset" in element.attrs
            ]
            if len(charsets) != 1:
                self.error(
                    "META_CHARSET",
                    relative,
                    f"must contain exactly one charset declaration; found {len(charsets)}",
                )
            elif (charsets[0].attrs.get("charset") or "").lower() != "utf-8":
                self.error(
                    "META_CHARSET",
                    relative,
                    "charset must be utf-8",
                    charsets[0].line,
                )
            elif page.raw.encode("utf-8").find(b"<meta charset") > 1024:
                self.error(
                    "META_CHARSET_POSITION",
                    relative,
                    "charset declaration must occur within the first 1024 bytes",
                    charsets[0].line,
                )

            self._single_meta(page, "name", "viewport")
            robots_entry = self._single_meta(page, "name", "robots")
            robots = robots_entry[0].lower() if robots_entry else ""
            robots_tokens = {
                token.strip()
                for token in re.split(r"[,;]", robots)
                if token.strip()
            }
            indexable = "noindex" not in robots_tokens
            self.page_indexability[relative] = indexable

            if len(page.titles) != 1:
                self.error(
                    "TITLE_COUNT",
                    relative,
                    f"must contain exactly one non-empty <title>; found {len(page.titles)}",
                )
                title = ""
            else:
                title, title_line = page.titles[0]
                if not title:
                    self.error("TITLE_EMPTY", relative, "<title> is empty", title_line)
                elif indexable:
                    title_owners[title.casefold()].append(relative)
                    if not 20 <= len(title) <= 65:
                        self.warning(
                            "TITLE_LENGTH",
                            relative,
                            f"title is {len(title)} characters; review its search-result presentation",
                            title_line,
                        )

            h1s = page.elements_named("h1")
            if len(h1s) != 1:
                self.error(
                    "H1_COUNT",
                    relative,
                    f"must contain exactly one <h1>; found {len(h1s)}",
                )
            mains = page.elements_named("main")
            if len(mains) != 1:
                self.error(
                    "MAIN_COUNT",
                    relative,
                    f"must contain exactly one <main>; found {len(mains)}",
                )
            if page.elements_named("base"):
                self.error(
                    "BASE_ELEMENT",
                    relative,
                    "<base> is unsupported because it makes local link checks ambiguous",
                    page.elements_named("base")[0].line,
                )

            description_entry = self._single_meta(
                page,
                "name",
                "description",
                required=relative != "404.html",
            )
            description = description_entry[0] if description_entry else ""
            if description_entry and indexable:
                description_owners[description.casefold()].append(relative)
                if not 70 <= len(description) <= 170:
                    self.warning(
                        "DESCRIPTION_LENGTH",
                        relative,
                        f"meta description is {len(description)} characters; review its search-result presentation",
                        description_entry[1],
                    )

            canonical_links = page.links_with_rel("canonical")
            if relative == "404.html":
                if canonical_links:
                    self.error(
                        "CANONICAL_404",
                        relative,
                        "the 404 page must not declare a canonical URL",
                        canonical_links[0].line,
                    )
                if indexable:
                    self.error(
                        "ROBOTS_404", relative, "the 404 page must be noindex"
                    )
                self._validate_ids(page)
                self._validate_idrefs(page)
                self._validate_images(page)
                continue

            if len(canonical_links) != 1:
                self.error(
                    "CANONICAL_COUNT",
                    relative,
                    f"must declare exactly one canonical link; found {len(canonical_links)}",
                )
                canonical = ""
            else:
                canonical_element = canonical_links[0]
                canonical = (canonical_element.attrs.get("href") or "").strip()
                self._validate_canonical(relative, canonical, canonical_element.line)
                self.page_canonicals[relative] = canonical
                if indexable:
                    canonical_owners[canonical].append(relative)

            refreshes = page.meta("http-equiv", "refresh")
            is_redirect = bool(refreshes)
            if len(refreshes) > 1:
                self.error(
                    "REDIRECT_DUPLICATE",
                    relative,
                    "contains multiple meta refresh directives",
                    refreshes[1][1],
                )
            if is_redirect:
                if indexable:
                    self.error(
                        "REDIRECT_INDEXABLE",
                        relative,
                        "a client-side redirect page must be noindex",
                        refreshes[0][1],
                    )
                target = self._meta_refresh_target(
                    relative, refreshes[0][0], refreshes[0][1]
                )
                if target and canonical and canonical != target:
                    self.error(
                        "REDIRECT_CANONICAL",
                        relative,
                        f"redirect target {target!r} does not match canonical {canonical!r}",
                        refreshes[0][1],
                    )
            elif canonical:
                expected = canonical_url_for_file(relative)
                if canonical != expected:
                    self.error(
                        "CANONICAL_SELF",
                        relative,
                        f"canonical must be self-referential ({expected}); found {canonical}",
                        canonical_links[0].line,
                    )

            self._validate_ids(page)
            self._validate_idrefs(page)
            self._validate_images(page)
            self._validate_open_graph(
                page, indexable, title, description, canonical
            )
            self._validate_json_ld(page, indexable, canonical)

        for normalized, owners in sorted(title_owners.items()):
            if len(owners) > 1:
                self.error(
                    "TITLE_DUPLICATE",
                    owners[1],
                    f"indexable pages share a title: {', '.join(owners)}",
                )
        for normalized, owners in sorted(description_owners.items()):
            if len(owners) > 1:
                self.error(
                    "DESCRIPTION_DUPLICATE",
                    owners[1],
                    f"indexable pages share a meta description: {', '.join(owners)}",
                )
        for canonical, owners in sorted(canonical_owners.items()):
            if len(owners) > 1:
                self.error(
                    "CANONICAL_DUPLICATE",
                    owners[1],
                    f"indexable pages share canonical {canonical}: {', '.join(owners)}",
                )

    def _validate_canonical(self, page_path: str, value: str, line: int) -> None:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != SITE_HOST
            or parsed.username
            or parsed.password
            or parsed.port
        ):
            self.error(
                "CANONICAL_ORIGIN",
                page_path,
                f"canonical must use {SITE_ORIGIN} with no credentials or port",
                line,
            )
        if parsed.query or parsed.fragment:
            self.error(
                "CANONICAL_COMPONENTS",
                page_path,
                "canonical must not contain a query string or fragment",
                line,
            )
        self._resolve_local(page_path, value, "canonical", line, check_fragment=False)

    def _meta_refresh_target(
        self, page_path: str, content: str, line: int
    ) -> str | None:
        match = re.fullmatch(
            r"\s*0\s*;\s*url\s*=\s*(['\"]?)(.*?)\1\s*",
            content,
            flags=re.IGNORECASE,
        )
        if not match:
            self.error(
                "REDIRECT_FORMAT",
                page_path,
                "meta refresh must use an immediate `0; url=…` target",
                line,
            )
            return None
        raw_target = match.group(2)
        target = self._resolve_local(
            page_path, raw_target, "meta refresh", line, check_fragment=False
        )
        if not target:
            return None
        return canonical_url_for_file(target[0])

    def _validate_ids(self, page: HtmlPage) -> None:
        seen: dict[str, int] = {}
        for identifier, line in page.ids:
            if not identifier:
                self.error("ID_EMPTY", page.path, "id must not be empty", line)
                continue
            if re.search(r"\s", identifier):
                self.error(
                    "ID_WHITESPACE",
                    page.path,
                    f"id {identifier!r} must not contain whitespace",
                    line,
                )
            if identifier in seen:
                self.error(
                    "ID_DUPLICATE",
                    page.path,
                    f"id {identifier!r} duplicates the id on line {seen[identifier]}",
                    line,
                )
            else:
                seen[identifier] = line

    def _validate_idrefs(self, page: HtmlPage) -> None:
        ids = {identifier for identifier, _ in page.ids}
        for element in page.elements:
            for attribute in IDREF_ATTRIBUTES:
                if attribute not in element.attrs:
                    continue
                raw = (element.attrs.get(attribute) or "").strip()
                if not raw:
                    self.error(
                        "IDREF_EMPTY",
                        page.path,
                        f"<{element.tag}> has an empty {attribute} attribute",
                        element.line,
                    )
                    continue
                for identifier in raw.split():
                    if identifier not in ids:
                        self.error(
                            "IDREF_MISSING",
                            page.path,
                            f"<{element.tag}> {attribute} references missing id {identifier!r}",
                            element.line,
                        )

    def _validate_images(self, page: HtmlPage) -> None:
        for image in page.elements_named("img"):
            if "alt" not in image.attrs:
                self.error(
                    "IMAGE_ALT_MISSING",
                    page.path,
                    "<img> must declare alt (use alt=\"\" for a decorative image)",
                    image.line,
                )
            width = image.attrs.get("width")
            height = image.attrs.get("height")
            if not is_positive_integer(width) or not is_positive_integer(height):
                self.error(
                    "IMAGE_DIMENSIONS_MISSING",
                    page.path,
                    "<img> must declare positive integer width and height attributes",
                    image.line,
                )
            src = (image.attrs.get("src") or "").strip()
            if not src:
                self.error(
                    "IMAGE_SRC_MISSING",
                    page.path,
                    "<img> must declare a non-empty src",
                    image.line,
                )
            else:
                target = self._resolve_local(
                    page.path, src, "image src", image.line, check_fragment=False
                )
                if (
                    target
                    and is_positive_integer(width)
                    and is_positive_integer(height)
                    and Path(target[0]).suffix.lower() in RASTER_SUFFIXES
                ):
                    self._validate_declared_ratio(
                        page.path,
                        image.line,
                        target[0],
                        int(width or 0),
                        int(height or 0),
                    )
            srcset = image.attrs.get("srcset")
            if srcset:
                self._validate_srcset(page.path, image, srcset)

        for source in page.elements_named("source"):
            srcset = source.attrs.get("srcset")
            if srcset:
                self._validate_srcset(page.path, source, srcset)
        for preload in page.elements_named("link"):
            image_srcset = preload.attrs.get("imagesrcset")
            if image_srcset:
                self._validate_srcset(page.path, preload, image_srcset)

        images = page.elements_named("img")
        eager_allowance = EAGER_IMAGE_ALLOWANCE.get(page.path, 1)
        high_priority_images = [
            image
            for image in images
            if (image.attrs.get("fetchpriority") or "").lower() == "high"
        ]
        if len(high_priority_images) > 1:
            self.error(
                "IMAGE_PRIORITY_COUNT",
                page.path,
                "at most one image per page may use fetchpriority=\"high\"",
                high_priority_images[1].line,
            )
        for index, image in enumerate(images):
            loading = (image.attrs.get("loading") or "").lower()
            priority = (image.attrs.get("fetchpriority") or "").lower()
            if index >= eager_allowance and loading != "lazy":
                self.error(
                    "IMAGE_LAZY_LOADING",
                    page.path,
                    (
                        "below-the-fold images must use loading=\"lazy\" "
                        f"(image {index + 1} of {len(images)})"
                    ),
                    image.line,
                )
            if index == 0 and loading == "lazy":
                self.error(
                    "IMAGE_LCP_LAZY",
                    page.path,
                    "the first content image must not be lazy-loaded",
                    image.line,
                )
            if priority == "high" and loading == "lazy":
                self.error(
                    "IMAGE_PRIORITY_CONFLICT",
                    page.path,
                    "fetchpriority=\"high\" must not be combined with lazy loading",
                    image.line,
                )
            if priority and priority not in {"high", "low", "auto"}:
                self.error(
                    "IMAGE_PRIORITY_VALUE",
                    page.path,
                    f"invalid fetchpriority value {priority!r}",
                    image.line,
                )

    def _validate_performance_budgets(self) -> None:
        aggregate: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for relative in sorted(self.files):
            if relative.startswith((".github/", "scripts/")):
                continue
            suffix = Path(relative).suffix.lower()
            if suffix not in TEXT_FILE_BUDGETS:
                continue
            data = (self.root / relative).read_bytes()
            compressed_size = len(gzip.compress(data, compresslevel=9, mtime=0))
            raw_budget, compressed_budget = TEXT_FILE_BUDGETS[suffix]
            if len(data) > raw_budget:
                self.error(
                    "PERF_RAW_BUDGET",
                    relative,
                    (
                        f"{len(data):,} raw bytes exceeds the "
                        f"{raw_budget:,}-byte {suffix} budget"
                    ),
                )
            if compressed_size > compressed_budget:
                self.error(
                    "PERF_TRANSFER_BUDGET",
                    relative,
                    (
                        f"{compressed_size:,} deterministic gzip bytes exceeds the "
                        f"{compressed_budget:,}-byte {suffix} transfer proxy"
                    ),
                )
            if suffix in TEXT_AGGREGATE_BUDGETS:
                aggregate[suffix][0] += len(data)
                aggregate[suffix][1] += compressed_size

        for suffix, (raw_size, compressed_size) in sorted(aggregate.items()):
            raw_budget, compressed_budget = TEXT_AGGREGATE_BUDGETS[suffix]
            if raw_size > raw_budget:
                self.error(
                    "PERF_AGGREGATE_RAW",
                    suffix.lstrip("."),
                    (
                        f"first-party {suffix} totals {raw_size:,} raw bytes, above "
                        f"the {raw_budget:,}-byte site budget"
                    ),
                )
            if compressed_size > compressed_budget:
                self.error(
                    "PERF_AGGREGATE_TRANSFER",
                    suffix.lstrip("."),
                    (
                        f"first-party {suffix} totals {compressed_size:,} gzip bytes, "
                        f"above the {compressed_budget:,}-byte site transfer proxy"
                    ),
                )
            self.stats[f"{suffix.lstrip('.')} gzip bytes"] = compressed_size

        optimized_images = sorted(
            relative
            for relative in self.files
            if relative.startswith("assets/optimized/")
            and Path(relative).suffix.lower() in RASTER_SUFFIXES
        )
        for relative in optimized_images:
            path = self.root / relative
            suffix = path.suffix.lower()
            size = path.stat().st_size
            budget = OPTIMIZED_IMAGE_BUDGETS.get(suffix)
            if budget is None:
                self.error(
                    "PERF_OPTIMIZED_FORMAT",
                    relative,
                    "optimized images must use AVIF or WebP",
                )
                continue
            if size > budget:
                self.error(
                    "PERF_OPTIMIZED_IMAGE_SIZE",
                    relative,
                    (
                        f"{size:,} bytes exceeds the {budget:,}-byte "
                        f"{suffix.lstrip('.').upper()} budget"
                    ),
                )
            dimensions = self._get_image_dimensions(relative, 1, relative)
            if dimensions and max(dimensions) > 1200:
                self.error(
                    "PERF_OPTIMIZED_IMAGE_DIMENSIONS",
                    relative,
                    (
                        f"{dimensions[0]}×{dimensions[1]} exceeds the 1,200px "
                        "optimized-source ceiling"
                    ),
                )
            width_marker = re.search(r"-(\d{3,4})\.(?:avif|webp)$", relative)
            if (
                width_marker
                and dimensions
                and dimensions[0] != int(width_marker.group(1))
            ):
                self.error(
                    "PERF_OPTIMIZED_FILENAME",
                    relative,
                    (
                        f"filename promises {width_marker.group(1)}px width, but "
                        f"the image is {dimensions[0]}px wide"
                    ),
                )
        self.stats["optimized_images"] = len(optimized_images)

    def _validate_declared_ratio(
        self,
        page_path: str,
        line: int,
        image_path: str,
        declared_width: int,
        declared_height: int,
    ) -> None:
        dimensions = self._get_image_dimensions(page_path, line, image_path)
        if not dimensions:
            return
        actual_width, actual_height = dimensions
        declared_ratio = declared_width / declared_height
        actual_ratio = actual_width / actual_height
        if not math.isclose(declared_ratio, actual_ratio, rel_tol=0.005):
            self.error(
                "IMAGE_ASPECT_RATIO",
                page_path,
                (
                    f"declared {declared_width}×{declared_height} ratio does not match "
                    f"{image_path} ({actual_width}×{actual_height})"
                ),
                line,
            )

    def _validate_srcset(
        self, page_path: str, element: Element, srcset: str
    ) -> None:
        try:
            candidates = parse_srcset(srcset)
        except ValueError as exc:
            self.error("SRCSET_FORMAT", page_path, str(exc), element.line)
            return
        if not candidates:
            self.error(
                "SRCSET_EMPTY", page_path, "srcset has no candidates", element.line
            )
            return
        descriptors: set[str] = set()
        descriptor_kinds: set[str] = set()
        for url, descriptor in candidates:
            if descriptor:
                if not re.fullmatch(r"(?:[1-9]\d*)w|(?:\d+(?:\.\d+)?)x", descriptor):
                    self.error(
                        "SRCSET_DESCRIPTOR",
                        page_path,
                        f"invalid srcset descriptor {descriptor!r}",
                        element.line,
                    )
                kind = descriptor[-1]
                descriptor_kinds.add(kind)
                if descriptor in descriptors:
                    self.error(
                        "SRCSET_DUPLICATE",
                        page_path,
                        f"srcset repeats descriptor {descriptor!r}",
                        element.line,
                    )
                descriptors.add(descriptor)
            target = self._resolve_local(
                page_path, url, "srcset candidate", element.line, check_fragment=False
            )
            if target and descriptor and descriptor.endswith("w"):
                dimensions = self._get_image_dimensions(
                    page_path, element.line, target[0]
                )
                expected_width = int(descriptor[:-1])
                if dimensions and dimensions[0] != expected_width:
                    self.error(
                        "SRCSET_WIDTH",
                        page_path,
                        (
                            f"{target[0]} is {dimensions[0]}px wide but its srcset "
                            f"descriptor says {expected_width}w"
                        ),
                        element.line,
                    )
        if len(descriptor_kinds) > 1:
            self.error(
                "SRCSET_MIXED_DESCRIPTORS",
                page_path,
                "srcset must not mix width and density descriptors",
                element.line,
            )

    def _validate_open_graph(
        self,
        page: HtmlPage,
        indexable: bool,
        title: str,
        description: str,
        canonical: str,
    ) -> None:
        required = indexable
        values: dict[str, tuple[str, int] | None] = {}
        for name in (
            "og:site_name",
            "og:title",
            "og:description",
            "og:url",
            "og:type",
            "og:image",
            "og:image:alt",
            "og:image:width",
            "og:image:height",
        ):
            values[name] = self._single_meta(
                page, "property", name, required=required
            )
        if not required:
            return
        if values["og:url"] and canonical and values["og:url"][0] != canonical:
            self.error(
                "OG_URL",
                page.path,
                f"og:url must match canonical {canonical}",
                values["og:url"][1],
            )
        # Open Graph copy may intentionally differ from search-result copy.
        # Presence, uniqueness, canonical URL alignment, and image integrity are
        # enforceable; copy equality is not a quality invariant.
        image_entry = values["og:image"]
        width_entry = values["og:image:width"]
        height_entry = values["og:image:height"]
        if image_entry and width_entry and height_entry:
            target = self._resolve_local(
                page.path,
                image_entry[0],
                "Open Graph image",
                image_entry[1],
                check_fragment=False,
            )
            if not is_positive_integer(width_entry[0]) or not is_positive_integer(
                height_entry[0]
            ):
                self.error(
                    "OG_IMAGE_DIMENSIONS",
                    page.path,
                    "og:image:width and og:image:height must be positive integers",
                    width_entry[1],
                )
            elif target:
                dimensions = self._get_image_dimensions(
                    page.path, image_entry[1], target[0]
                )
                declared = (int(width_entry[0]), int(height_entry[0]))
                if dimensions and dimensions != declared:
                    self.error(
                        "OG_IMAGE_DIMENSIONS",
                        page.path,
                        (
                            f"Open Graph dimensions {declared[0]}×{declared[1]} do not "
                            f"match {target[0]} ({dimensions[0]}×{dimensions[1]})"
                        ),
                        width_entry[1],
                    )

        og_type = values["og:type"][0] if values["og:type"] else ""
        if og_type in {"music.album", "music.song"}:
            for musician_url, musician_line in page.meta(
                "property", "music:musician"
            ):
                target = self._resolve_local(
                    page.path,
                    musician_url,
                    "Open Graph musician",
                    musician_line,
                    check_fragment=False,
                )
                if target:
                    target_page = self.pages.get(target[0])
                    target_types = (
                        target_page.meta("property", "og:type")
                        if target_page
                        else []
                    )
                    if [entry[0] for entry in target_types] != ["profile"]:
                        self.error(
                            "OG_MUSICIAN_PROFILE",
                            page.path,
                            (
                                "music:musician must identify a local Open Graph "
                                f"profile object; {musician_url} does not"
                            ),
                            musician_line,
                        )

        if og_type == "music.album":
            for song_url, song_line in page.meta("property", "music:song"):
                target = self._resolve_local(
                    page.path,
                    song_url,
                    "Open Graph song",
                    song_line,
                    check_fragment=False,
                )
                if target:
                    target_page = self.pages.get(target[0])
                    target_types = (
                        target_page.meta("property", "og:type")
                        if target_page
                        else []
                    )
                    if [entry[0] for entry in target_types] != ["music.song"]:
                        self.error(
                            "OG_SONG_OBJECT",
                            page.path,
                            (
                                "music:song must identify a local Open Graph "
                                f"music.song object; {song_url} does not"
                            ),
                            song_line,
                        )

    def _validate_json_ld(
        self, page: HtmlPage, indexable: bool, canonical: str
    ) -> None:
        if indexable and not page.json_ld:
            self.error(
                "JSONLD_MISSING",
                page.path,
                "indexable pages must include JSON-LD structured data",
            )
            return
        canonical_in_schema = False
        for raw, line in page.json_ld:
            if not raw:
                self.error(
                    "JSONLD_EMPTY", page.path, "JSON-LD script is empty", line
                )
                continue
            try:
                data = load_json_text(raw)
            except (json.JSONDecodeError, DuplicateKeyError) as exc:
                self.error(
                    "JSONLD_INVALID",
                    page.path,
                    f"invalid JSON-LD: {exc}",
                    line,
                )
                continue
            roots = data if isinstance(data, list) else [data]
            if not all(isinstance(root, dict) for root in roots):
                self.error(
                    "JSONLD_ROOT",
                    page.path,
                    "JSON-LD root must be an object or an array of objects",
                    line,
                )
                continue
            for root in roots:
                context = root.get("@context")
                if context not in SCHEMA_CONTEXTS:
                    self.error(
                        "JSONLD_CONTEXT",
                        page.path,
                        "each JSON-LD root must use https://schema.org",
                        line,
                    )
                for node in walk_json_objects(root):
                    raw_types = node.get("@type")
                    node_types = (
                        raw_types if isinstance(raw_types, list) else [raw_types]
                    )
                    if "MusicAlbum" in node_types and "duration" in node:
                        self.error(
                            "JSONLD_MUSIC_ALBUM_DURATION",
                            page.path,
                            (
                                "MusicAlbum must not declare duration; model the "
                                "release duration on MusicRelease instead"
                            ),
                            line,
                        )
                for key, value in walk_json(root):
                    if key == "url" and value == canonical:
                        canonical_in_schema = True
                    if key in {"url", "item"} and isinstance(value, str):
                        self._resolve_local(
                            page.path,
                            value,
                            f"JSON-LD {key}",
                            line,
                            check_fragment=True,
                        )
                    elif key in {"image", "logo", "thumbnailUrl"} and isinstance(
                        value, str
                    ):
                        self._resolve_local(
                            page.path,
                            value,
                            f"JSON-LD {key}",
                            line,
                            check_fragment=False,
                        )
                    elif key == "@id" and isinstance(value, str):
                        fragment = unquote(urlsplit(value).fragment)
                        if "#" in fragment:
                            self.error(
                                "JSONLD_ID_FRAGMENT",
                                page.path,
                                (
                                    "JSON-LD @id contains more than one fragment "
                                    f"separator: {value}"
                                ),
                                line,
                            )
                        self._resolve_local(
                            page.path,
                            value,
                            "JSON-LD @id",
                            line,
                            check_fragment=False,
                        )
        if indexable and canonical and not canonical_in_schema:
            self.error(
                "JSONLD_CANONICAL",
                page.path,
                "JSON-LD must include the page canonical in a url property",
            )

    def _validate_references(self) -> None:
        for page_path, page in sorted(self.pages.items()):
            for element in page.elements:
                for tag, attribute in URL_ATTRIBUTES:
                    if element.tag != tag or attribute not in element.attrs:
                        continue
                    value = (element.attrs.get(attribute) or "").strip()
                    if not value:
                        self.error(
                            "URL_EMPTY",
                            page_path,
                            f"<{tag}> has an empty {attribute}",
                            element.line,
                        )
                        continue
                    check_fragment = tag in {"a", "area"}
                    target = self._resolve_local(
                        page_path,
                        value,
                        f"<{tag}> {attribute}",
                        element.line,
                        check_fragment=check_fragment,
                    )
                    if target and tag in {"a", "area"}:
                        self.inbound_links[target[0]].add(page_path)

                if "srcset" in element.attrs:
                    # Candidate existence and dimensions are checked with images.
                    continue

    def _resolve_local(
        self,
        source_path: str,
        raw_url: str,
        context: str,
        line: int,
        *,
        check_fragment: bool,
    ) -> tuple[str, str] | None:
        cache_key = (source_path, raw_url, check_fragment)
        # Repeated references are common. Keep errors deterministic but avoid
        # emitting the same resource error many times from one source page.
        already_validated = cache_key in self._validated_resources
        self._validated_resources.add(cache_key)

        if "\\" in raw_url:
            if not already_validated:
                self.error(
                    "URL_BACKSLASH",
                    source_path,
                    f"{context} URL must use forward slashes: {raw_url!r}",
                    line,
                )
            return None
        parsed = urlsplit(raw_url)
        scheme = parsed.scheme.lower()
        if scheme in SKIPPED_SCHEMES:
            return None
        if scheme in {"javascript", "vbscript"}:
            if not already_validated:
                self.error(
                    "URL_UNSAFE_SCHEME",
                    source_path,
                    f"{context} must not use {scheme}: URLs",
                    line,
                )
            return None
        if scheme and scheme not in {"http", "https"}:
            return None

        if parsed.netloc:
            if parsed.hostname != SITE_HOST:
                return None
            if scheme != "https":
                if not already_validated:
                    self.error(
                        "URL_INTERNAL_HTTP",
                        source_path,
                        f"internal {context} must use HTTPS: {raw_url}",
                        line,
                    )
            if parsed.username or parsed.password or parsed.port:
                if not already_validated:
                    self.error(
                        "URL_INTERNAL_AUTHORITY",
                        source_path,
                        f"internal {context} must not use credentials or a port",
                        line,
                    )

        decoded_path = unquote(parsed.path)
        fragment = unquote(parsed.fragment)
        if "\x00" in decoded_path or "\x00" in fragment:
            if not already_validated:
                self.error(
                    "URL_NUL",
                    source_path,
                    f"{context} contains an encoded NUL byte",
                    line,
                )
            return None

        if not decoded_path:
            relative = source_path
        elif decoded_path.startswith("/"):
            relative = decoded_path.lstrip("/")
        else:
            relative = posixpath.join(posixpath.dirname(source_path), decoded_path)
        relative = posixpath.normpath(relative)
        if relative in {"", "."}:
            relative = "index.html"
        if relative == ".." or relative.startswith("../"):
            if not already_validated:
                self.error(
                    "URL_ESCAPE",
                    source_path,
                    f"{context} escapes the site root: {raw_url!r}",
                    line,
                )
            return None
        if decoded_path.endswith("/") and decoded_path != "/":
            relative = f"{relative.rstrip('/')}/index.html"

        if relative not in self.files:
            if not already_validated:
                case_match = next(
                    (
                        candidate
                        for candidate in self.files
                        if candidate.casefold() == relative.casefold()
                    ),
                    None,
                )
                if case_match:
                    self.error(
                        "URL_CASE",
                        source_path,
                        (
                            f"{context} uses {relative!r}, but the exact on-disk path "
                            f"is {case_match!r}"
                        ),
                        line,
                    )
                else:
                    self.error(
                        "URL_MISSING",
                        source_path,
                        f"{context} points to missing local file {relative!r}",
                        line,
                    )
            return None

        if (
            check_fragment
            and fragment
            and not fragment.startswith(":~:text=")
            and relative.lower().endswith(".html")
        ):
            target_page = self.pages.get(relative)
            target_ids = (
                {identifier for identifier, _ in target_page.ids}
                if target_page
                else set()
            )
            if fragment not in target_ids and not already_validated:
                self.error(
                    "FRAGMENT_MISSING",
                    source_path,
                    (
                        f"{context} points to #{fragment}, which does not exist in "
                        f"{relative}"
                    ),
                    line,
                )
        return relative, fragment

    def _validate_styles_and_scripts(self) -> None:
        css_url = re.compile(
            r"url\(\s*(?P<quote>['\"]?)(?P<url>.*?)(?P=quote)\s*\)",
            flags=re.IGNORECASE,
        )
        css_import = re.compile(
            r"@import\s+(?:url\(\s*)?['\"](?P<url>[^'\"]+)['\"]",
            flags=re.IGNORECASE,
        )
        js_local = re.compile(
            r"\b(?:fetch|import)\(\s*(['\"])(?P<url>[^'\"]+)\1",
        )
        for relative in sorted(self.files):
            if relative.startswith(("scripts/", ".github/")):
                continue
            suffix = Path(relative).suffix.lower()
            if suffix not in {".css", ".js"}:
                continue
            try:
                raw = (self.root / relative).read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                self.error(
                    "ASSET_UTF8", relative, f"is not valid UTF-8: {exc}"
                )
                continue
            if suffix == ".css":
                matches = [
                    (match.group("url"), raw.count("\n", 0, match.start()) + 1)
                    for match in css_url.finditer(raw)
                ]
                matches.extend(
                    (
                        match.group("url"),
                        raw.count("\n", 0, match.start()) + 1,
                    )
                    for match in css_import.finditer(raw)
                )
                for url, line in matches:
                    if url.startswith("#"):
                        continue
                    self._resolve_local(
                        relative,
                        url,
                        "CSS resource",
                        line,
                        check_fragment=False,
                    )
            else:
                for match in js_local.finditer(raw):
                    url = match.group("url")
                    line = raw.count("\n", 0, match.start()) + 1
                    self._resolve_local(
                        relative,
                        url,
                        "JavaScript resource",
                        line,
                        check_fragment=False,
                    )

    def _validate_json_files(self) -> None:
        json_files = sorted(
            relative
            for relative in self.files
            if relative.endswith(".json")
            and not relative.startswith((".github/", "scripts/"))
        )
        for relative in json_files:
            try:
                raw = (self.root / relative).read_text(encoding="utf-8")
                load_json_text(raw)
            except UnicodeDecodeError as exc:
                self.error("JSON_UTF8", relative, f"is not valid UTF-8: {exc}")
            except (json.JSONDecodeError, DuplicateKeyError) as exc:
                self.error("JSON_INVALID", relative, f"invalid JSON: {exc}")
        self.stats["json_files"] = len(json_files)

    def _load_json_file(self, relative: str) -> Any | None:
        if relative not in self.files:
            self.error("DATA_FILE_MISSING", relative, "required data file is missing")
            return None
        try:
            return load_json_text((self.root / relative).read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, DuplicateKeyError) as exc:
            # The general JSON pass already reports this, but retain a focused
            # message if this method is used independently in the future.
            if not any(
                issue.path == relative and issue.code in {"JSON_UTF8", "JSON_INVALID"}
                for issue in self.issues
            ):
                self.error("JSON_INVALID", relative, f"invalid JSON: {exc}")
            return None

    def _validate_music_data(self) -> None:
        runtime_required = {
            "music-catalog.json",
            "music-lyrics.json",
        }
        runtime_present = runtime_required.intersection(self.files)
        if not runtime_present:
            return
        if not runtime_required.issubset(self.files):
            for missing in sorted(runtime_required - runtime_present):
                self.error(
                    "MUSIC_DATA_PARTIAL",
                    missing,
                    "runtime music data files must be deployed as a complete set",
                )
            return

        metadata_path = "music-release-metadata.json"
        is_authoring_tree = "scripts/build_music_pages.rb" in self.files
        if is_authoring_tree and metadata_path not in self.files:
            self.error(
                "MUSIC_DATA_PARTIAL",
                metadata_path,
                "the authoring tree requires release-generation metadata",
            )
            return

        catalog = self._load_json_file("music-catalog.json")
        lyrics = self._load_json_file("music-lyrics.json")
        metadata = (
            self._load_json_file(metadata_path)
            if metadata_path in self.files
            else None
        )
        if not isinstance(catalog, dict) or not isinstance(lyrics, dict):
            return
        if metadata is not None and not isinstance(metadata, dict):
            return

        if metadata is not None:
            catalog_keys = list(catalog)
            metadata_keys = list(metadata)
            if catalog_keys != metadata_keys:
                self.error(
                    "MUSIC_RELEASE_ORDER",
                    metadata_path,
                    "release keys must exactly match music-catalog.json in the same order",
                )
        if not catalog:
            self.error(
                "MUSIC_CATALOG_EMPTY",
                "music-catalog.json",
                "catalog must contain at least one release",
            )

        source_owners: dict[str, str] = {}
        valid_tracks: set[str] = set()
        descriptions: dict[str, str] = {}
        music_page = self.pages.get("music.html")
        music_ids = Counter(
            identifier for identifier, _ in music_page.ids
        ) if music_page else Counter()
        player_ids = Counter(
            (element.attrs.get("data-release-player") or "")
            for element in (music_page.elements if music_page else [])
            if "data-release-player" in element.attrs
        )
        music_images = (
            [
                element
                for element in music_page.elements_named("img")
            ]
            if music_page
            else []
        )

        track_count = 0
        for release_id, release in catalog.items():
            location = f"music-catalog.json:{release_id}"
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", release_id):
                self.error(
                    "MUSIC_RELEASE_ID",
                    "music-catalog.json",
                    f"release id {release_id!r} must be lowercase kebab-case",
                )
            if not isinstance(release, dict):
                self.error(
                    "MUSIC_RELEASE_TYPE",
                    "music-catalog.json",
                    f"{location} must be an object",
                )
                continue
            artist = release.get("artist")
            if not isinstance(artist, str) or not artist.strip():
                self.error(
                    "MUSIC_ARTIST", "music-catalog.json", f"{location} has no artist"
                )
            title = release.get("title")
            if not isinstance(title, str) or not title.strip():
                self.error(
                    "MUSIC_TITLE", "music-catalog.json", f"{location} has no title"
                )
            tracks = release.get("tracks")
            if not isinstance(tracks, list) or not tracks:
                self.error(
                    "MUSIC_TRACKS",
                    "music-catalog.json",
                    f"{location} must contain a non-empty tracks array",
                )
                continue

            previous_number = 0
            seen_numbers: set[int] = set()
            for index, track in enumerate(tracks):
                track_count += 1
                if not isinstance(track, dict):
                    self.error(
                        "MUSIC_TRACK_TYPE",
                        "music-catalog.json",
                        f"{location} track {index + 1} must be an object",
                    )
                    continue
                number = track.get("number")
                if not isinstance(number, int) or isinstance(number, bool) or number < 1:
                    self.error(
                        "MUSIC_TRACK_NUMBER",
                        "music-catalog.json",
                        f"{location} track {index + 1} has invalid number {number!r}",
                    )
                    continue
                if number in seen_numbers or number <= previous_number:
                    self.error(
                        "MUSIC_TRACK_ORDER",
                        "music-catalog.json",
                        f"{location} track numbers must be unique and increasing",
                    )
                seen_numbers.add(number)
                previous_number = number
                track_key = f"{release_id}:{number}"
                valid_tracks.add(track_key)
                track_title = track.get("title")
                if not isinstance(track_title, str) or not track_title.strip():
                    self.error(
                        "MUSIC_TRACK_TITLE",
                        "music-catalog.json",
                        f"{track_key} has no title",
                    )
                duration = track.get("duration")
                if (
                    not isinstance(duration, (int, float))
                    or isinstance(duration, bool)
                    or not math.isfinite(duration)
                    or duration <= 0
                ):
                    self.error(
                        "MUSIC_DURATION",
                        "music-catalog.json",
                        f"{track_key} has invalid duration {duration!r}",
                    )
                source = track.get("source")
                parsed_source = urlsplit(source) if isinstance(source, str) else None
                if (
                    not parsed_source
                    or parsed_source.scheme != "https"
                    or not parsed_source.netloc
                ):
                    self.error(
                        "MUSIC_SOURCE",
                        "music-catalog.json",
                        f"{track_key} source must be an absolute HTTPS URL",
                    )
                elif source in source_owners:
                    self.error(
                        "MUSIC_SOURCE_DUPLICATE",
                        "music-catalog.json",
                        f"{track_key} reuses the source from {source_owners[source]}",
                    )
                else:
                    source_owners[source] = track_key
                for optional_text in ("credits", "lyricsCredit"):
                    if optional_text in track and (
                        not isinstance(track[optional_text], str)
                        or not track[optional_text].strip()
                    ):
                        self.error(
                            "MUSIC_OPTIONAL_TEXT",
                            "music-catalog.json",
                            f"{track_key} {optional_text} must be non-empty when present",
                        )
                if music_page and music_ids[f"{release_id}-track-{number}"] != 1:
                    self.error(
                        "MUSIC_TRACK_HTML",
                        "music.html",
                        f"{track_key} must have exactly one crawlable fallback track id",
                    )

            if music_page and music_ids[f"{release_id}-download"] != 1:
                self.error(
                    "MUSIC_RELEASE_HTML",
                    "music.html",
                    f"{release_id} must have exactly one release anchor",
                )
            if music_page and player_ids[release_id] != 1:
                self.error(
                    "MUSIC_PLAYER_HTML",
                    "music.html",
                    f"{release_id} must have exactly one player mount",
                )

            if metadata is None:
                continue
            release_meta = metadata.get(release_id)
            if not isinstance(release_meta, dict):
                self.error(
                    "MUSIC_METADATA",
                    "music-release-metadata.json",
                    f"{release_id} must have a metadata object",
                )
                continue
            raw_date = release_meta.get("date")
            try:
                if not isinstance(raw_date, str):
                    raise ValueError
                date.fromisoformat(raw_date)
            except ValueError:
                self.error(
                    "MUSIC_DATE",
                    "music-release-metadata.json",
                    f"{release_id} date must use YYYY-MM-DD",
                )
            description = release_meta.get("description")
            if not isinstance(description, str) or not description.strip():
                self.error(
                    "MUSIC_DESCRIPTION",
                    "music-release-metadata.json",
                    f"{release_id} needs a non-empty description",
                )
            elif description.casefold() in descriptions:
                self.warning(
                    "MUSIC_DESCRIPTION_DUPLICATE",
                    "music-release-metadata.json",
                    (
                        f"{release_id} duplicates the description for "
                        f"{descriptions[description.casefold()]}"
                    ),
                )
            else:
                descriptions[description.casefold()] = release_id
            artwork = release_meta.get("artwork")
            artwork_alt = release_meta.get("artworkAlt")
            if not isinstance(artwork, str) or not artwork.startswith("/"):
                self.error(
                    "MUSIC_ARTWORK",
                    "music-release-metadata.json",
                    f"{release_id} artwork must be a root-relative path",
                )
            else:
                self._resolve_local(
                    "music-release-metadata.json",
                    artwork,
                    f"{release_id} artwork",
                    1,
                    check_fragment=False,
                )
            if not isinstance(artwork_alt, str) or not artwork_alt.strip():
                self.error(
                    "MUSIC_ARTWORK_ALT",
                    "music-release-metadata.json",
                    f"{release_id} needs meaningful artworkAlt text",
                )
            if music_page:
                matching_images = [
                    image
                    for image in music_images
                    if image.attrs.get("src") == artwork
                    and image.attrs.get("alt") == artwork_alt
                ]
                if len(matching_images) != 1:
                    self.error(
                        "MUSIC_ARTWORK_HTML",
                        "music.html",
                        (
                            f"{release_id} metadata artwork and alt text must match "
                            "exactly one catalog image"
                        ),
                    )

            if "schemaTrackCount" in release_meta:
                schema_track_count = release_meta["schemaTrackCount"]
                if schema_track_count is not None and (
                    not isinstance(schema_track_count, int)
                    or isinstance(schema_track_count, bool)
                    or schema_track_count < 1
                ):
                    self.error(
                        "MUSIC_SCHEMA_TRACK_COUNT",
                        "music-release-metadata.json",
                        f"{release_id} schemaTrackCount must be positive, null, or omitted",
                    )
            if "schemaDuration" in release_meta:
                schema_duration = release_meta["schemaDuration"]
                if schema_duration is not None and (
                    not isinstance(schema_duration, str)
                    or not re.fullmatch(
                        r"P(?=\d|T\d)(?:\d+Y)?(?:\d+M)?(?:\d+D)?(?:T(?=\d)(?:\d+H)?(?:\d+M)?(?:\d+(?:\.\d+)?S)?)?",
                        schema_duration,
                    )
                ):
                    self.error(
                        "MUSIC_SCHEMA_DURATION",
                        "music-release-metadata.json",
                        f"{release_id} schemaDuration is not an ISO 8601 duration",
                    )

        for key, lyric in lyrics.items():
            if key not in valid_tracks:
                self.error(
                    "MUSIC_LYRIC_ORPHAN",
                    "music-lyrics.json",
                    f"lyrics key {key!r} does not match a catalog track",
                )
            if not isinstance(lyric, str) or not lyric.strip():
                self.error(
                    "MUSIC_LYRIC_EMPTY",
                    "music-lyrics.json",
                    f"lyrics key {key!r} must contain non-empty text",
                )

        self.stats["releases"] = len(catalog)
        self.stats["tracks"] = track_count
        self.stats["lyrics"] = len(lyrics)

    def _validate_manifest(self) -> None:
        relative = "site.webmanifest"
        if relative not in self.files:
            self.error("MANIFEST_MISSING", relative, "web app manifest is missing")
            return
        try:
            manifest = load_json_text(
                (self.root / relative).read_text(encoding="utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError, DuplicateKeyError) as exc:
            self.error("MANIFEST_INVALID", relative, f"invalid manifest JSON: {exc}")
            return
        if not isinstance(manifest, dict):
            self.error("MANIFEST_ROOT", relative, "manifest root must be an object")
            return
        for key in ("name", "short_name", "description", "start_url", "display"):
            if not isinstance(manifest.get(key), str) or not manifest[key].strip():
                self.error(
                    "MANIFEST_FIELD", relative, f"manifest needs non-empty {key}"
                )
        start_url = manifest.get("start_url")
        if isinstance(start_url, str):
            self._resolve_local(
                relative,
                start_url,
                "manifest start_url",
                1,
                check_fragment=False,
            )
        for color_key in ("background_color", "theme_color"):
            color = manifest.get(color_key)
            if not isinstance(color, str) or not re.fullmatch(
                r"#[0-9a-fA-F]{6}", color
            ):
                self.error(
                    "MANIFEST_COLOR",
                    relative,
                    f"{color_key} must be a six-digit hexadecimal color",
                )
        icons = manifest.get("icons")
        if not isinstance(icons, list) or not icons:
            self.error(
                "MANIFEST_ICONS", relative, "manifest needs at least one icon"
            )
            return
        for index, icon in enumerate(icons, start=1):
            if not isinstance(icon, dict):
                self.error(
                    "MANIFEST_ICON",
                    relative,
                    f"icon {index} must be an object",
                )
                continue
            src = icon.get("src")
            sizes = icon.get("sizes")
            if not isinstance(src, str) or not src:
                self.error(
                    "MANIFEST_ICON_SRC",
                    relative,
                    f"icon {index} needs a non-empty src",
                )
                continue
            target = self._resolve_local(
                relative, src, "manifest icon", 1, check_fragment=False
            )
            if not isinstance(sizes, str) or not sizes:
                self.error(
                    "MANIFEST_ICON_SIZES",
                    relative,
                    f"icon {index} needs sizes",
                )
                continue
            if target and sizes != "any":
                dimensions = self._get_image_dimensions(relative, 1, target[0])
                declared_sizes: set[tuple[int, int]] = set()
                for token in sizes.split():
                    match = re.fullmatch(r"([1-9]\d*)x([1-9]\d*)", token)
                    if not match:
                        self.error(
                            "MANIFEST_ICON_SIZES",
                            relative,
                            f"icon {index} has invalid size {token!r}",
                        )
                    else:
                        declared_sizes.add((int(match[1]), int(match[2])))
                if dimensions and dimensions not in declared_sizes:
                    self.error(
                        "MANIFEST_ICON_DIMENSIONS",
                        relative,
                        (
                            f"{target[0]} is {dimensions[0]}×{dimensions[1]}, which "
                            f"is absent from declared sizes {sizes!r}"
                        ),
                    )

    def _validate_sitemap(self) -> None:
        relative = "sitemap.xml"
        if relative not in self.files:
            self.error("SITEMAP_MISSING", relative, "sitemap is missing")
            return
        try:
            tree = ElementTree.parse(self.root / relative)
        except ElementTree.ParseError as exc:
            self.error("SITEMAP_XML", relative, f"invalid XML: {exc}")
            return
        root = tree.getroot()
        if root.tag != f"{{{SITEMAP_NS}}}urlset":
            self.error(
                "SITEMAP_ROOT",
                relative,
                "root must be the standard sitemap <urlset>",
            )
            return

        locations: dict[str, str] = {}
        for url_element in root.findall(f"{{{SITEMAP_NS}}}url"):
            loc_elements = url_element.findall(f"{{{SITEMAP_NS}}}loc")
            if len(loc_elements) != 1 or not (loc_elements[0].text or "").strip():
                self.error(
                    "SITEMAP_LOC",
                    relative,
                    "each sitemap URL needs exactly one non-empty loc",
                )
                continue
            loc = (loc_elements[0].text or "").strip()
            if loc in locations:
                self.error(
                    "SITEMAP_DUPLICATE",
                    relative,
                    f"duplicate sitemap URL {loc}",
                )
                continue
            target = self._resolve_local(
                relative, loc, "sitemap loc", 1, check_fragment=False
            )
            if urlsplit(loc).fragment or urlsplit(loc).query:
                self.error(
                    "SITEMAP_LOC_COMPONENTS",
                    relative,
                    f"sitemap URL must not contain query or fragment: {loc}",
                )
            if target:
                target_path = target[0]
                locations[loc] = target_path
                if not self.page_indexability.get(target_path, False):
                    self.error(
                        "SITEMAP_NOINDEX",
                        relative,
                        f"sitemap includes non-indexable page {loc}",
                    )
                page_canonical = self.page_canonicals.get(target_path)
                if page_canonical != loc:
                    self.error(
                        "SITEMAP_CANONICAL",
                        relative,
                        (
                            f"{loc} does not match {target_path}'s canonical "
                            f"{page_canonical!r}"
                        ),
                    )

            lastmods = url_element.findall(f"{{{SITEMAP_NS}}}lastmod")
            if len(lastmods) > 1:
                self.error(
                    "SITEMAP_LASTMOD_DUPLICATE",
                    relative,
                    f"{loc} has multiple lastmod entries",
                )
            if lastmods:
                raw_lastmod = (lastmods[0].text or "").strip()
                if not valid_w3c_datetime(raw_lastmod):
                    self.error(
                        "SITEMAP_LASTMOD",
                        relative,
                        f"{loc} has invalid W3C lastmod {raw_lastmod!r}",
                    )

            for image_element in url_element.findall(
                f"{{{IMAGE_SITEMAP_NS}}}image"
            ):
                unsupported_children = [
                    child.tag
                    for child in image_element
                    if child.tag != f"{{{IMAGE_SITEMAP_NS}}}loc"
                ]
                if unsupported_children:
                    unsupported_names = {
                        tag.rsplit("}", 1)[-1] for tag in unsupported_children
                    }
                    self.error(
                        "SITEMAP_IMAGE_FIELDS",
                        relative,
                        (
                            f"{loc} uses unsupported image sitemap field"
                            f"{'s' if len(unsupported_names) != 1 else ''}: "
                            f"{', '.join(sorted(unsupported_names))}; retain only "
                            "image:loc"
                        ),
                    )
                image_locs = image_element.findall(
                    f"{{{IMAGE_SITEMAP_NS}}}loc"
                )
                if len(image_locs) != 1 or not (
                    image_locs[0].text or ""
                ).strip():
                    self.error(
                        "SITEMAP_IMAGE_LOC",
                        relative,
                        f"{loc} has an image entry without exactly one loc",
                    )
                    continue
                image_url = (image_locs[0].text or "").strip()
                if urlsplit(image_url).scheme != "https":
                    self.error(
                        "SITEMAP_IMAGE_HTTPS",
                        relative,
                        f"sitemap image must use HTTPS: {image_url}",
                    )
                self._resolve_local(
                    relative,
                    image_url,
                    "sitemap image",
                    1,
                    check_fragment=False,
                )

        expected = {
            canonical
            for path, canonical in self.page_canonicals.items()
            if self.page_indexability.get(path, False)
        }
        actual = set(locations)
        for missing in sorted(expected - actual):
            self.error(
                "SITEMAP_PAGE_MISSING",
                relative,
                f"indexable canonical is absent from sitemap: {missing}",
            )
        for extra in sorted(actual - expected):
            self.error(
                "SITEMAP_PAGE_EXTRA",
                relative,
                f"sitemap URL is not an indexable canonical: {extra}",
            )
        self.stats["sitemap_urls"] = len(actual)

    def _validate_robots(self) -> None:
        relative = "robots.txt"
        if relative not in self.files:
            self.error("ROBOTS_MISSING", relative, "robots.txt is missing")
            return
        raw = (self.root / relative).read_text(encoding="utf-8")
        groups: list[dict[str, list[str]]] = []
        current: dict[str, list[str]] | None = None
        sitemaps: list[str] = []
        for line_number, raw_line in enumerate(raw.splitlines(), start=1):
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            if ":" not in line:
                self.error(
                    "ROBOTS_DIRECTIVE",
                    relative,
                    f"directive lacks a colon: {raw_line!r}",
                    line_number,
                )
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "sitemap":
                sitemaps.append(value)
                continue
            if key == "user-agent":
                if current is None or any(
                    directive in current for directive in ("allow", "disallow")
                ):
                    current = defaultdict(list)
                    groups.append(current)
                current["user-agent"].append(value)
                continue
            if key not in {"allow", "disallow", "crawl-delay"}:
                self.warning(
                    "ROBOTS_UNKNOWN",
                    relative,
                    f"unrecognized robots directive {key!r}",
                    line_number,
                )
            if current is None:
                self.error(
                    "ROBOTS_GROUP",
                    relative,
                    f"{key} appears before any User-agent",
                    line_number,
                )
                continue
            current[key].append(value)

        default_groups = [
            group
            for group in groups
            if "*" in [agent.strip() for agent in group.get("user-agent", [])]
        ]
        if len(default_groups) != 1:
            self.error(
                "ROBOTS_DEFAULT_GROUP",
                relative,
                f"robots.txt must have exactly one User-agent: * group; found {len(default_groups)}",
            )
        else:
            group = default_groups[0]
            if "/" in [value.strip() for value in group.get("disallow", [])]:
                self.error(
                    "ROBOTS_BLOCKS_SITE",
                    relative,
                    "default crawler group must not disallow the entire site",
                )
            if "/" not in [value.strip() for value in group.get("allow", [])]:
                self.error(
                    "ROBOTS_ALLOW",
                    relative,
                    "default crawler group must explicitly allow /",
                )
        expected_sitemap = f"{SITE_ORIGIN}/sitemap.xml"
        if sitemaps != [expected_sitemap]:
            self.error(
                "ROBOTS_SITEMAP",
                relative,
                f"robots.txt must declare exactly `Sitemap: {expected_sitemap}`",
            )

    def _validate_cname(self) -> None:
        relative = "CNAME"
        if relative not in self.files:
            self.error("CNAME_MISSING", relative, "GitHub Pages CNAME is missing")
            return
        value = (self.root / relative).read_text(encoding="utf-8").strip()
        if value != SITE_HOST:
            self.error(
                "CNAME_VALUE",
                relative,
                f"CNAME must contain exactly {SITE_HOST!r}; found {value!r}",
            )

    def _validate_orphans(self) -> None:
        for path, indexable in sorted(self.page_indexability.items()):
            if not indexable or path == "index.html":
                continue
            if not self.inbound_links.get(path):
                self.error(
                    "PAGE_ORPHAN",
                    path,
                    "indexable page has no crawlable inbound HTML link",
                )

    def _get_image_dimensions(
        self, source_path: str, line: int, image_path: str
    ) -> tuple[int, int] | None:
        suffix = Path(image_path).suffix.lower()
        if suffix not in RASTER_SUFFIXES:
            return None
        try:
            dimensions = image_dimensions(str(self.root / image_path))
        except (OSError, struct.error, ValueError) as exc:
            self.error(
                "IMAGE_READ",
                source_path,
                f"could not inspect {image_path}: {exc}",
                line,
            )
            return None
        if not dimensions or dimensions[0] <= 0 or dimensions[1] <= 0:
            self.error(
                "IMAGE_FORMAT",
                source_path,
                f"could not determine dimensions for {image_path}",
                line,
            )
            return None
        self.stats["referenced_images"] += 1
        return dimensions


def walk_json(value: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def walk_json_objects(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json_objects(child)


def valid_w3c_datetime(value: str) -> bool:
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            date.fromisoformat(value)
            return True
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T.+", value))
    except ValueError:
        return False


def github_escape(value: str) -> str:
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def emit_results(
    validator: SiteValidator, output_format: str, fail_on_warnings: bool
) -> int:
    issues = sorted(
        validator.issues,
        key=lambda issue: (
            issue.path,
            issue.line,
            issue.severity,
            issue.code,
            issue.message,
        ),
    )
    errors = [issue for issue in issues if issue.severity == "error"]
    warnings = [issue for issue in issues if issue.severity == "warning"]

    if output_format == "json":
        payload = {
            "ok": not errors and not (fail_on_warnings and warnings),
            "stats": dict(sorted(validator.stats.items())),
            "issues": [
                {
                    "severity": issue.severity,
                    "code": issue.code,
                    "path": issue.path,
                    "line": issue.line,
                    "message": issue.message,
                }
                for issue in issues
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        if output_format == "github":
            for issue in issues:
                print(
                    f"::{issue.severity} file={github_escape(issue.path)},"
                    f"line={issue.line},title={github_escape(issue.code)}::"
                    f"{github_escape(issue.message)}"
                )
        else:
            for issue in issues:
                print(
                    f"{issue.severity.upper():7} "
                    f"{issue.path}:{issue.line} [{issue.code}] {issue.message}"
                )

        state = "PASS"
        if errors or (fail_on_warnings and warnings):
            state = "FAIL"
        summary = (
            f"Site quality gate: {state} — {len(errors)} error(s), "
            f"{len(warnings)} warning(s)"
        )
        if validator.stats:
            stats = ", ".join(
                f"{value} {name.replace('_', ' ')}"
                for name, value in sorted(validator.stats.items())
            )
            summary += f"; checked {stats}"
        print(summary)

    return 1 if errors or (fail_on_warnings and warnings) else 0


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the beareab static site without network access."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="site root (defaults to the repository root)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "github", "json"),
        default="text",
        help="diagnostic output format",
    )
    parser.add_argument(
        "--fail-on-warnings",
        action="store_true",
        help="return non-zero when advisory warnings are present",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        print(f"error: site root is not a directory: {root}", file=sys.stderr)
        return 2
    validator = SiteValidator(root)
    validator.run()
    return emit_results(validator, args.format, args.fail_on_warnings)


if __name__ == "__main__":
    raise SystemExit(main())
