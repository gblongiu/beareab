#!/usr/bin/env ruby

require "cgi"
require "date"
require "fileutils"
require "json"

ROOT = File.expand_path("..", __dir__)
CATALOG_PATH = File.join(ROOT, "music-catalog.json")
LYRICS_PATH = File.join(ROOT, "music-lyrics.json")
METADATA_PATH = File.join(ROOT, "music-release-metadata.json")
MUSIC_PATH = File.join(ROOT, "music.html")
SITEMAP_PATH = File.join(ROOT, "sitemap.xml")
RELEASES_PATH = File.join(ROOT, "music")
SITE_URL = "https://beareab.com"

catalog = JSON.parse(File.read(CATALOG_PATH))
lyrics = JSON.parse(File.read(LYRICS_PATH))
metadata = JSON.parse(File.read(METADATA_PATH))

unless catalog.keys == metadata.keys
  abort "Catalog and release metadata must contain the same releases in the same order."
end

catalog.each do |release_id, release|
  abort "Unsafe release id: #{release_id.inspect}" unless release_id.match?(/\A[a-z0-9]+(?:-[a-z0-9]+)*\z/)

  release_meta = metadata.fetch(release_id)
  %w[artist title tracks].each { |key| abort "#{release_id} is missing #{key}." unless release.key?(key) }
  %w[date dateModified releaseType description seoDescription artwork artworkAlt downloads].each do |key|
    abort "#{release_id} metadata is missing #{key}." unless release_meta.key?(key)
  end
  abort "#{release_id} must provide at least one download." if release_meta.fetch("downloads").empty?

  release.fetch("tracks").each do |track|
    %w[number title duration source].each do |key|
      abort "#{release_id} track #{track["number"] || "?"} is missing #{key}." unless track.key?(key)
    end
  end
end

unknown_lyrics = lyrics.keys.reject do |track_key|
  release_id, track_number = track_key.split(":", 2)
  catalog.fetch(release_id, {}).fetch("tracks", []).any? { |track| track.fetch("number").to_s == track_number }
end
abort "Lyrics reference unknown tracks: #{unknown_lyrics.join(", ")}" unless unknown_lyrics.empty?

def h(value)
  CGI.escapeHTML(value.to_s)
end

def write_if_changed(path, content)
  return false if File.exist?(path) && File.binread(path) == content.b

  FileUtils.mkdir_p(File.dirname(path))
  File.write(path, content)
  true
end

def format_time(raw_seconds)
  seconds = raw_seconds.to_f.round
  hours = seconds / 3600
  minutes = (seconds % 3600) / 60
  remainder = seconds % 60

  return format("%d:%02d:%02d", hours, minutes, remainder) if hours.positive?

  format("%d:%02d", minutes, remainder)
end

def iso_duration(raw_seconds)
  seconds = raw_seconds.to_f.round
  hours = seconds / 3600
  minutes = (seconds % 3600) / 60
  remainder = seconds % 60
  value = +"PT"
  value << "#{hours}H" if hours.positive?
  value << "#{minutes}M" if minutes.positive?
  value << "#{remainder}S" if remainder.positive? || value == "PT"
  value
end

def format_date(iso_date)
  Date.iso8601(iso_date).strftime("%-d %B %Y")
rescue Date::Error
  abort "Invalid ISO date: #{iso_date.inspect}"
end

def release_path(release_id)
  "/music/#{release_id}/"
end

def release_url(release_id)
  "#{SITE_URL}#{release_path(release_id)}"
end

def track_anchor(release_id, track_number)
  "#{release_id}-track-#{track_number}"
end

def slugify(value)
  value.downcase
    .encode("ASCII", invalid: :replace, undef: :replace, replace: "")
    .gsub(/[^a-z0-9]+/, "-")
    .gsub(/\A-+|-+\z/, "")
end

def lyric_page_path(release_id, track)
  "/music/#{release_id}/lyrics/#{slugify(track.fetch("title"))}/"
end

def lyric_page_url(release_id, track)
  "#{SITE_URL}#{lyric_page_path(release_id, track)}"
end

def artist_id(artist)
  artist == "beareab" ? "#{SITE_URL}/#beareab" : "#{SITE_URL}/#ucygrx"
end

def artist_url(artist)
  artist == "beareab" ? "#{SITE_URL}/" : "#{SITE_URL}/projects.html#ucygrx-heading"
end

def musician_profile_url
  "#{SITE_URL}/about.html"
end

def artist_genres(artist)
  if artist == "beareab"
    ["Experimental rock", "Skramz", "Noise rock", "Post-hardcore", "Post-rock"]
  else
    ["Electronic", "Experimental electronic"]
  end
end

def optimized_artwork_base(release_meta)
  artwork = release_meta.fetch("artwork")
  filename = File.basename(artwork, File.extname(artwork))
  "/assets/optimized/#{filename}"
end

def render_artwork_picture(release_meta, loading:, fetchpriority:, sizes:)
  base = optimized_artwork_base(release_meta)
  loading_attribute = loading ? %( loading="#{h(loading)}") : ""
  priority_attribute = fetchpriority ? %( fetchpriority="#{h(fetchpriority)}") : ""

  <<~HTML.rstrip
    <picture>
      <source type="image/avif" srcset="#{base}-480.avif 480w, #{base}-700.avif 700w" sizes="#{h(sizes)}">
      <source type="image/webp" srcset="#{base}-480.webp 480w, #{base}-700.webp 700w" sizes="#{h(sizes)}">
      <img src="#{h(release_meta.fetch("artwork"))}" alt="#{h(release_meta.fetch("artworkAlt"))}" width="700" height="700"#{loading_attribute}#{priority_attribute} decoding="async">
    </picture>
  HTML
end

def render_artwork_preload(release_meta, sizes:)
  base = optimized_artwork_base(release_meta)
  %(<link rel="preload" as="image" type="image/avif" href="#{base}-700.avif" imagesrcset="#{base}-480.avif 480w, #{base}-700.avif 700w" imagesizes="#{h(sizes)}" fetchpriority="high">)
end

def album_artist_schema(release_id, artist)
  return {"@id" => artist_id(artist)} unless release_id == "eyesplice-split"

  [
    {"@id" => artist_id(artist)},
    {"@type" => "MusicGroup", "name" => "eyesplice"}
  ]
end

def render_json_script(data)
  JSON.pretty_generate(data).gsub("</", "<\\/")
end

def recording_schema(release_id, release, track, lyric_catalog, lyrics_mode:)
  page_url = release_url(release_id)
  track_key = "#{release_id}:#{track.fetch("number")}"
  has_lyrics = lyric_catalog.key?(track_key)
  track_url = has_lyrics ? lyric_page_url(release_id, track) : "#{page_url}##{track_anchor(release_id, track.fetch("number"))}"
  recording_id = has_lyrics ? "#{track_url}#recording" : "#{page_url}#recording-track-#{track.fetch("number")}"
  audio_id = has_lyrics ? "#{track_url}#audio" : "#{page_url}#audio-track-#{track.fetch("number")}"
  recording = {
    "@type" => "MusicRecording",
    "@id" => recording_id,
    "url" => track_url,
    "name" => track.fetch("title"),
    "position" => track.fetch("number"),
    "duration" => iso_duration(track.fetch("duration")),
    "byArtist" => {"@id" => artist_id(release.fetch("artist"))},
    "inAlbum" => {"@id" => "#{page_url}#release"},
    "audio" => {
      "@type" => "AudioObject",
      "@id" => audio_id,
      "contentUrl" => track.fetch("source"),
      "encodingFormat" => "audio/mpeg",
      "duration" => iso_duration(track.fetch("duration"))
    }
  }

  if lyrics_mode != :none && has_lyrics
    recording["lyrics"] = {
      "@type" => "CreativeWork",
      "@id" => "#{lyric_page_url(release_id, track)}#lyrics",
      "url" => lyric_page_url(release_id, track),
      "name" => "#{track.fetch("title")} lyrics",
      "inLanguage" => "en"
    }
    recording["lyrics"]["text"] = lyric_catalog.fetch(track_key) if lyrics_mode == :full
  end
  recording["creditText"] = track.fetch("credits") if track["credits"]
  recording
end

def release_schema(release_id, release, release_meta, lyric_catalog, lyrics_mode:, include_downloads:)
  page_url = release_url(release_id)
  tracks = release.fetch("tracks")
  album = {
    "@type" => "MusicAlbum",
    "@id" => "#{page_url}#release",
    "url" => page_url,
    "name" => release.fetch("title"),
    "description" => release_meta.fetch("description"),
    "datePublished" => release_meta.fetch("date"),
    "dateModified" => release_meta.fetch("dateModified"),
    "albumReleaseType" => "https://schema.org/#{release_meta.fetch("releaseType")}",
    "image" => {
      "@type" => "ImageObject",
      "@id" => "#{page_url}#artwork",
      "url" => "#{SITE_URL}#{release_meta.fetch("artwork")}",
      "contentUrl" => "#{SITE_URL}#{release_meta.fetch("artwork")}",
      "caption" => release_meta.fetch("artworkAlt"),
      "width" => 700,
      "height" => 700
    },
    "byArtist" => album_artist_schema(release_id, release.fetch("artist")),
    "genre" => artist_genres(release.fetch("artist")),
    "isAccessibleForFree" => true,
    "track" => tracks.map do |track|
      recording_schema(release_id, release, track, lyric_catalog, lyrics_mode: lyrics_mode)
    end
  }

  schema_duration = release_meta.key?("schemaDuration") ? release_meta["schemaDuration"] : iso_duration(tracks.sum { |track| track.fetch("duration").to_f })
  music_release = {
    "@type" => "MusicRelease",
    "@id" => "#{page_url}#digital-release",
    "url" => page_url,
    "name" => "#{release.fetch("title")} — digital release",
    "datePublished" => release_meta.fetch("date"),
    "musicReleaseFormat" => "https://schema.org/DigitalFormat",
    "releaseOf" => {"@id" => "#{page_url}#release"}
  }
  music_release["duration"] = schema_duration unless schema_duration.nil?
  album["albumRelease"] = music_release
  schema_track_count = release_meta.key?("schemaTrackCount") ? release_meta["schemaTrackCount"] : tracks.length
  album["numTracks"] = schema_track_count unless schema_track_count.nil?

  if include_downloads
    album["encoding"] = release_meta.fetch("downloads").map do |download|
      {
        "@type" => "MediaObject",
        "name" => "#{release.fetch("title")} — #{download.fetch("name")} download",
        "contentUrl" => download.fetch("url"),
        "encodingFormat" => "application/zip",
        "contentSize" => download.fetch("size"),
        "description" => download.fetch("label")
      }
    end
  end

  album
end

def artist_schema(artist)
  if artist == "beareab"
    {
      "@type" => "MusicGroup",
      "@id" => artist_id(artist),
      "name" => "beareab",
      "url" => "#{SITE_URL}/",
      "description" => "The experimental music project of musician, songwriter, and producer Gabriel Long.",
      "genre" => artist_genres(artist),
      "member" => {"@id" => "#{SITE_URL}/#gabriel-long"}
    }
  else
    {
      "@type" => "MusicGroup",
      "@id" => artist_id(artist),
      "name" => "ucygrx",
      "url" => artist_url(artist),
      "description" => "Gabriel Long’s electronic project exploring the strange emotional connection between humans and machines.",
      "genre" => artist_genres(artist),
      "member" => {"@id" => "#{SITE_URL}/#gabriel-long"},
      "sameAs" => ["https://ucygrx.bandcamp.com"]
    }
  end
end

def render_fallback(release_id, release, lyric_catalog, on_release_page:)
  items = release.fetch("tracks").map do |track|
    number = track.fetch("number")
    track_key = "#{release_id}:#{number}"
    destination = on_release_page ? "##{track_anchor(release_id, number)}" : "#{release_path(release_id)}##{track_anchor(release_id, number)}"
    lyric_destination = lyric_page_path(release_id, track)
    lines = [
      %(<li id="#{track_anchor(release_id, number)}">),
      %(  <span class="fallback-track-number">#{number.to_s.rjust(2, "0")}</span>),
      %(  <strong><a href="#{destination}">#{h(track.fetch("title"))}</a></strong>)
    ]
    lines << %(  <a class="fallback-lyrics" href="#{lyric_destination}">Lyrics</a>) if lyric_catalog.key?(track_key)
    lines << %(  <span class="fallback-track-duration">#{format_time(track.fetch("duration"))}</span>)
    lines << %(  <small>#{h(track.fetch("credits"))}</small>) if track["credits"]
    lines << "</li>"
    lines.join("\n")
  end.join("\n")

  <<~HTML.rstrip
    <ol class="player-fallback-tracklist" aria-label="#{h(release.fetch("title"))} track list">
    #{items}
    </ol>
    <p class="player-fallback-note">Enable JavaScript for the first-party streaming player. Downloads remain available below.</p>
  HTML
end

def format_options(release, release_meta)
  release_meta.fetch("downloads").map do |download|
    <<~HTML.rstrip
      <a class="format-option" href="#{h(download.fetch("url"))}" aria-label="#{h(download.fetch("label"))}">
        <span class="format-name">#{h(download.fetch("name"))}</span>
        <span class="format-detail">#{h(download.fetch("size"))}</span>
      </a>
    HTML
  end.join("\n")
end

def lyrics_and_credits(release_id, release, lyric_catalog)
  entries = release.fetch("tracks").map do |track|
    number = track.fetch("number")
    text = lyric_catalog["#{release_id}:#{number}"]
    next unless text || track["credits"] || track["lyricsCredit"]

    paragraphs = if text
      %(<p><a href="#{lyric_page_path(release_id, track)}">Read the complete lyrics to “#{h(track.fetch("title"))}”</a></p>)
    else
      "<p>No lyrics are published for this track.</p>"
    end
    credit_lines = []
    credit_lines << %(<p class="lyrics-credit">#{h(track.fetch("lyricsCredit"))}</p>) if track["lyricsCredit"]
    credit_lines << %(<p class="lyrics-credit">#{h(track.fetch("credits"))}</p>) if track["credits"]

    <<~HTML.rstrip
      <details class="inline-release-notes" id="track-details-#{number}">
        <summary>#{text ? "Lyrics" : "Credits"} — #{h(track.fetch("title"))}</summary>
        <div class="prose">
      #{([paragraphs] + credit_lines).join("\n").lines.map { |line| "    #{line}" }.join.rstrip}
        </div>
      </details>
    HTML
  end.compact

  if entries.empty?
    <<~HTML.rstrip
      <section class="prose" aria-labelledby="lyrics-credits-heading">
        <h2 id="lyrics-credits-heading">Lyrics and track credits</h2>
        <p>No lyrics or additional track-level credits are published for this release.</p>
      </section>
    HTML
  else
    <<~HTML.rstrip
      <section aria-labelledby="lyrics-credits-heading">
        <div class="prose">
          <h2 id="lyrics-credits-heading">Lyrics and track credits</h2>
        </div>
      #{entries.join("\n").lines.map { |line| "  #{line}" }.join.rstrip}
      </section>
    HTML
  end
end

def release_notes(release_meta)
  return "" unless release_meta["notes"]

  paragraphs = release_meta.fetch("notes").map { |note| "<p>#{h(note)}</p>" }.join("\n")
  <<~HTML.rstrip
    <details class="inline-release-notes">
      <summary>Release notes</summary>
      <div class="prose">
    #{paragraphs.lines.map { |line| "    #{line}" }.join.rstrip}
      </div>
    </details>
  HTML
end

def release_page_schema(release_id, release, release_meta, lyric_catalog)
  page_url = release_url(release_id)
  {
    "@context" => "https://schema.org",
    "@graph" => [
      {
        "@type" => "ItemPage",
        "@id" => "#{page_url}#webpage",
        "url" => page_url,
        "name" => "#{release.fetch("title")} by #{release.fetch("artist")}",
        "description" => release_meta.fetch("seoDescription"),
        "dateModified" => release_meta.fetch("dateModified"),
        "isPartOf" => {"@id" => "#{SITE_URL}/#website"},
        "breadcrumb" => {"@id" => "#{page_url}#breadcrumb"},
        "primaryImageOfPage" => {"@id" => "#{page_url}#artwork"},
        "mainEntity" => {"@id" => "#{page_url}#release"},
        "about" => {"@id" => artist_id(release.fetch("artist"))},
        "inLanguage" => "en"
      },
      {
        "@type" => "BreadcrumbList",
        "@id" => "#{page_url}#breadcrumb",
        "itemListElement" => [
          {"@type" => "ListItem", "position" => 1, "name" => "beareab", "item" => "#{SITE_URL}/"},
          {"@type" => "ListItem", "position" => 2, "name" => "Music", "item" => "#{SITE_URL}/music.html"},
          {"@type" => "ListItem", "position" => 3, "name" => release.fetch("title"), "item" => page_url}
        ]
      },
      {
        "@type" => "Person",
        "@id" => "#{SITE_URL}/#gabriel-long",
        "name" => "Gabriel Long",
        "url" => "#{SITE_URL}/about.html"
      },
      artist_schema(release.fetch("artist")),
      release_schema(
        release_id,
        release,
        release_meta,
        lyric_catalog,
        lyrics_mode: :link,
        include_downloads: true
      )
    ]
  }
end

def lyric_page_schema(release_id, release, release_meta, track, lyric_catalog)
  page_url = lyric_page_url(release_id, track)
  album = release_schema(
    release_id,
    release,
    release_meta,
    lyric_catalog,
    lyrics_mode: :none,
    include_downloads: false
  )
  album["track"] = release.fetch("tracks").map do |release_track|
    {
      "@id" => recording_schema(
        release_id,
        release,
        release_track,
        lyric_catalog,
        lyrics_mode: :none
      ).fetch("@id")
    }
  end
  recording = recording_schema(
    release_id,
    release,
    track,
    lyric_catalog,
    lyrics_mode: :full
  )

  {
    "@context" => "https://schema.org",
    "@graph" => [
      {
        "@type" => "ItemPage",
        "@id" => "#{page_url}#webpage",
        "url" => page_url,
        "name" => "#{track.fetch("title")} lyrics — #{release.fetch("artist")}",
        "description" => "Read the complete lyrics to #{track.fetch("title")} by #{release.fetch("artist")}, stream the recording, and explore #{release.fetch("title")}.",
        "dateModified" => release_meta.fetch("dateModified"),
        "isPartOf" => {"@id" => "#{SITE_URL}/#website"},
        "breadcrumb" => {"@id" => "#{page_url}#breadcrumb"},
        "primaryImageOfPage" => {"@id" => "#{release_url(release_id)}#artwork"},
        "mainEntity" => {"@id" => recording.fetch("@id")},
        "about" => {"@id" => artist_id(release.fetch("artist"))},
        "inLanguage" => "en"
      },
      {
        "@type" => "BreadcrumbList",
        "@id" => "#{page_url}#breadcrumb",
        "itemListElement" => [
          {"@type" => "ListItem", "position" => 1, "name" => "beareab", "item" => "#{SITE_URL}/"},
          {"@type" => "ListItem", "position" => 2, "name" => "Music", "item" => "#{SITE_URL}/music.html"},
          {"@type" => "ListItem", "position" => 3, "name" => release.fetch("title"), "item" => release_url(release_id)},
          {"@type" => "ListItem", "position" => 4, "name" => "#{track.fetch("title")} lyrics", "item" => page_url}
        ]
      },
      {
        "@type" => "Person",
        "@id" => "#{SITE_URL}/#gabriel-long",
        "name" => "Gabriel Long",
        "url" => "#{SITE_URL}/about.html"
      },
      artist_schema(release.fetch("artist")),
      album,
      recording
    ]
  }
end

def render_full_lyrics(text)
  text.split(/\n{2,}/).map do |stanza|
    "<p>#{stanza.lines.map { |line| h(line.chomp) }.join("<br>")}</p>"
  end.join("\n")
end

def render_track_fallback(release_id, release, track)
  <<~HTML.rstrip
    <ol class="player-fallback-tracklist" aria-label="#{h(track.fetch("title"))} audio">
      <li id="#{track_anchor(release_id, track.fetch("number"))}">
        <span class="fallback-track-number">#{track.fetch("number").to_s.rjust(2, "0")}</span>
        <strong>#{h(track.fetch("title"))}</strong>
        <span class="fallback-lyrics">Lyrics below</span>
        <span class="fallback-track-duration">#{format_time(track.fetch("duration"))}</span>
      </li>
    </ol>
    <p class="player-fallback-note">Enable JavaScript for the first-party streaming player. The complete lyrics remain available below.</p>
  HTML
end

def render_lyric_page(release_id, release, release_meta, track, lyric_catalog, catalog, asset_versions)
  artist = release.fetch("artist")
  release_title = release.fetch("title")
  track_title = track.fetch("title")
  lyric_text = lyric_catalog.fetch("#{release_id}:#{track.fetch("number")}")
  page_url = lyric_page_url(release_id, track)
  seo_title = "#{track_title} Lyrics — #{artist} | #{release_title}"
  seo_description = "Read the complete lyrics to “#{track_title}” by #{artist} from #{release_title}. Stream the track and download the release free in FLAC or MP3."
  schema = lyric_page_schema(release_id, release, release_meta, track, lyric_catalog)
  lyrical_tracks = release.fetch("tracks").select do |release_track|
    lyric_catalog.key?("#{release_id}:#{release_track.fetch("number")}")
  end
  track_index = lyrical_tracks.index { |release_track| release_track.fetch("number") == track.fetch("number") }
  previous_track = track_index&.positive? ? lyrical_tracks[track_index - 1] : nil
  next_track = track_index && track_index < lyrical_tracks.length - 1 ? lyrical_tracks[track_index + 1] : nil
  pagination_links = []
  pagination_links << %(<link rel="prev" href="#{lyric_page_url(release_id, previous_track)}">) if previous_track
  pagination_links << %(<link rel="next" href="#{lyric_page_url(release_id, next_track)}">) if next_track
  credit_lines = []
  credit_lines << %(<p class="lyrics-credit">#{h(track.fetch("lyricsCredit"))}</p>) if track["lyricsCredit"]
  credit_lines << %(<p class="lyrics-credit">#{h(track.fetch("credits"))}</p>) if track["credits"]
  related_links = []
  related_links << <<~HTML.rstrip if previous_track
    <a href="#{lyric_page_path(release_id, previous_track)}">
      <strong>#{h(previous_track.fetch("title"))}</strong>
      <span>Previous lyrics in #{h(release_title)}</span>
    </a>
  HTML
  related_links << <<~HTML.rstrip
    <a href="#{release_path(release_id)}">
      <strong>#{h(release_title)}</strong>
      <span>Return to the complete release, player, notes, and downloads.</span>
    </a>
  HTML
  related_links << <<~HTML.rstrip if next_track
    <a href="#{lyric_page_path(release_id, next_track)}">
      <strong>#{h(next_track.fetch("title"))}</strong>
      <span>Next lyrics in #{h(release_title)}</span>
    </a>
  HTML
  artwork_sizes = "(max-width: 760px) calc(100vw - 2rem), 700px"

  <<~HTML
    <!doctype html>
    <html lang="en" prefix="og: https://ogp.me/ns# music: https://ogp.me/ns/music#">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <meta name="theme-color" content="#f3f0e9">
      <title>#{h(seo_title)}</title>
      <meta name="description" content="#{h(seo_description)}">
      <meta name="author" content="Gabriel Long">
      <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1">
      <link rel="canonical" href="#{page_url}">
    #{pagination_links.map { |link| "  #{link}" }.join("\n")}
      <meta property="og:site_name" content="beareab">
      <meta property="og:title" content="#{h(seo_title)}">
      <meta property="og:description" content="#{h(seo_description)}">
      <meta property="og:url" content="#{page_url}">
      <meta property="og:type" content="music.song">
      <meta property="og:image" content="#{SITE_URL}#{h(release_meta.fetch("artwork"))}">
      <meta property="og:image:secure_url" content="#{SITE_URL}#{h(release_meta.fetch("artwork"))}">
      <meta property="og:image:type" content="image/jpeg">
      <meta property="og:image:alt" content="#{h(release_meta.fetch("artworkAlt"))}">
      <meta property="og:image:width" content="700">
      <meta property="og:image:height" content="700">
      <meta property="music:musician" content="#{musician_profile_url}">
      <meta property="music:album" content="#{release_url(release_id)}">
      <meta property="music:album:track" content="#{track.fetch("number")}">
      <meta property="music:duration" content="#{track.fetch("duration").to_f.round}">
      <meta name="twitter:card" content="summary_large_image">
      <meta name="twitter:title" content="#{h(seo_title)}">
      <meta name="twitter:description" content="#{h(seo_description)}">
      <meta name="twitter:image" content="#{SITE_URL}#{h(release_meta.fetch("artwork"))}">
      <meta name="twitter:image:alt" content="#{h(release_meta.fetch("artworkAlt"))}">
      <link rel="icon" href="/favicon.ico">
      <link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
      <link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">
      <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
      <link rel="manifest" href="/site.webmanifest">
      <link rel="preload" href="/assets/fonts/newsreader-latin.woff2" as="font" type="font/woff2" crossorigin>
      #{render_artwork_preload(release_meta, sizes: artwork_sizes)}
      <link rel="stylesheet" href="#{h(asset_versions.fetch(:style))}">
      <script type="application/ld+json">
    #{render_json_script(schema).lines.map { |line| "  #{line}" }.join.rstrip}
      </script>
    </head>
    <body>
      <!-- Generated by scripts/build_music_pages.rb; edit the JSON source files, not this page. -->
      <a class="skip-link" href="#main">Skip to content</a>
      <header class="site-header">
        <div class="header-inner">
          <a class="brand" href="/" aria-label="beareab home">
            <span class="wordmark">beareab</span>
          </a>
          <nav aria-label="Primary">
            <ul class="nav-list">
              <li><a href="/music.html" aria-current="page">Music</a></li>
              <li><a href="/projects.html">Projects</a></li>
              <li><a href="/about.html">About</a></li>
              <li><a href="/support.html">Support</a></li>
            </ul>
          </nav>
        </div>
      </header>

      <main id="main">
        <section class="page-head music-hero">
          <div class="shell">
            <p class="eyebrow">#{h(artist)} · #{h(release_title)}</p>
            <h1>#{h(track_title)}</h1>
            <p class="dek">Complete lyrics, first-party audio, and track credits.</p>
          </div>
        </section>

        <div class="catalog-overview shell">
          <p class="catalog-summary">Track #{track.fetch("number")} <span aria-hidden="true">·</span> #{format_time(track.fetch("duration"))} <span aria-hidden="true">·</span> <time datetime="#{h(release_meta.fetch("date"))}">#{format_date(release_meta.fetch("date"))}</time></p>
          <nav class="catalog-index" aria-label="Breadcrumb">
            <a href="/"><strong>beareab</strong><span>home</span></a>
            <a href="/music.html"><strong>Music</strong><span>catalog</span></a>
            <a href="#{release_path(release_id)}"><strong>#{h(release_title)}</strong><span>release</span></a>
            <a href="#main" aria-current="page"><strong>#{h(track_title)}</strong><span>lyrics</span></a>
          </nav>
        </div>

        <section class="download-catalog shell" aria-labelledby="lyrics-heading">
          <div class="catalog-heading">
            <h2 id="lyrics-heading">Lyrics</h2>
            <p>Published lyrics for track #{track.fetch("number")} from <em>#{h(release_title)}</em>.</p>
          </div>
          <div class="download-grid">
            <article class="download-card" id="#{release_id}-track-#{track.fetch("number")}-page">
    #{render_artwork_picture(release_meta, loading: nil, fetchpriority: "high", sizes: artwork_sizes).lines.map { |line| "          #{line}" }.join.rstrip}
              <div class="release-overview">
                <p class="release-meta"><span>Track #{track.fetch("number")}</span><span>#{format_time(track.fetch("duration"))}</span></p>
                <h3>#{h(track_title)}</h3>
              </div>
              <details class="release-details" open>
                <summary>
                  <span class="summary-closed">Open track</span>
                  <span class="summary-open">Close track</span>
                </summary>
                <div class="release-panel">
                  <p>From <a href="#{release_path(release_id)}"><em>#{h(release_title)}</em></a> by #{h(artist)}.</p>
                  <div class="music-card-player" data-release-player="#{release_id}" data-initial-track="#{track.fetch("number")}">
    #{render_track_fallback(release_id, release, track).lines.map { |line| "                #{line}" }.join.rstrip}
                  </div>
                  <section class="prose" id="lyrics" aria-labelledby="full-lyrics-heading">
                    <h2 id="full-lyrics-heading">#{h(track_title)} lyrics</h2>
    #{render_full_lyrics(lyric_text).lines.map { |line| "                #{line}" }.join.rstrip}
    #{credit_lines.map { |line| "                #{line}" }.join("\n")}
                  </section>
                  <div class="format-options" role="group" aria-label="#{h(release_title)} download formats">
    #{format_options(release, release_meta).lines.map { |line| "                #{line}" }.join.rstrip}
                  </div>
                  <p class="checksum-link"><a href="#{release_path(release_id)}">Release details and complete track list</a></p>
                </div>
              </details>
            </article>
          </div>
        </section>

        <section class="catalog-coda shell" aria-labelledby="related-heading">
          <div>
            <p class="eyebrow">Continue reading</p>
            <h2 id="related-heading">More from this release.</h2>
          </div>
          <div class="catalog-coda-links">
    #{related_links.join("\n").lines.map { |line| "        #{line}" }.join.rstrip}
          </div>
        </section>
      </main>

      <footer class="site-footer">
        <div class="footer-inner shell">
          <p>© 2026 beareab · Gabriel Long</p>
          <nav class="footer-nav" aria-label="Footer">
            <a href="/connect.html">Connect</a>
            <a href="/support.html">Support</a>
            <a href="/leaving-streaming.html">Why this music lives here</a>
            <a href="/privacy.html">Privacy</a>
          </nav>
        </div>
      </footer>
      <script src="#{h(asset_versions.fetch(:analytics))}" defer></script>
      <script src="#{h(asset_versions.fetch(:music))}" defer></script>
    </body>
    </html>
  HTML
end

def catalog_schema(catalog, metadata, lyric_catalog)
  item_list_elements = catalog.each_with_index.map do |(release_id, release), index|
    {
      "@type" => "ListItem",
      "position" => index + 1,
      "url" => release_url(release_id),
      "item" => release_schema(
        release_id,
        release,
        metadata.fetch(release_id),
        lyric_catalog,
        lyrics_mode: :none,
        include_downloads: false
      )
    }
  end

  {
    "@context" => "https://schema.org",
    "@graph" => [
      {
        "@type" => "WebSite",
        "@id" => "#{SITE_URL}/#website",
        "url" => "#{SITE_URL}/",
        "name" => "beareab",
        "alternateName" => "beareab official website",
        "publisher" => {"@id" => "#{SITE_URL}/#beareab"},
        "inLanguage" => "en"
      },
      {
        "@type" => "CollectionPage",
        "@id" => "#{SITE_URL}/music.html#webpage",
        "url" => "#{SITE_URL}/music.html",
        "name" => "beareab Discography: Music, Lyrics and Free Downloads",
        "description" => "Stream the complete beareab and ucygrx catalogs, read beareab lyrics, and download every release free in FLAC, MP3 320, or MP3 128. No account or paywall.",
        "isPartOf" => {"@id" => "#{SITE_URL}/#website"},
        "breadcrumb" => {"@id" => "#{SITE_URL}/music.html#breadcrumb"},
        "mainEntity" => {"@id" => "#{SITE_URL}/music.html#catalog"},
        "about" => [
          {"@id" => "#{SITE_URL}/#beareab"},
          {"@id" => "#{SITE_URL}/#ucygrx"}
        ],
        "inLanguage" => "en"
      },
      {
        "@type" => "BreadcrumbList",
        "@id" => "#{SITE_URL}/music.html#breadcrumb",
        "itemListElement" => [
          {"@type" => "ListItem", "position" => 1, "name" => "beareab", "item" => "#{SITE_URL}/"},
          {"@type" => "ListItem", "position" => 2, "name" => "Music", "item" => "#{SITE_URL}/music.html"}
        ]
      },
      {
        "@type" => "Person",
        "@id" => "#{SITE_URL}/#gabriel-long",
        "name" => "Gabriel Long",
        "url" => "#{SITE_URL}/about.html"
      },
      artist_schema("beareab"),
      artist_schema("ucygrx"),
      {
        "@type" => "ItemList",
        "@id" => "#{SITE_URL}/music.html#catalog",
        "name" => "beareab and ucygrx discography",
        "numberOfItems" => item_list_elements.length,
        "itemListElement" => item_list_elements
      }
    ]
  }
end

def render_related_links(release_id, release, catalog)
  siblings = catalog.select { |_id, item| item.fetch("artist") == release.fetch("artist") }.to_a
  index = siblings.index { |id, _item| id == release_id }
  newer = index&.positive? ? siblings[index - 1] : nil
  older = index && index < siblings.length - 1 ? siblings[index + 1] : nil
  links = []
  if newer
    links << <<~HTML.rstrip
      <a href="#{release_path(newer[0])}">
        <strong>#{h(newer[1].fetch("title"))}</strong>
        <span>Newer #{h(release.fetch("artist"))} release</span>
      </a>
    HTML
  end
  links << <<~HTML.rstrip
    <a href="/music.html">
      <strong>Complete music catalog</strong>
      <span>All beareab and ucygrx releases, streams, lyrics, and downloads.</span>
    </a>
  HTML
  if older
    links << <<~HTML.rstrip
      <a href="#{release_path(older[0])}">
        <strong>#{h(older[1].fetch("title"))}</strong>
        <span>Earlier #{h(release.fetch("artist"))} release</span>
      </a>
    HTML
  end
  links.join("\n")
end

def render_release_page(release_id, release, release_meta, lyric_catalog, catalog, asset_versions)
  artist = release.fetch("artist")
  title = release.fetch("title")
  tracks = release.fetch("tracks")
  has_lyrics = tracks.any? { |track| lyric_catalog.key?("#{release_id}:#{track.fetch("number")}") }
  seo_title = if release_id == "eyesplice-split"
    "#{title} | Lyrics, Stream & Free Download"
  else
    "#{title} — #{artist} | #{has_lyrics ? "Lyrics, Stream & Free Download" : "Stream & Free Download"}"
  end
  page_url = release_url(release_id)
  total_duration = tracks.sum { |track| track.fetch("duration").to_f }
  track_label = "#{tracks.length} #{tracks.length == 1 ? "track" : "tracks"}"
  schema = release_page_schema(release_id, release, release_meta, lyric_catalog)
  siblings = catalog.select { |_id, item| item.fetch("artist") == artist }.to_a
  sibling_index = siblings.index { |id, _item| id == release_id }
  newer = sibling_index&.positive? ? siblings[sibling_index - 1] : nil
  older = sibling_index && sibling_index < siblings.length - 1 ? siblings[sibling_index + 1] : nil
  pagination_links = []
  pagination_links << %(<link rel="prev" href="#{release_url(newer[0])}">) if newer
  pagination_links << %(<link rel="next" href="#{release_url(older[0])}">) if older
  music_song_meta = tracks.map do |track|
    track_key = "#{release_id}:#{track.fetch("number")}"
    next unless lyric_catalog.key?(track_key)

    track_url = lyric_page_url(release_id, track)
    [
      %(<meta property="music:song" content="#{h(track_url)}">),
      %(<meta property="music:song:disc" content="1">),
      %(<meta property="music:song:track" content="#{track.fetch("number")}">)
    ].join("\n")
  end.compact.join("\n")
  rights_note = if release_meta["rightsNote"]
    %(                <p class="rights-note">#{h(release_meta.fetch("rightsNote"))}</p>)
  else
    ""
  end
  notes = release_notes(release_meta)
  notes = "\n#{notes}" unless notes.empty?
  artwork_sizes = "(max-width: 760px) calc(100vw - 2rem), 700px"

  <<~HTML
    <!doctype html>
    <html lang="en" prefix="og: https://ogp.me/ns# music: https://ogp.me/ns/music#">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <meta name="theme-color" content="#f3f0e9">
      <title>#{h(seo_title)}</title>
      <meta name="description" content="#{h(release_meta.fetch("seoDescription"))}">
      <meta name="author" content="Gabriel Long">
      <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1">
      <link rel="canonical" href="#{page_url}">
    #{pagination_links.map { |link| "  #{link}" }.join("\n")}
      <meta property="og:site_name" content="beareab">
      <meta property="og:title" content="#{h(seo_title)}">
      <meta property="og:description" content="#{h(release_meta.fetch("seoDescription"))}">
      <meta property="og:url" content="#{page_url}">
      <meta property="og:type" content="music.album">
      <meta property="og:image" content="#{SITE_URL}#{h(release_meta.fetch("artwork"))}">
      <meta property="og:image:secure_url" content="#{SITE_URL}#{h(release_meta.fetch("artwork"))}">
      <meta property="og:image:type" content="image/jpeg">
      <meta property="og:image:alt" content="#{h(release_meta.fetch("artworkAlt"))}">
      <meta property="og:image:width" content="700">
      <meta property="og:image:height" content="700">
      <meta property="music:release_date" content="#{h(release_meta.fetch("date"))}">
      <meta property="music:musician" content="#{musician_profile_url}">
    #{music_song_meta.lines.map { |line| "  #{line}" }.join.rstrip}
      <meta name="twitter:card" content="summary_large_image">
      <meta name="twitter:title" content="#{h(seo_title)}">
      <meta name="twitter:description" content="#{h(release_meta.fetch("seoDescription"))}">
      <meta name="twitter:image" content="#{SITE_URL}#{h(release_meta.fetch("artwork"))}">
      <meta name="twitter:image:alt" content="#{h(release_meta.fetch("artworkAlt"))}">
      <link rel="icon" href="/favicon.ico">
      <link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
      <link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">
      <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
      <link rel="manifest" href="/site.webmanifest">
      <link rel="preload" href="/assets/fonts/newsreader-latin.woff2" as="font" type="font/woff2" crossorigin>
      #{render_artwork_preload(release_meta, sizes: artwork_sizes)}
      <link rel="stylesheet" href="#{h(asset_versions.fetch(:style))}">
      <script type="application/ld+json">
    #{render_json_script(schema).lines.map { |line| "  #{line}" }.join.rstrip}
      </script>
    </head>
    <body>
      <!-- Generated by scripts/build_music_pages.rb; edit the JSON source files, not this page. -->
      <a class="skip-link" href="#main">Skip to content</a>
      <header class="site-header">
        <div class="header-inner">
          <a class="brand" href="/" aria-label="beareab home">
            <span class="wordmark">beareab</span>
          </a>
          <nav aria-label="Primary">
            <ul class="nav-list">
              <li><a href="/music.html" aria-current="page">Music</a></li>
              <li><a href="/projects.html">Projects</a></li>
              <li><a href="/about.html">About</a></li>
              <li><a href="/support.html">Support</a></li>
            </ul>
          </nav>
        </div>
      </header>

      <main id="main">
        <section class="page-head music-hero">
          <div class="shell">
            <p class="eyebrow">#{h(artist)} release</p>
            <h1>#{h(title)}</h1>
            <p class="dek">#{h(release_meta.fetch("description"))}</p>
          </div>
        </section>

        <div class="catalog-overview shell">
          <p class="catalog-summary"><time datetime="#{h(release_meta.fetch("date"))}">#{format_date(release_meta.fetch("date"))}</time> <span aria-hidden="true">·</span> #{track_label} <span aria-hidden="true">·</span> #{format_time(total_duration)}</p>
          <nav class="catalog-index" aria-label="Breadcrumb">
            <a href="/"><strong>beareab</strong><span>home</span></a>
            <a href="/music.html"><strong>Music</strong><span>catalog</span></a>
            <a href="#main" aria-current="page"><strong>#{h(title)}</strong><span>release</span></a>
          </nav>
        </div>

        <section class="download-catalog shell" aria-labelledby="listen-heading">
          <div class="catalog-heading">
            <h2 id="listen-heading">Listen and download</h2>
            <p>#{has_lyrics ? "First-party audio, published lyrics and free lossless or MP3 downloads." : "First-party audio and free lossless or MP3 downloads."} No account or paywall.</p>
          </div>
          <div class="download-grid">
            <article class="download-card" id="#{release_id}-download">
    #{render_artwork_picture(release_meta, loading: nil, fetchpriority: "high", sizes: artwork_sizes).lines.map { |line| "          #{line}" }.join.rstrip}
              <div class="release-overview">
                <p class="release-meta"><span>#{format_date(release_meta.fetch("date"))}</span><span>#{track_label}</span></p>
                <h3>#{h(title)}</h3>
    #{rights_note}
              </div>
              <details class="release-details" open>
                <summary>
                  <span class="summary-closed">Open release</span>
                  <span class="summary-open">Close release</span>
                </summary>
                <div class="release-panel">
                  <p>#{h(release_meta.fetch("description"))}</p>
                  <div class="music-card-player" data-release-player="#{release_id}">
    #{render_fallback(release_id, release, lyric_catalog, on_release_page: true).lines.map { |line| "                #{line}" }.join.rstrip}
                  </div>
                  <div class="format-options" role="group" aria-label="#{h(title)} download formats">
    #{format_options(release, release_meta).lines.map { |line| "                #{line}" }.join.rstrip}
                  </div>#{notes}
                  #{lyrics_and_credits(release_id, release, lyric_catalog)}
                  <p class="checksum-link"><a href="https://github.com/gblongiu/beareab/releases/download/direct-downloads-2026-07-25/SHA256SUMS-all.txt">Verify download checksums</a></p>
                </div>
              </details>
            </article>
          </div>
        </section>

        <section class="catalog-coda shell" aria-labelledby="related-heading">
          <div>
            <p class="eyebrow">Continue listening</p>
            <h2 id="related-heading">More from the archive.</h2>
          </div>
          <div class="catalog-coda-links">
    #{render_related_links(release_id, release, catalog).lines.map { |line| "        #{line}" }.join.rstrip}
          </div>
        </section>
      </main>

      <footer class="site-footer">
        <div class="footer-inner shell">
          <p>© 2026 beareab · Gabriel Long</p>
          <nav class="footer-nav" aria-label="Footer">
            <a href="/connect.html">Connect</a>
            <a href="/support.html">Support</a>
            <a href="/leaving-streaming.html">Why this music lives here</a>
            <a href="/privacy.html">Privacy</a>
          </nav>
        </div>
      </footer>
      <script src="#{h(asset_versions.fetch(:analytics))}" defer></script>
      <script src="#{h(asset_versions.fetch(:music))}" defer></script>
    </body>
    </html>
  HTML
end

catalog.each do |release_id, release|
  lyric_slugs = release.fetch("tracks").map do |track|
    next unless lyrics.key?("#{release_id}:#{track.fetch("number")}")

    slug = slugify(track.fetch("title"))
    abort "#{release_id} track #{track.fetch("number")} has an empty lyric-page slug." if slug.empty?
    slug
  end.compact
  duplicates = lyric_slugs.group_by { |slug| slug }.select { |_slug, values| values.length > 1 }.keys
  abort "#{release_id} has duplicate lyric-page slugs: #{duplicates.join(", ")}" unless duplicates.empty?
end

music_html = File.read(MUSIC_PATH)
asset_versions = {
  style: music_html[/<link rel="stylesheet" href="([^"]+)"/, 1] || "/style.css",
  analytics: music_html[/<script src="([^"]*analytics\.js[^"]*)"/, 1] || "/analytics.js",
  music: music_html[/<script src="([^"]*music\.js[^"]*)"/, 1] || "/music.js"
}

schema_markup = [
  "  <!-- MUSIC_SCHEMA_START -->",
  "  <script type=\"application/ld+json\">",
  render_json_script(catalog_schema(catalog, metadata, lyrics)).lines.map { |line| "  #{line}" }.join.rstrip,
  "  </script>",
  "  <!-- MUSIC_SCHEMA_END -->"
].join("\n")

music_html.sub!(
  /\s*<!-- MUSIC_SCHEMA_START -->.*?<!-- MUSIC_SCHEMA_END -->/m,
  "\n#{schema_markup}"
) || abort("Could not find the music schema markers.")

catalog.each do |release_id, release|
  article_pattern = /<article class="download-card" id="#{Regexp.escape(release_id)}-download">.*?<\/article>/m
  article_match = music_html.match(article_pattern)
  abort "Could not find catalog card for #{release_id}." unless article_match
  article_html = article_match[0]
  release_meta = metadata.fetch(release_id)
  artwork_sizes = "(max-width: 760px) calc(100vw - 2rem), (max-width: 1100px) 42vw, 420px"
  artwork_markup = render_artwork_picture(
    release_meta,
    loading: release_id == catalog.keys.first ? nil : "lazy",
    fetchpriority: release_id == catalog.keys.first ? "high" : nil,
    sizes: artwork_sizes
  )
  media_pattern = /(?:<picture>.*?<\/picture>|<img\b[^>]*>)/m
  article_html.sub!(media_pattern, artwork_markup) || abort("Could not update artwork for #{release_id}.")

  fallback = render_fallback(release_id, release, lyrics, on_release_page: false)
  player_pattern = /(<div class="music-card-player" data-release-player="#{Regexp.escape(release_id)}">).*?(<\/div>)/m
  article_html.sub!(player_pattern) do
    "#{Regexp.last_match(1)}\n#{fallback.lines.map { |line| "                #{line}" }.join.rstrip}\n              #{Regexp.last_match(2)}"
  end || abort("Could not find player mount for #{release_id}.")

  title_pattern = /(<h3>).*?(<\/h3>)/m
  linked_title = %(<a href="#{release_path(release_id)}">#{h(release.fetch("title"))}</a>)
  article_html.sub!(title_pattern) do
    "#{Regexp.last_match(1)}#{linked_title}#{Regexp.last_match(2)}"
  end || abort("Could not link release title for #{release_id}.")

  description = h(release_meta.fetch("description"))
  if article_html.match?(/<div class="release-panel">\s*<p>/m)
    article_html.sub!(/(<div class="release-panel">\s*<p>).*?(<\/p>)/m) do
      "#{Regexp.last_match(1)}#{description}#{Regexp.last_match(2)}"
    end
  else
    article_html.sub!(/<div class="release-panel">/) do
      "#{Regexp.last_match(0)}\n              <p>#{description}</p>"
    end || abort("Could not insert release description for #{release_id}.")
  end

  music_html.sub!(article_pattern) { article_html }
end

changed = []
changed << "music.html" if write_if_changed(MUSIC_PATH, music_html)

catalog.each do |release_id, release|
  output_path = File.join(RELEASES_PATH, release_id, "index.html")
  page = render_release_page(
    release_id,
    release,
    metadata.fetch(release_id),
    lyrics,
    catalog,
    asset_versions
  )
  changed << "music/#{release_id}/index.html" if write_if_changed(output_path, page)

  release.fetch("tracks").each do |track|
    next unless lyrics.key?("#{release_id}:#{track.fetch("number")}")

    lyric_output_path = File.join(
      RELEASES_PATH,
      release_id,
      "lyrics",
      slugify(track.fetch("title")),
      "index.html"
    )
    lyric_page = render_lyric_page(
      release_id,
      release,
      metadata.fetch(release_id),
      track,
      lyrics,
      catalog,
      asset_versions
    )
    relative_path = lyric_output_path.delete_prefix("#{ROOT}/")
    changed << relative_path if write_if_changed(lyric_output_path, lyric_page)
  end
end

sitemap_html = File.read(SITEMAP_PATH)
release_sitemap_entries = catalog.map do |release_id, release|
  release_meta = metadata.fetch(release_id)
  <<~XML.rstrip
    <url>
      <loc>#{release_url(release_id)}</loc>
      <lastmod>#{h(release_meta.fetch("dateModified"))}</lastmod>
      <image:image>
        <image:loc>#{SITE_URL}#{h(release_meta.fetch("artwork"))}</image:loc>
      </image:image>
    </url>
  XML
end.join("\n")

lyric_sitemap_entries = catalog.flat_map do |release_id, release|
  release.fetch("tracks").map do |track|
    next unless lyrics.key?("#{release_id}:#{track.fetch("number")}")

    release_meta = metadata.fetch(release_id)
    <<~XML.rstrip
      <url>
        <loc>#{lyric_page_url(release_id, track)}</loc>
        <lastmod>#{h(release_meta.fetch("dateModified"))}</lastmod>
        <image:image>
          <image:loc>#{SITE_URL}#{h(release_meta.fetch("artwork"))}</image:loc>
        </image:image>
      </url>
    XML
  end.compact
end.join("\n")

sitemap_block = <<~XML.rstrip
  <!-- RELEASE_PAGES_START: generated by scripts/build_music_pages.rb -->
#{release_sitemap_entries.lines.map { |line| "  #{line}" }.join.rstrip}
#{lyric_sitemap_entries.lines.map { |line| "  #{line}" }.join.rstrip}
  <!-- RELEASE_PAGES_END -->
XML

if sitemap_html.include?("<!-- RELEASE_PAGES_START")
  sitemap_html.sub!(
    /  <!-- RELEASE_PAGES_START.*?  <!-- RELEASE_PAGES_END -->/m,
    sitemap_block
  )
else
  sitemap_html.sub!("</urlset>", "#{sitemap_block}\n</urlset>") || abort("Could not update sitemap.xml.")
end
changed << "sitemap.xml" if write_if_changed(SITEMAP_PATH, sitemap_html)

if changed.empty?
  puts "Music pages are already up to date."
else
  puts "Updated #{changed.length} generated artifact#{changed.length == 1 ? "" : "s"}:"
  changed.each { |path| puts "  #{path}" }
end
