# beareab.com

The source for [beareab.com](https://beareab.com): Gabriel Long’s independent music archive for beareab, ucygrx, and related work.

The production site is deliberately framework-free. HTML, CSS, JavaScript, JSON, images, and fonts are served as static files, so the core catalog remains readable, crawlable, and downloadable without a client runtime or package registry.

## Architecture

```text
.
├── index.html                         Hand-authored primary pages
├── music.html                         Generated catalog markup inside a hand-authored shell
├── music/
│   └── <release>/                     Generated release and lyric pages
├── assets/
│   ├── downloads/                     Canonical 700×700 release artwork
│   ├── optimized/                     Responsive derivatives + integrity manifest
│   └── fonts/                         Self-hosted fonts and their licenses
├── music-catalog.json                 Artists, tracks, durations, and audio sources
├── music-release-metadata.json        Release SEO, editorial copy, art, and downloads
├── music-lyrics.json                  Published lyrics keyed by release and track number
├── scripts/
│   ├── build_music_pages.rb           Unified release, lyric, schema, and sitemap generator
│   ├── build_responsive_images.sh     Reproducible responsive-image source command
│   ├── responsive_images.py           Encoder orchestration and hash-integrity check
│   ├── check_site.sh                  Complete local/CI quality gate
│   ├── validate_site.py               Dependency-free whole-site validator
│   └── build_site.py                  Minimal production artifact builder
└── .github/workflows/                 Pull-request validation and gated Pages deployment
```

Generated files carry a source comment where practical. Do not hand-edit files under `music/`, generated track lists in `music.html`, generated music JSON-LD, or the generated sitemap block. Change their JSON sources and rebuild them.

## Local setup

Required:

- Python 3.10 or newer
- Ruby 3.1 or newer

Optional:

- ImageMagick 7 (`magick`) when rebuilding responsive images

There are no npm, pip, Bundler, or other install steps.

Run the full gate before and after a change:

```sh
bash scripts/check_site.sh
```

Preview the site through an HTTP server so root-relative URLs behave like production:

```sh
python3 -m http.server 8080
```

Then open `http://localhost:8080/`. Opening HTML directly from disk is not a representative test because the site uses root-relative resources and `fetch()`.

## Quality gate

`scripts/check_site.sh` is the single entry point used locally and in GitHub Actions. It runs:

1. standard-library unit tests for URL routing, JSON duplicate detection, HTML extraction, image headers, sitemap dates, and safe artifact staging;
2. an isolated regeneration of all music artifacts, compared byte-for-byte with the working tree;
3. a source-bound SHA-256 check of every responsive-image derivative;
4. music-specific release, lyric, download, and sitemap checks;
5. the whole-site validator.

The whole-site validator is offline and deterministic. It checks:

| Contract | Enforced invariants |
|---|---|
| HTML | UTF-8, HTML5 doctype, language, viewport, one title/H1/main, no duplicate attributes |
| Search metadata | unique titles/descriptions/canonicals, self-canonical rules, noindex and redirect behavior, Open Graph completeness |
| Structured data | duplicate-free JSON, Schema.org context, parseability, canonical presence, resolvable first-party URLs |
| Accessibility structure | image alt attributes, intrinsic dimensions, unique IDs, and valid ARIA/label ID references |
| Crawlability | case-exact internal files, safe schemes, valid local fragments, no orphaned indexable pages |
| Images | real binary dimensions, aspect ratios, `srcset` width descriptors, manifest/OG dimensions, lazy-loading policy |
| Discovery | sitemap XML, indexable-page coverage, canonical agreement, W3C dates, image entries, robots and CNAME |
| Music data | ordered release sources, valid tracks/durations/audio URLs, lyric ownership, artwork/alt parity, schema fields |
| Performance | static transfer proxies, total CSS/JS ceilings, optimized-image dimensions and byte ceilings |
| Deployment | only public files enter `_site`, followed by validation of that exact staged artifact |

Human-readable diagnostics are the default. Other useful modes are:

```sh
python3 scripts/validate_site.py --format json
python3 scripts/validate_site.py --format github
python3 scripts/validate_site.py --fail-on-warnings
python3 scripts/validate_site.py --root /path/to/staged/site
```

The complete `check_site.sh` gate requires zero errors and zero warnings. When the validator is run directly, errors fail by default and warnings fail when `--fail-on-warnings` is supplied. A warning should either be resolved or consciously accepted; the validator avoids warnings for valid intentional choices such as social copy differing from search-result copy.

### Performance budgets

Compressed values are generated locally with deterministic gzip level 9. They are stable transfer-size proxies, not claims about a CDN’s exact wire encoding.

| Asset | Maximum raw bytes | Maximum gzip proxy |
|---|---:|---:|
| One HTML file | 220 KiB | 40 KiB |
| One CSS file | 100 KiB | 24 KiB |
| All first-party CSS | 120 KiB | 28 KiB |
| One JavaScript file | 80 KiB | 20 KiB |
| All first-party JavaScript | 160 KiB | 40 KiB |
| One JSON file | 300 KiB | 80 KiB |

Optimized AVIF files are capped at 140 KiB, optimized WebP files at 300 KiB, and optimized sources at 1,200 pixels on their longest edge. A numeric width in an optimized filename must match the image header. All content images after the initial above-the-fold image must be lazy-loaded; the homepage has an explicit four-image allowance because those four covers form one composite hero.

Budgets are intentionally generous enough for release and lyric pages. Change a budget only alongside evidence that the user experience requires it—not simply to make a regression pass.

## Editing ordinary pages

For every new indexable page:

1. choose its final permanent URL;
2. add one descriptive title, one meta description, one H1, and one main landmark;
3. add a self-referential HTTPS canonical;
4. add Open Graph image dimensions and alt text;
5. add page-specific JSON-LD containing the canonical in a `url` property;
6. link the page contextually from another crawlable page;
7. add the canonical to `sitemap.xml`;
8. include width, height, and alt on every image;
9. use responsive AVIF/WebP sources for substantial raster images and lazy-load below-the-fold images;
10. run the complete gate and test narrow and wide viewports.

The 404 and legacy redirect pages are intentionally noindex and follow different canonical rules; the validator encodes those exceptions.

## Publishing music

The release ID is a stable lowercase kebab-case identifier. It becomes part of public URLs and should never be renamed casually.

1. Add the release and tracks to `music-catalog.json`.
2. Add the same release key, in the same order, to `music-release-metadata.json`.
3. Add publishable lyrics to `music-lyrics.json` with keys such as `psion:2`. Omit lyrics that are not cleared for publication.
4. Place canonical square artwork in `assets/downloads/` and provide concise, visual `artworkAlt`.
5. Confirm every audio and download URL is HTTPS and publicly available.
6. Regenerate all derived pages and discovery data:

   ```sh
   ruby scripts/build_music_pages.rb
   ```

   `ruby scripts/build_music_seo.rb` remains a backward-compatible entry point to the same unified builder.

7. Rebuild responsive derivatives after source-art changes:

   ```sh
   bash scripts/build_responsive_images.sh
   ```

8. Review generated release and lyric pages, then run:

   ```sh
   bash scripts/check_site.sh
   ```

The generator writes only changed output, so a second run should report that music pages are already current. CI performs the same generation in a temporary directory and fails on missing, obsolete, or stale artifacts without modifying the checkout.

## Responsive images

`assets/downloads/` and the other original artwork files remain the canonical sources. `assets/optimized/` contains delivery derivatives, not editorial sources.

The responsive-image script requires ImageMagick because codec availability and quality settings are explicit. CI does not re-encode images: codec builds can vary across runners. Instead, `assets/optimized/manifest.json` cryptographically binds every canonical source and generated derivative by SHA-256, while the site validator checks byte size, binary dimensions, filename width, `srcset` descriptors, and local references. A changed source with stale AVIF/WebP output fails CI.

When adding a new image family, update the responsive-image script, generate both AVIF and WebP candidates, retain a broadly supported fallback, and verify that `sizes` describes the rendered layout rather than the source dimensions.

## Pull requests

Keep a pull request focused and include visual evidence when rendering changes. The repository template covers metadata, accessibility, performance, generation, and private-data checks.

Recommended branch protection for `main`:

- require pull requests;
- require `Quality gate / Validate static site`;
- require the branch to be current before merging;
- dismiss stale approvals after new commits;
- block force pushes and deletion;
- restrict workflow-file changes to trusted reviewers.

All third-party actions are pinned to full commit SHAs. Dependabot groups and proposes reviewed updates weekly.

## Deployment

`.github/workflows/deploy-pages.yml` is the production path. On a push to `main` it:

1. checks out the exact commit without persistent Git credentials;
2. runs the complete offline gate;
3. builds `_site` from an explicit public-page manifest plus only the assets those pages reference;
4. validates the exact staged artifact;
5. uploads one immutable Pages artifact;
6. deploys it through GitHub’s `github-pages` environment using short-lived OIDC credentials.

One-time repository setup:

1. In **Settings → Pages → Build and deployment**, select **GitHub Actions** as the source.
2. Confirm the custom domain is `beareab.com` and **Enforce HTTPS** is enabled.
3. In **Settings → Environments → github-pages**, add deployment protection or required reviewers if production should require manual approval.
4. Apply the branch-protection rules above.

Do not keep branch-based Pages publishing enabled in parallel; there should be one production authority.

For rollback, rerun the last known-good deployment workflow (it remains attached to its original commit) or revert the offending commit through the normal protected-branch flow. Never repair production by editing generated HTML directly.

## Troubleshooting

### A generated page is stale

Run `ruby scripts/build_music_pages.rb`, review the diff, and rerun the gate. If an obsolete page is reported, confirm the release/lyric removal was intentional before removing that generated directory.

### A fragment is missing

The validator resolves fragments case-sensitively, as deployment on Linux does. Fix the link or restore the target `id`; do not suppress the error.

### An image width is wrong

The number in a `srcset` width descriptor is the candidate file’s intrinsic pixel width. Regenerate the derivative or correct the descriptor. The `width` and `height` attributes on `<img>` must preserve the actual aspect ratio.

### CI passes but a third-party URL is unavailable

CI deliberately performs no flaky network crawl. It validates first-party integrity and HTTPS syntax. Check new external audio, download, payment, and profile URLs manually during review; monitoring external availability belongs in a separately scheduled job, not the merge gate.
