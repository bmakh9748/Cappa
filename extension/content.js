// Runs on youtube.com. Every ~700ms it snapshots which video is playing and
// the exact playback position, and hands it to the background worker (content
// scripts can't reach localhost directly; the worker can). The Cappa app reads
// this to auto-select the video and to pin a card's moment by position.

(function () {
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

  function snapshot() {
    const vid = videoId();
    if (!vid) return;
    const v = document.querySelector("video");
    const payload = {
      videoId: vid,
      url: location.href,
      title: document.title.replace(/ - YouTube$/, ""),
      currentTime: v ? v.currentTime : null,
      paused: v ? v.paused : true,
      duration: v && isFinite(v.duration) ? v.duration : null
    };
    try {
      chrome.runtime.sendMessage({ type: "cappa-state", payload });
    } catch (e) {
      // extension context was invalidated (reloaded); ignore.
    }
  }

  setInterval(snapshot, 700);
  snapshot();
})();
