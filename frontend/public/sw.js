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
const VERSION = "carinaa-shell-v1";

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

  // Same-origin navigation and assets: cache-first for speed, then network to
  // refresh; but a navigation request itself (not an asset) should fall through
  // to the network on a cache MISS so a deep link like /app/chat/7 is not turned
  // into a 404 by an absent shell entry.
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
        /* Offline on a cold navigation: hand back the cached shell root. */
        if (request.mode === "navigate") {
          const shell = await caches.match("/index.html");
          if (shell) return shell;
        }
        throw cause;
      }
    })(),
  );
});
