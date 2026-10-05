/* XAVI service worker: makes the site installable and shows a friendly page when you are offline.
   It never caches pages or data (prices and portfolios must always be live); it only keeps the
   look-and-feel files so the app opens instantly. Bump CACHE when the shell files change. */
const CACHE = 'xavi-shell-v1';
const SHELL = ['/offline', '/static/style.css', '/static/icons/icon-192.png', '/static/icons/favicon-32.png'];

self.addEventListener('install', function (event) {
    event.waitUntil(caches.open(CACHE).then(function (cache) { return cache.addAll(SHELL); })
        .then(function () { return self.skipWaiting(); }));
});

self.addEventListener('activate', function (event) {
    event.waitUntil(caches.keys().then(function (keys) {
        return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); }));
});

self.addEventListener('fetch', function (event) {
    var request = event.request;
    if (request.method !== 'GET') { return; }                       // never touch forms / API calls
    var url = new URL(request.url);
    if (url.origin !== location.origin) { return; }                 // fonts etc. go straight to the network
    if (request.mode === 'navigate') {                              // pages: network only, offline page as fallback
        event.respondWith(fetch(request).catch(function () { return caches.match('/offline'); }));
        return;
    }
    if (url.pathname.indexOf('/static/') === 0) {                   // look-and-feel files: network first, cache if offline
        event.respondWith(fetch(request).then(function (response) {
            var copy = response.clone();
            caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
            return response;
        }).catch(function () { return caches.match(request); }));
    }
});
