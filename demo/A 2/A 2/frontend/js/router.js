const routes = {
  '/demo':      () => import('./screens/demo.js?v=navfix7'),
  '/dashboard': () => import('./screens/dashboard.js?v=accuracy4'),
  '/inventory':  () => import('./screens/inventory.js?v=accuracy4'),
  '/orders':     () => import('./screens/orders.js?v=accuracy4'),
  '/history':    () => import('./screens/history.js?v=accuracy4'),
  '/forecasts':  () => import('./screens/forecasts.js?v=accuracy4'),
  '/stress':     () => import('./screens/stress.js?v=accuracy4'),
  '/settings':   () => import('./screens/settings.js?v=accuracy4'),
};

let _current = null;
const _listeners = [];

export function navigate(path) {
  window.location.hash = path;
}

export function onRouteChange(fn) {
  _listeners.push(fn);
}

export function initRouter() {
  window.addEventListener('hashchange', _handleRoute);
  _handleRoute();
}

async function _handleRoute() {
  const path = window.location.hash.slice(1) || '/demo';
  if (path === _current) return;
  _current = path;
  document.body.classList.remove('demo-mode', 'steam-demo-mode');

  const container = document.getElementById('screen-content');
  if (!container) return;
  container._cleanup?.();
  container._cleanup = null;

  container.innerHTML = `
    <div style="padding:0">
      <div class="skeleton" style="height:28px;width:200px;margin-bottom:20px;border-radius:6px"></div>
      <div class="grid grid-cols-4 gap-4" style="margin-bottom:24px">
        ${Array(4).fill('<div class="skeleton" style="height:88px;border-radius:8px"></div>').join('')}
      </div>
      <div class="skeleton" style="height:220px;border-radius:8px"></div>
    </div>`;

  document.querySelectorAll('.nav-item, .top-nav-link').forEach(el => {
    el.classList.toggle('active', el.dataset.route === path);
  });

  _listeners.forEach(fn => fn(path));

  const loader = routes[path] || routes['/dashboard'];
  try {
    const mod = await loader();
    // Yield to browser before rendering — lets the browser paint the skeleton
    // and register the click interaction quickly, dramatically reducing INP
    await new Promise(resolve => setTimeout(resolve, 0));
    container.innerHTML = '';
    // Yield again after clearing, before heavy render work begins
    await new Promise(resolve => requestAnimationFrame(resolve));
    await mod.render(container);
  } catch (err) {
    console.error('[router] screen load failed:', err);
    container.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-icon">⚠</div>
        <div class="empty-state-title">Failed to load screen</div>
        <div class="empty-state-sub">${err.message}</div>
      </div>`;
  }
}
