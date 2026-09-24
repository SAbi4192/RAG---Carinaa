/**
 * Service-worker registration for the PWA.
 *
 * Kept to a tiny explicit call from main.tsx rather than automatic side-effect,
 * for one reason the rest of Carinaa already follows: a background worker must
 * not install without something that looks like a decision. The register only
 * runs in a secure context (https or localhost - the browser forbids service
 * workers otherwise), and only on production builds; a development server would
 * otherwise cache stale modules and make hot-reload appear to randomly stop
 * working, which is the classic service-worker footgun.
 */
export function registerServiceWorker(): void {
  if (typeof window === "undefined") return;
  if (!import.meta.env.PROD) return;
  if (!("serviceWorker" in navigator)) return;
  // Service workers require a secure context. `isSecureContext` is false on
  // plain http over a LAN address, exactly where a demo might run, and the
  // registration would throw a confusing error there.
  if (!window.isSecureContext) return;

  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .catch(() => {
        /* A failed registration must never block the app. Everything works
           online without a worker; the worker only adds offline shell speed. */
      });
  });
}
