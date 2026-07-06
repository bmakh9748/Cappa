// Forwards each snapshot from the content script to the Cappa app on localhost.
// The service worker (unlike a content script) is allowed to reach 127.0.0.1
// because the host is declared in host_permissions, so this sidesteps the
// page's mixed-content / CORS / private-network restrictions.
//
// It also ships the user's youtube.com cookies to the app (localhost only,
// nothing leaves the machine): YouTube bot-checks yt-dlp's anonymous fetches
// ("Sign in to confirm you're not a bot") and logged-in cookies pass it.
// yt-dlp can't decrypt current Chrome/Edge cookie stores on Windows itself,
// but an extension reads them natively via chrome.cookies.

const ENDPOINT = "http://127.0.0.1:8765/state";
const COOKIE_ENDPOINT = "http://127.0.0.1:8765/cookies";
const COOKIE_INTERVAL_MS = 10 * 60 * 1000; // refresh every 10 minutes

let lastCookiePush = 0;

function pushCookies() {
  lastCookiePush = Date.now();
  chrome.cookies.getAll({ domain: "youtube.com" }, (cookies) => {
    if (chrome.runtime.lastError || !cookies || !cookies.length) return;
    fetch(COOKIE_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cookies })
    }).catch(() => {});
  });
}

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.type !== "cappa-state") return;
  // Piggyback the periodic cookie refresh on the state ticks: the worker may
  // have been suspended, so timers can't be trusted, but ticks always flow
  // while a YouTube tab is open.
  if (Date.now() - lastCookiePush > COOKIE_INTERVAL_MS) pushCookies();
  fetch(ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(msg.payload)
  }).catch(() => {
    // Cappa app not running / bridge down: nothing to do, try again next tick.
  });
});

pushCookies(); // and once at startup

// Installing or REPLACING the extension kills the content script in every
// YouTube tab that is already open, and Chrome only injects into new page
// loads — so a replaced extension used to go silently dead (Cappa stuck on
// "yt: idle") until the user happened to reload the tab. Re-inject into the
// open tabs ourselves; content.js guards against running twice.
chrome.runtime.onInstalled.addListener(() => {
  chrome.tabs.query({ url: "*://www.youtube.com/*" }, (tabs) => {
    if (chrome.runtime.lastError || !tabs) return;
    for (const tab of tabs) {
      chrome.scripting
        .executeScript({ target: { tabId: tab.id }, files: ["content.js"] })
        .catch(() => {}); // discarded/errored tab: the next page load injects.
    }
  });
});
