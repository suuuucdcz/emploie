/* Service worker : coquille applicative en cache, agenda en reseau d'abord. */

const SHELL_CACHE = 'auriga-shell-v7';
const DATA_CACHE = 'auriga-data-v7';

const OFFLINE_PAYLOAD = JSON.stringify({ events: [], error: 'hors ligne', stale: true });

const SHELL = [
  '/',
  '/index.html',
  '/styles.css',
  '/app.js',
  '/manifest.webmanifest',
  '/icons/icon.svg',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((key) => key !== SHELL_CACHE && key !== DATA_CACHE)
          .map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // L'etat du robot est temps reel : jamais de cache, sinon on reaffiche un
  // vieux code A2F.
  if (url.pathname.startsWith('/api/sync/')) return;

  // L'agenda : on tente le reseau, on retombe sur la derniere reponse connue.
  // (Response.json() est trop recente pour Safari iOS < 16.4.)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(DATA_CACHE).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request).then((hit) => hit || new Response(
          OFFLINE_PAYLOAD,
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )))
    );
    return;
  }

  // La coquille : reseau d'abord pour toujours avoir la derniere version.
  event.respondWith(
    fetch(request).then((response) => {
      if (response.ok) {
        const copy = response.clone();
        caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
      }
      return response;
    }).catch(() => caches.match(request))
  );
});
