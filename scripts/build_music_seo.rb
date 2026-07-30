#!/usr/bin/env ruby

# Backward-compatible entry point. The unified builder now keeps the catalog
# schema, release pages, lyric pages, crawlable fallbacks, and sitemap in sync.
load File.expand_path("build_music_pages.rb", __dir__)
