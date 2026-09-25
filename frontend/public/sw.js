/*
 * Carinaa service worker.
 *
 * WHAT IT CACHES, AND WHY THAT IS A CAREFUL CHOICE
 * ------------------------------------------------
 * The app shell (the HTML, the hashed JS/CSS bundles, the icons). That is what
 * makes Carinaa installable and what lets a launched window open instantly.
 *
 * It deliberately does NOT cache `/api/*` - not network-first, not even
 * network-only with a fallback. The reason is the product itself: an answer, a
 * trace, and its grounding verdict are facts about a moment in time. Serving one
 * from a cache weeks later - after the documents changed, or the provider
 * recovered - would present an old truth as current, which is the exact failure
 * every other part of Carinaa is built to prevent. So an offline app can open and
 * read its shell, but any real question fails the honest way the backend
 * already fails it: "the server is unreachable". A stale answer is worse than no
 * answer.
 *
 * Streaming (`/api/chat/ask/stream`) is passed through untouched for the same
 * reason, and because a streaming response must not be intercepted at all.
 *
 * SHELL UPDATES
 * -------------
 * Precache entries are versioned by build (the register script passes the
 * injected build time). On a new deployment, `activate` clears the old shell
 * cache, so an installed client always fetches fresh bundles rather than
 * resurrecting stale JavaScript that might no longer match the API.
 */

// Replaced at build time by the register script when it exists; harmless
// literal default so the file works even opened directly in development.
const VERSION = "carinaa-shell-v2";

const SHELL_CACHE = `${VERSION}-shell`;

const PRECACHE = [
  "/",
  "/index.html",
  "/manifest.webmanifest",
  "/icon-192.png",
  "/icon-512.png",
  "/apple-touch-icon.png",
];

// Routes that must NEVER be handled by this worker at all.
function isApiRequest(url) {
  return url.pathname === "/api" || url.pathname.startsWith("/api/");
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
      .catch(() => {
        /* a precache miss must not abort installation; runtime caching still helps */
      }),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((key) => key !== SHELL_CACHE).map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  // API and streaming always hit the network. No cache read, no cache write.
  if (isApiRequest(url)) return;

  // NAVIGATIONS ARE NETWORK-FIRST. THIS IS THE IMPORTANT RULE.
  // The app shell is a tiny index.html that references content-hashed JS/CSS
  // bundles. If a rebuild replaces those bundles on disk and a navigation is
  // answered CACHE-FIRST, the browser gets the OLD index.html pointing at
  // hashed files that no longer exist - every chunk 404s and the screen goes
  // black. That is exactly what happened once and must never happen again.
  // So: a navigation always asks the network for fresh HTML, and only falls
  // back to the cached shell when offline. Static assets below stay cache-first
  // for speed - that is safe, because a fresh HTML names NEW hashed filenames
  // that are not in cache yet, so the genuinely-new files always reach the
  // network, and the old ones are simply never referenced again.
  if (request.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const response = await fetch(request);
          const copy = response.clone();
          const cache = await caches.open(SHELL_CACHE);
          cache.put("/index.html", copy);
          return response;
        } catch (cause) {
          /* Offline on a cold navigation: hand back the cached shell root. */
          const shell = await caches.match("/index.html");
          if (shell) return shell;
          throw cause;
        }
      })(),
    );
    return;
  }

  // Same-origin assets: cache-first (content-hashed, so safe), then network.
  event.respondWith(
    (async () => {
      const cached = await caches.match(request);
      if (cached) return cached;

      try {
        const response = await fetch(request);
        const sameOriginAsset =
          url.origin === self.location.origin &&
          !url.pathname.startsWith("/src/") &&
          response.ok &&
          (request.destination === "style" ||
            request.destination === "script" ||
            request.destination === "image" ||
            request.destination === "font" ||
            url.pathname.endsWith(".js") ||
            url.pathname.endsWith(".css"));
        if (sameOriginAsset) {
          const copy = response.clone();
          const cache = await caches.open(SHELL_CACHE);
          cache.put(request, copy);
        }
        return response;
      } catch (cause) {
        throw cause;
      }
    })(),
  );
});
