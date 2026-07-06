// Runs on youtube.com. Every ~700ms it snapshots which video is playing and
// the exact playback position, and hands it to the background worker (content
// scripts can't reach localhost directly; the worker can). The Cappa app reads
// this to auto-select the video and to pin a card's moment by position.

(function () {
  // This file is injected twice into a tab that was open when the extension
  // was installed or replaced: once by the manifest's content_scripts on page
  // load, once by the worker's install-time re-injection. Only one copy may
  // run the posting loop.
  if (window.__cappaBridgeActive) return;
  window.__cappaBridgeActive = true;

  function videoId() {
    try {
      const u = new URL(location.href);
      if (u.pathname === "/watch") return u.searchParams.get("v");
      const m = u.pathname.match(/^\/(?:shorts|embed)\/([A-Za-z0-9_-]{11})/);
      return m ? m[1] : null;
    } catch (e) {
      return null;
    }
  }

  function activeVideo() {
    // Shorts (and the home-page inline preview) keep SEVERAL <video>
    // elements alive at once — the feed preloads the neighbouring shorts —
    // and the first in DOM order is often a paused preload stuck at t=0.
    // Taking that one told the app the click happened at the START of the
    // video, so cards got the first caption line's audio. Prefer elements
    // that are actually playing; break ties (or an all-paused page, e.g.
    // the user paused) by which shows the most pixels in the viewport.
    const vids = Array.from(document.querySelectorAll("video"));
    if (!vids.length) return null;
    const playing = vids.filter((v) => !v.paused && v.readyState >= 2);
    const pool = playing.length ? playing : vids;
    let best = null;
    let bestArea = -1;
    for (const v of pool) {
      const r = v.getBoundingClientRect();
      const w = Math.min(r.right, innerWidth) - Math.max(r.left, 0);
      const h = Math.min(r.bottom, innerHeight) - Math.max(r.top, 0);
      const area = w > 0 && h > 0 ? w * h : 0;
      if (area > bestArea) {
        bestArea = area;
        best = v;
      }
    }
    return best;
  }

  function snapshot() {
    // Every YouTube tab runs this script and the app keeps the LATEST
    // report — a background tab with a different video must stay silent,
    // or the two tabs fight over which video Cappa aligns cards to.
    if (document.hidden) return;
    const vid = videoId();
    if (!vid) return;
    const v = activeVideo();
    const payload = {
      videoId: vid,
      url: location.href,
      title: document.title.replace(/ - YouTube$/, ""),
      currentTime: v ? v.currentTime : null,
      paused: v ? v.paused : true,
      duration: v && isFinite(v.duration) ? v.duration : null,
      // Surfaced in the app's tooltip: proves which extension version is
      // actually running (edited files do nothing until Chrome reloads it).
      ext: chrome.runtime.getManifest ? chrome.runtime.getManifest().version : "?"
    };
    try {
      chrome.runtime.sendMessage({ type: "cappa-state", payload });
    } catch (e) {
      // Extension replaced/reloaded: this copy can never reach the worker
      // again. Stop its loop and let go of the guard so the NEW extension's
      // injected copy takes over.
      clearInterval(timer);
      window.__cappaBridgeActive = false;
    }
  }

  const timer = setInterval(snapshot, 700);
  // Returning to this tab updates the app immediately instead of waiting
  // out the interval (background timers are throttled, so it could lag).
  document.addEventListener("visibilitychange", snapshot);
  snapshot();
})();
