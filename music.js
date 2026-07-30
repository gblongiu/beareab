const playerMounts = document.querySelectorAll("[data-release-player]");
const releaseDetails = document.querySelectorAll(".release-details");
const activeAudio = new Set();
let catalogRequest;

function formatTime(rawSeconds) {
  const seconds = Math.max(0, Math.round(Number(rawSeconds) || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
  }

  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function trackEvent(name, parameters) {
  window.beareabAnalytics?.track(name, parameters);
}

function renderLyrics(container, title, track, lyrics) {
  const body = container.querySelector(".player-lyrics-copy");
  const lyricText = lyrics || "";

  container.setAttribute("aria-label", `Lyrics for ${title}`);
  body.replaceChildren();

  if (lyricText) {
    lyricText.split(/\n{2,}/).forEach((stanza) => {
      const paragraph = makeElement("p");
      stanza.split("\n").forEach((line, index) => {
        if (index > 0) paragraph.append(document.createElement("br"));
        paragraph.append(document.createTextNode(line));
      });
      body.append(paragraph);
    });
    if (track.lyricsCredit || track.credits) {
      body.append(makeElement("p", "lyrics-credit", track.lyricsCredit || track.credits));
    }
    return;
  }

  body.append(
    makeElement(
      "p",
      "lyrics-unavailable",
      track.lyricsNote || "No lyrics are published for this track."
    )
  );
}

function renderPlayer(mount, releaseId, release, lyricCatalog) {
  const tracks = release.tracks;
  const requestedTrackNumber = Number(mount.dataset.initialTrack);
  const requestedTrackIndex = tracks.findIndex(
    (track) => track.number === requestedTrackNumber
  );
  let currentIndex = requestedTrackIndex >= 0 ? requestedTrackIndex : 0;
  let lyricsIndex = null;
  let sourceIndex = -1;
  let playbackStarted = false;
  let engagedSent = false;
  let listenedMilliseconds = 0;
  let lastPlayingTick = 0;

  const audio = document.createElement("audio");
  audio.preload = "none";
  audio.setAttribute("playsinline", "");
  activeAudio.add(audio);

  const player = makeElement("div", "archive-player");
  player.dataset.releaseId = releaseId;
  player.classList.toggle("is-single-track", tracks.length === 1);

  const stage = makeElement("div", "player-stage");
  const playButton = makeElement("button", "player-play");
  playButton.type = "button";
  const releaseArtwork = mount
    .closest(".download-card")
    ?.querySelector(":scope > picture img, :scope > img");
  if (releaseArtwork) {
    const playArtwork = document.createElement("img");
    playArtwork.className = "player-play-artwork";
    playArtwork.src = releaseArtwork.currentSrc || releaseArtwork.src;
    playArtwork.alt = "";
    playArtwork.loading = "lazy";
    playArtwork.decoding = "async";
    playArtwork.setAttribute("aria-hidden", "true");
    playButton.append(playArtwork);
  }
  const playGlyph = makeElement("span", "player-play-glyph");
  playGlyph.setAttribute("aria-hidden", "true");
  playButton.append(playGlyph);

  const nowPlaying = makeElement("div", "player-now-playing");
  const trackPosition = makeElement("span", "player-position");
  const trackTitle = makeElement("strong", "player-track-title");
  const trackCredit = makeElement("span", "player-track-credit");
  nowPlaying.append(trackPosition, trackTitle, trackCredit);

  const clock = makeElement("span", "player-clock");
  stage.append(playButton, nowPlaying, clock);

  const progressWrap = makeElement("div", "player-progress-wrap");
  const progress = document.createElement("input");
  progress.className = "player-progress";
  progress.type = "range";
  progress.min = "0";
  progress.max = "1000";
  progress.value = "0";
  progress.step = "1";
  progress.setAttribute("aria-label", "Seek");
  progressWrap.append(progress);

  const tools = makeElement("div", "player-tools");
  const skipControls = makeElement("div", "player-skip");
  const previousButton = makeElement("button", "player-step", "Previous");
  const nextButton = makeElement("button", "player-step", "Next");
  previousButton.type = "button";
  nextButton.type = "button";
  skipControls.append(previousButton, nextButton);

  const volumeLabel = makeElement("label", "player-volume");
  const volumeText = makeElement("span", null, "Volume");
  const volume = document.createElement("input");
  volume.type = "range";
  volume.min = "0";
  volume.max = "1";
  volume.value = "1";
  volume.step = "0.05";
  volume.setAttribute("aria-label", "Volume");
  volumeLabel.append(volumeText, volume);

  tools.append(skipControls, volumeLabel);

  const trackList = makeElement("ol", "player-tracklist");
  const lyricsId = `lyrics-${releaseId}`;
  const trackItems = [];
  const lyricsButtons = [];
  const trackButtons = tracks.map((track, index) => {
    const item = makeElement("li", "player-track-item");
    item.id = `${releaseId}-track-${track.number}`;
    const button = makeElement("button", "player-track");
    button.type = "button";
    button.dataset.trackIndex = String(index);
    button.setAttribute("aria-label", `Play ${track.title}`);

    const number = makeElement("span", "player-track-number", String(track.number).padStart(2, "0"));
    const title = makeElement("span", "player-track-name", track.title);
    const duration = makeElement("span", "player-track-duration", formatTime(track.duration));

    button.append(number, title, duration);
    item.append(button);

    const publishedLyrics = lyricCatalog[`${releaseId}:${track.number}`];
    if (publishedLyrics) {
      const lyricsButton = makeElement("button", "player-track-lyrics", "Lyrics");
      lyricsButton.type = "button";
      lyricsButton.setAttribute("aria-expanded", "false");
      lyricsButton.setAttribute("aria-controls", lyricsId);
      lyricsButton.setAttribute("aria-label", `Show lyrics for ${track.title}`);
      item.append(lyricsButton);
      lyricsButtons.push(lyricsButton);
    } else {
      lyricsButtons.push(null);
    }

    trackList.append(item);
    trackItems.push(item);
    return button;
  });

  const lyricsPanel = makeElement("div", "player-lyrics");
  lyricsPanel.id = lyricsId;
  lyricsPanel.setAttribute("role", "region");
  lyricsPanel.hidden = true;
  const lyricsCopy = makeElement("div", "player-lyrics-copy");
  lyricsPanel.append(lyricsCopy);
  trackItems[0].append(lyricsPanel);

  const status = makeElement("p", "player-status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");

  player.append(audio, stage, progressWrap, tools, trackList, status);
  mount.replaceChildren(player);

  function eventParameters(index = currentIndex) {
    const track = tracks[index];
    return {
      project: release.artist,
      release_id: releaseId,
      release_title: release.title,
      track_id: `${releaseId}:${track.number}`,
      track_title: track.title,
      track_number: track.number,
      duration_seconds: Math.round(track.duration)
    };
  }

  function resetPlaybackCycle() {
    playbackStarted = false;
    engagedSent = false;
    listenedMilliseconds = 0;
    lastPlayingTick = 0;
  }

  function stopListeningClock() {
    if (!lastPlayingTick) return;
    listenedMilliseconds += performance.now() - lastPlayingTick;
    lastPlayingTick = 0;
  }

  function ensureSource() {
    if (sourceIndex === currentIndex) return;
    audio.src = tracks[currentIndex].source;
    sourceIndex = currentIndex;
    audio.load();
  }

  function setPlayState(isPlaying) {
    playButton.setAttribute(
      "aria-label",
      `${isPlaying ? "Pause" : "Play"} ${tracks[currentIndex].title}`
    );
    player.classList.toggle("is-playing", isPlaying);
  }

  function updateTrackDisplay() {
    const track = tracks[currentIndex];

    trackPosition.textContent = `Track ${currentIndex + 1} of ${tracks.length}`;
    trackTitle.textContent = track.title;
    trackCredit.textContent = track.credits || "";
    trackCredit.hidden = !track.credits;
    clock.textContent = `0:00 / ${formatTime(track.duration)}`;
    progress.value = "0";
    progress.style.setProperty("--progress", "0%");
    progress.setAttribute("aria-label", `Seek in ${track.title}`);
    progress.setAttribute("aria-valuetext", `0:00 of ${formatTime(track.duration)}`);
    previousButton.disabled = currentIndex === 0;
    nextButton.disabled = currentIndex === tracks.length - 1;
    setPlayState(false);

    trackButtons.forEach((button, index) => {
      if (index === currentIndex) {
        button.setAttribute("aria-current", "true");
      } else {
        button.removeAttribute("aria-current");
      }
      trackItems[index].classList.toggle("is-current", index === currentIndex);
    });
  }

  function toggleLyrics(index) {
    const button = lyricsButtons[index];
    if (!button) return;

    const isSameOpenPanel = lyricsIndex === index && !lyricsPanel.hidden;
    lyricsButtons.forEach((lyricsButton, buttonIndex) => {
      if (!lyricsButton) return;
      const willBeOpen = !isSameOpenPanel && buttonIndex === index;
      lyricsButton.setAttribute("aria-expanded", String(willBeOpen));
      lyricsButton.setAttribute(
        "aria-label",
        `${willBeOpen ? "Hide" : "Show"} lyrics for ${tracks[buttonIndex].title}`
      );
    });

    if (isSameOpenPanel) {
      lyricsPanel.hidden = true;
      lyricsIndex = null;
      return;
    }

    const track = tracks[index];
    const publishedLyrics = lyricCatalog[`${releaseId}:${track.number}`] || "";
    trackItems[index].append(lyricsPanel);
    renderLyrics(lyricsPanel, track.title, track, publishedLyrics);
    lyricsPanel.hidden = false;
    lyricsIndex = index;
    trackEvent("lyrics_view", eventParameters(index));
  }

  async function playCurrent() {
    ensureSource();
    try {
      await audio.play();
      status.textContent = "";
    } catch {
      status.textContent = "Playback could not begin. Downloads remain available below.";
    }
  }

  function selectTrack(index, shouldPlay = true) {
    if (index < 0 || index >= tracks.length) return;

    if (currentIndex !== index) {
      audio.pause();
      currentIndex = index;
      sourceIndex = -1;
      resetPlaybackCycle();
      updateTrackDisplay();

    }

    if (shouldPlay) playCurrent();
  }

  playButton.addEventListener("click", () => {
    if (audio.paused) {
      playCurrent();
    } else {
      audio.pause();
    }
  });

  previousButton.addEventListener("click", () => selectTrack(currentIndex - 1));
  nextButton.addEventListener("click", () => selectTrack(currentIndex + 1));

  trackButtons.forEach((button, index) => {
    button.addEventListener("click", () => selectTrack(index));
  });

  lyricsButtons.forEach((button, index) => {
    button?.addEventListener("click", () => toggleLyrics(index));
  });

  progress.addEventListener("input", () => {
    ensureSource();
    const duration = Number.isFinite(audio.duration) ? audio.duration : tracks[currentIndex].duration;
    const seekTime = (Number(progress.value) / 1000) * duration;

    if (audio.readyState === 0) {
      audio.addEventListener(
        "loadedmetadata",
        () => {
          audio.currentTime = seekTime;
        },
        { once: true }
      );
    } else {
      audio.currentTime = seekTime;
    }
  });

  volume.addEventListener("input", () => {
    audio.volume = Number(volume.value);
    volume.style.setProperty("--volume", `${Number(volume.value) * 100}%`);
  });

  audio.addEventListener("playing", () => {
    activeAudio.forEach((otherAudio) => {
      if (otherAudio !== audio && !otherAudio.paused) otherAudio.pause();
    });

    setPlayState(true);
    if (!lastPlayingTick) lastPlayingTick = performance.now();

    if (!playbackStarted) {
      playbackStarted = true;
      trackEvent("audio_start", eventParameters());
    }
  });

  audio.addEventListener("pause", () => {
    stopListeningClock();
    setPlayState(false);
  });

  ["waiting", "stalled", "seeking"].forEach((eventName) => {
    audio.addEventListener(eventName, stopListeningClock);
  });

  audio.addEventListener("seeked", () => {
    if (!audio.paused && !audio.ended && !lastPlayingTick) {
      lastPlayingTick = performance.now();
    }
  });

  audio.addEventListener("timeupdate", () => {
    const duration = Number.isFinite(audio.duration) ? audio.duration : tracks[currentIndex].duration;
    const ratio = duration > 0 ? audio.currentTime / duration : 0;
    const elapsed = Math.min(duration, audio.currentTime);

    progress.value = String(Math.round(ratio * 1000));
    progress.style.setProperty("--progress", `${ratio * 100}%`);
    clock.textContent = `${formatTime(elapsed)} / ${formatTime(duration)}`;
    progress.setAttribute(
      "aria-valuetext",
      `${formatTime(elapsed)} of ${formatTime(duration)}`
    );

    if (lastPlayingTick && !engagedSent) {
      const cumulative = listenedMilliseconds + (performance.now() - lastPlayingTick);
      if (cumulative >= 30000) {
        engagedSent = true;
        trackEvent("audio_engaged", {
          ...eventParameters(),
          engagement_seconds: 30
        });
      }
    }
  });

  audio.addEventListener("ended", () => {
    trackEvent("audio_complete", eventParameters());
    resetPlaybackCycle();

    if (currentIndex < tracks.length - 1) {
      selectTrack(currentIndex + 1);
    } else {
      setPlayState(false);
      progress.value = "0";
      progress.style.setProperty("--progress", "0%");
    }
  });

  audio.addEventListener("error", () => {
    setPlayState(false);
    status.textContent = "This stream could not be loaded. Downloads remain available below.";
  });

  updateTrackDisplay();
  volume.style.setProperty("--volume", "100%");
}

function openReleaseFromHash() {
  if (!window.location.hash) return;

  const target = document.getElementById(window.location.hash.slice(1));
  const card = target?.closest(".download-card");
  const release = card?.querySelector(".release-details");
  if (release) {
    release.open = true;
    mountReleasePlayer(release);
  }
}

function loadCatalog() {
  if (!catalogRequest) {
    catalogRequest = Promise.all([
      fetch("/music-catalog.json?v=20260727-2").then((response) => {
        if (!response.ok) throw new Error("Catalog request failed");
        return response.json();
      }),
      fetch("/music-lyrics.json?v=20260727-2").then((response) => {
        if (!response.ok) throw new Error("Lyrics request failed");
        return response.json();
      })
    ]);
  }

  return catalogRequest;
}

function restoreCurrentHashPosition(mount) {
  if (!window.location.hash) return;

  const hash = window.location.hash;
  const target = document.getElementById(hash.slice(1));
  const card = mount.closest(".download-card");
  if (!target || !card?.contains(target)) return;

  const scrollToTarget = () => {
    if (window.location.hash !== hash || !target.isConnected) return;
    target.scrollIntoView({ block: "start", behavior: "auto" });
  };
  const scheduleAfterLayout = () => {
    window.setTimeout(() => {
      requestAnimationFrame(() => {
        requestAnimationFrame(scrollToTarget);
      });
    }, 0);
  };

  if (document.readyState === "complete") {
    scheduleAfterLayout();
  } else {
    window.addEventListener("load", scheduleAfterLayout, { once: true });
  }

  document.fonts?.ready.then(() => {
    scheduleAfterLayout();
  }).catch(() => {
    // Font loading must never block fragment navigation.
  });
}

function mountPlayer(mount) {
  if (!mount || mount.dataset.playerState) return;

  mount.dataset.playerState = "loading";
  mount.setAttribute("aria-busy", "true");

  loadCatalog()
    .then(([catalog, lyrics]) => {
      const releaseId = mount.dataset.releasePlayer;
      const release = catalog[releaseId];

      if (!release) {
        throw new Error(`Player data is unavailable for ${releaseId}`);
      }

      renderPlayer(mount, releaseId, release, lyrics);
      mount.dataset.playerState = "ready";
      restoreCurrentHashPosition(mount);
    })
    .catch(() => {
      mount.dataset.playerState = "error";
      const fallbackNote = mount.querySelector(".player-fallback-note");
      if (fallbackNote) {
        fallbackNote.textContent =
          "The interactive player could not load. The track list and downloads remain available.";
      }
    })
    .finally(() => {
      mount.removeAttribute("aria-busy");
    });
}

function mountReleasePlayer(release) {
  mountPlayer(release?.querySelector("[data-release-player]"));
}

releaseDetails.forEach((release) => {
  release.addEventListener("toggle", () => {
    if (release.open) {
      mountReleasePlayer(release);
    } else {
      release.querySelector("audio")?.pause();
    }
  });

  if (release.open) mountReleasePlayer(release);
});

playerMounts.forEach((mount) => {
  if (!mount.closest(".release-details")) mountPlayer(mount);
});

openReleaseFromHash();
window.addEventListener("hashchange", openReleaseFromHash);
