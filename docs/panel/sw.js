// 积分面板 Service Worker（2026-09-27 Zero）
// 目标：离线时能看上次的数据——summary 接口响应缓存为「上次数据」
// 策略：页面+API=网络优先（在线永远最新），断网退回缓存；POST（refresh/token）一律直放
const CACHE = 'points-v2-snap';
const CORE = ['./index.html', './manifest.json', './icon-192.png', './icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => Promise.all(CORE.map(u => c.add(u).catch(() => {}))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return; // refresh/token POST 不缓存
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  // snapshot.bin（离线快照）：网络优先拿最新，失败退回缓存
  if (url.pathname.endsWith('/snapshot.bin')) {
    e.respondWith(
      fetch(req, { cache: 'no-store' }).then(res => {
        const cp = res.clone();
        caches.open(CACHE).then(c => c.put(req, cp));
        return res;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // summary 数据接口：网络优先拿最新，失败退回上次缓存（离线看数据的关键）
  if (url.pathname.indexOf('/points/api/summary') !== -1) {
    e.respondWith(
      fetch(req).then(res => {
        if (res.ok) {
          const cp = res.clone();
          caches.open(CACHE).then(c => c.put(req, cp));
        }
        return res;
      }).catch(() => caches.match(req))
    );
    return;
  }

  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req, { cache: 'no-store' }).then(res => {
        const cp = res.clone();
        caches.open(CACHE).then(c => c.put('./index.html', cp));
        return res;
      }).catch(() => caches.match('./index.html').then(hit => hit || Response.error()))
    );
    return;
  }

  // 其余静态资源：缓存优先回填
  e.respondWith(
    caches.match(req).then(hit => hit || fetch(req).then(res => {
      const cp = res.clone();
      caches.open(CACHE).then(c => c.put(req, cp));
      return res;
    }).catch(() => hit))
  );
});
