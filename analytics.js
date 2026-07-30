const GA_MEASUREMENT_ID = "G-X4WF8EFKG9";
const CONSENT_KEY = "beareab-analytics-consent-v1";
const GOOGLE_COOKIE_NAMES = ["_ga"];
let analyticsReady = false;

function validMeasurementId() {
  return /^G-[A-Z0-9]+$/i.test(GA_MEASUREMENT_ID);
}

function privacySignalEnabled() {
  const doNotTrackValues = [
    navigator.doNotTrack,
    window.doNotTrack,
    navigator.msDoNotTrack
  ];

  return (
    navigator.globalPrivacyControl === true ||
    doNotTrackValues.some((value) => value === "1" || value === "yes")
  );
}

function readConsent() {
  try {
    return localStorage.getItem(CONSENT_KEY);
  } catch {
    return null;
  }
}

function writeConsent(value) {
  try {
    localStorage.setItem(CONSENT_KEY, value);
  } catch {
    return;
  }
}

function announcePreference(value) {
  const announcement = document.createElement("p");
  announcement.className = "visually-hidden";
  announcement.setAttribute("role", "status");
  announcement.setAttribute("aria-live", "polite");
  announcement.textContent = value === "granted"
    ? "Analytics preference saved: allowed."
    : "Analytics preference saved: declined.";
  document.body.append(announcement);

  window.setTimeout(() => announcement.remove(), 4000);
}

function restoreFocusAfterPrompt(promptHadFocus) {
  if (!promptHadFocus) return;

  const brandLink = document.querySelector(".site-header .brand");
  brandLink?.focus({ preventScroll: true });
}

function loadGoogleAnalytics() {
  if (
    analyticsReady ||
    !validMeasurementId() ||
    privacySignalEnabled() ||
    readConsent() !== "granted"
  ) {
    return;
  }

  window.dataLayer = window.dataLayer || [];
  window.gtag = function gtag() {
    window.dataLayer.push(arguments);
  };

  window.gtag("js", new Date());
  window.gtag("config", GA_MEASUREMENT_ID, {
    allow_ad_personalization_signals: false,
    allow_google_signals: false,
    anonymize_ip: true
  });

  const script = document.createElement("script");
  script.async = true;
  script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(GA_MEASUREMENT_ID)}`;
  document.head.append(script);
  analyticsReady = true;
}

function track(name, parameters = {}) {
  if (!analyticsReady || typeof window.gtag !== "function") return;
  window.gtag("event", name, parameters);
}

function deleteAnalyticsCookies() {
  const hostParts = window.location.hostname.split(".");
  const domains = [
    window.location.hostname,
    `.${window.location.hostname}`,
    hostParts.length > 2 ? `.${hostParts.slice(-2).join(".")}` : null
  ].filter(Boolean);

  document.cookie.split(";").forEach((rawCookie) => {
    const name = rawCookie.split("=")[0].trim();
    if (!GOOGLE_COOKIE_NAMES.some((prefix) => name === prefix || name.startsWith(`${prefix}_`))) return;

    document.cookie = `${name}=; Max-Age=0; path=/; SameSite=Lax`;
    domains.forEach((domain) => {
      document.cookie = `${name}=; Max-Age=0; path=/; domain=${domain}; SameSite=Lax`;
    });
  });
}

function setConsent(value) {
  const prompt = document.querySelector(".analytics-consent");
  const promptHadFocus = prompt?.contains(document.activeElement) === true;

  writeConsent(value);

  if (value === "granted") {
    window[`ga-disable-${GA_MEASUREMENT_ID}`] = false;
    loadGoogleAnalytics();
  } else {
    window[`ga-disable-${GA_MEASUREMENT_ID}`] = true;
    deleteAnalyticsCookies();
  }

  prompt?.remove();
  updatePreferenceStatus();
  announcePreference(value);
  restoreFocusAfterPrompt(promptHadFocus);
}

function clearConsent() {
  try {
    localStorage.removeItem(CONSENT_KEY);
  } catch {
    // Continue clearing cookies and in-memory analytics state even when
    // browser storage is unavailable.
  }

  window[`ga-disable-${GA_MEASUREMENT_ID}`] = true;
  deleteAnalyticsCookies();
  window.location.reload();
}

function updatePreferenceStatus() {
  const status = document.querySelector("[data-analytics-status]");
  if (!status) return;

  if (!validMeasurementId()) {
    status.textContent = "Analytics is not active because the site has not been connected to a GA4 Measurement ID.";
    return;
  }

  if (privacySignalEnabled()) {
    status.textContent = "Analytics is off because your browser is sending a privacy signal.";
    return;
  }

  const consent = readConsent();
  if (consent === "granted") {
    status.textContent = "Analytics is currently allowed on this device.";
  } else if (consent === "denied") {
    status.textContent = "Analytics is currently declined on this device.";
  } else {
    status.textContent = "No analytics choice has been saved on this device.";
  }
}

function showConsentPrompt() {
  if (!validMeasurementId() || privacySignalEnabled() || readConsent()) return;

  const region = document.createElement("section");
  region.className = "analytics-consent";
  region.setAttribute("aria-labelledby", "analytics-consent-title");
  region.setAttribute("aria-describedby", "analytics-consent-description");

  const inner = document.createElement("div");
  inner.className = "analytics-consent-inner";

  const copy = document.createElement("p");
  const title = document.createElement("strong");
  title.id = "analytics-consent-title";
  title.textContent = "Optional analytics.";
  const description = document.createElement("span");
  description.id = "analytics-consent-description";
  description.textContent = "May Google Analytics measure visits, listening, downloads, and support clicks? Names, emails, and payment details are not sent.";
  copy.append(
    title,
    " ",
    description,
    " ",
    Object.assign(document.createElement("a"), {
      href: "/privacy.html",
      textContent: "Details"
    })
  );

  const actions = document.createElement("div");
  actions.className = "analytics-consent-actions";

  const allow = document.createElement("button");
  allow.type = "button";
  allow.textContent = "Allow analytics";
  allow.dataset.consentAction = "allow";
  allow.addEventListener("click", () => setConsent("granted"));

  const decline = document.createElement("button");
  decline.type = "button";
  decline.textContent = "No thanks";
  decline.dataset.consentAction = "decline";
  decline.addEventListener("click", () => setConsent("denied"));

  actions.append(allow, decline);
  inner.append(copy, actions);
  region.append(inner);

  const header = document.querySelector(".site-header");
  if (header) {
    header.insertAdjacentElement("afterend", region);
  } else {
    document.body.prepend(region);
  }
}

function downloadFormatFromUrl(url) {
  if (url.includes("-flac.zip")) return "flac_zip";
  if (url.includes("-mp3-320.zip")) return "mp3_320_zip";
  if (url.includes("-mp3.zip")) return "mp3_128_zip";
  return null;
}

function observeSiteEvents() {
  document.addEventListener("click", (event) => {
    const target = event.target.closest("a[href]");
    if (!target) return;

    const href = target.href;
    const downloadFormat = downloadFormatFromUrl(href);

    if (downloadFormat) {
      const card = target.closest(".download-card");
      const section = target.closest(".download-catalog");
      track("release_download", {
        project: section?.id || "",
        release_id: card?.id?.replace(/-download$/, "") || "",
        release_title: card?.querySelector("h3")?.textContent?.trim() || "",
        download_format: downloadFormat
      });
    }

    if (href.startsWith("https://www.paypal.com/ncp/payment/")) {
      track("support_handoff", {
        support_provider: "paypal",
        support_placement: "support_page"
      });
    }
  });

  document.querySelectorAll("[data-analytics-choice]").forEach((button) => {
    button.addEventListener("click", () => {
      const choice = button.dataset.analyticsChoice;
      if (choice === "reset") {
        clearConsent();
      } else if (choice === "allow") {
        setConsent("granted");
      } else if (choice === "deny") {
        setConsent("denied");
      }
    });
  });
}

window.beareabAnalytics = {
  track,
  allow: () => setConsent("granted"),
  decline: () => setConsent("denied"),
  reset: clearConsent
};

document.addEventListener("DOMContentLoaded", () => {
  observeSiteEvents();
  updatePreferenceStatus();

  if (validMeasurementId() && !privacySignalEnabled()) {
    if (readConsent() === "granted") {
      loadGoogleAnalytics();
    } else {
      showConsentPrompt();
    }
  }
});
