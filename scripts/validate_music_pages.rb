#!/usr/bin/env ruby

require "cgi"
require "json"
require "rexml/document"
require "set"

ROOT = File.expand_path("..", __dir__)
SITE_URL = "https://beareab.com"

catalog = JSON.parse(File.read(File.join(ROOT, "music-catalog.json")))
lyrics = JSON.parse(File.read(File.join(ROOT, "music-lyrics.json")))
metadata = JSON.parse(File.read(File.join(ROOT, "music-release-metadata.json")))
errors = []

def slugify(value)
  value.downcase
    .encode("ASCII", invalid: :replace, undef: :replace, replace: "")
    .gsub(/[^a-z0-9]+/, "-")
    .gsub(/\A-+|-+\z/, "")
end

def release_path(release_id)
  "/music/#{release_id}/"
end

def lyric_path(release_id, track)
  "#{release_path(release_id)}lyrics/#{slugify(track.fetch("title"))}/"
end

def local_target(root, raw_url)
  path = raw_url.split(/[?#]/, 2).first
  return if path.nil? || path.empty? || !path.start_with?("/")

  path == "/" ? File.join(root, "index.html") :
    path.end_with?("/") ? File.join(root, path, "index.html") : File.join(root, path)
end

expected_pages = {}
catalog.each do |release_id, release|
  expected_pages[release_path(release_id)] = File.join(ROOT, "music", release_id, "index.html")
  release.fetch("tracks").each do |track|
    next unless lyrics.key?("#{release_id}:#{track.fetch("number")}")

    expected_pages[lyric_path(release_id, track)] = File.join(
      ROOT,
      "music",
      release_id,
      "lyrics",
      slugify(track.fetch("title")),
      "index.html"
    )
  end
end

actual_pages = Dir[File.join(ROOT, "music", "**", "index.html")].to_set
missing_pages = expected_pages.values.reject { |path| File.file?(path) }
extra_pages = actual_pages - expected_pages.values.to_set
errors << "Missing generated pages: #{missing_pages.join(", ")}" unless missing_pages.empty?
errors << "Unexpected generated pages: #{extra_pages.to_a.join(", ")}" unless extra_pages.empty?

expected_pages.each do |web_path, file_path|
  next unless File.file?(file_path)

  html = File.read(file_path)
  canonical = "#{SITE_URL}#{web_path}"
  title = CGI.unescapeHTML(html[/<title>(.*?)<\/title>/m, 1].to_s)
  description = CGI.unescapeHTML(html[/<meta name="description" content="([^"]*)">/, 1].to_s)
  canonicals = html.scan(/<link rel="canonical" href="([^"]+)">/).flatten
  h1_count = html.scan(/<h1(?:\s[^>]*)?>/).length
  json_blocks = html.scan(/<script type="application\/ld\+json">\s*(.*?)\s*<\/script>/m).flatten

  errors << "#{web_path}: expected one canonical, found #{canonicals.length}" unless canonicals == [canonical]
  errors << "#{web_path}: expected one h1, found #{h1_count}" unless h1_count == 1
  errors << "#{web_path}: title is empty" if title.empty?
  errors << "#{web_path}: title exceeds 65 characters (#{title.length})" if title.length > 65
  errors << "#{web_path}: meta description is empty" if description.empty?
  errors << "#{web_path}: meta description exceeds 160 characters (#{description.length})" if description.length > 160
  errors << "#{web_path}: missing JSON-LD" if json_blocks.empty?
  json_blocks.each_with_index do |json, index|
    parsed = JSON.parse(json)
    stack = [parsed]
    until stack.empty?
      value = stack.pop
      case value
      when Hash
        raw_types = value["@type"]
        node_types = raw_types.is_a?(Array) ? raw_types : [raw_types]
        if node_types.include?("MusicAlbum") && value.key?("duration")
          errors << "#{web_path}: MusicAlbum must not declare duration"
        end
        if value["@id"].is_a?(String) && value["@id"].split("#", -1).length > 2
          errors << "#{web_path}: JSON-LD @id contains multiple fragments (#{value["@id"]})"
        end
        stack.concat(value.values)
      when Array
        stack.concat(value)
      end
    end
  rescue JSON::ParserError => error
    errors << "#{web_path}: JSON-LD block #{index + 1} is invalid (#{error.message})"
  end

  html.scan(/(?:href|src)="([^"]+)"/).flatten.each do |url|
    target = local_target(ROOT, url)
    errors << "#{web_path}: missing local target #{url}" if target && !File.exist?(target)
  end
end

catalog.each do |release_id, release|
  release_file = expected_pages.fetch(release_path(release_id))
  next unless File.file?(release_file)

  release_html = File.read(release_file)
  release_meta = metadata.fetch(release_id)
  og_type = release_html[/<meta property="og:type" content="([^"]+)">/, 1]
  musician_urls = release_html.scan(
    /<meta property="music:musician" content="([^"]+)">/
  ).flatten
  song_urls = release_html.scan(
    /<meta property="music:song" content="([^"]+)">/
  ).flatten
  expected_song_urls = release.fetch("tracks").map do |track|
    track_key = "#{release_id}:#{track.fetch("number")}"
    "#{SITE_URL}#{lyric_path(release_id, track)}" if lyrics.key?(track_key)
  end.compact

  errors << "#{release_path(release_id)}: og:type must be music.album" unless og_type == "music.album"
  unless musician_urls == ["#{SITE_URL}/about.html"]
    errors << "#{release_path(release_id)}: music:musician must identify the About profile"
  end
  unless song_urls == expected_song_urls
    errors << "#{release_path(release_id)}: music:song relations must match published music.song pages"
  end
  if expected_song_urls.empty? && release_html.include?("published lyrics")
    errors << "#{release_path(release_id)}: claims to have published lyrics when none exist"
  end
  release_meta.fetch("downloads").each do |download|
    errors << "#{release_path(release_id)}: missing #{download.fetch("name")} download" unless release_html.include?(download.fetch("url"))
  end

  release.fetch("tracks").each do |track|
    track_key = "#{release_id}:#{track.fetch("number")}"
    next unless lyrics.key?(track_key)

    target_path = lyric_path(release_id, track)
    lyric_html = File.read(expected_pages.fetch(target_path))
    errors << "#{release_path(release_id)}: missing link to #{target_path}" unless release_html.include?(%{href="#{target_path}"})
    errors << "#{release_path(release_id)}: duplicates full lyrics for #{track.fetch("title")}" if release_html.include?(CGI.escapeHTML(lyrics.fetch(track_key)))
    errors << "#{target_path}: og:type must be music.song" unless lyric_html.include?(%{<meta property="og:type" content="music.song">})
    errors << "#{target_path}: music:musician must identify the About profile" unless lyric_html.include?(%{<meta property="music:musician" content="#{SITE_URL}/about.html">})
  end
end

music_html = File.read(File.join(ROOT, "music.html"))
catalog.each_key do |release_id|
  expected_link = %{href="#{release_path(release_id)}"}
  errors << "music.html: missing release link #{expected_link}" unless music_html.include?(expected_link)
end

begin
  sitemap = REXML::Document.new(File.read(File.join(ROOT, "sitemap.xml")))
  sitemap_urls = REXML::XPath.match(sitemap, "//*[local-name()='loc']").map(&:text).to_set
  expected_pages.each_key do |web_path|
    url = "#{SITE_URL}#{web_path}"
    errors << "sitemap.xml: missing #{url}" unless sitemap_urls.include?(url)
  end
rescue REXML::ParseException => error
  errors << "sitemap.xml is invalid XML (#{error.message})"
end

if errors.empty?
  puts "Validated #{expected_pages.length} generated music pages, #{catalog.length} releases, and #{lyrics.length} lyric pages."
else
  warn "Music page validation failed with #{errors.length} error#{errors.length == 1 ? "" : "s"}:"
  errors.each { |error| warn "  - #{error}" }
  exit 1
end
