import { AppState } from '../state.js';
import { API } from '../api.js';

export const NAV = [
  { route: '/demo',      label: 'Showcase',    icon: 'sparkles' },
  { route: '/dashboard', label: 'Command',     icon: 'panel-top' },
  { route: '/inventory', label: 'Stock Ops',   icon: 'boxes' },
  { route: '/orders',    label: 'Orders',      icon: 'shopping-bag',  badge: true },
  { route: '/forecasts', label: 'Demand AI',   icon: 'activity' },
  { route: '/stress',    label: 'Risk Lab',    icon: 'shield-alert' },
  { route: '/history',   label: 'Logs',        icon: 'history' },
  { route: '/settings',  label: 'Setup',       icon: 'sliders-horizontal' },
];

export async function renderSidebar() {
  const sidebar = document.getElementById('sidebar');
  if (!sidebar) return;

  let pending = 0;
  try {
    const recs = await API.recommendations();
    pending = AppState.pendingCount(recs);
  } catch {}

  sidebar.innerHTML = `
    <div class="nav-logo">
      <div class="nav-logo-text">Smart<span>Stock</span></div>
    </div>
    <div class="nav-section nav-rail-summary">
      <div class="store-selector">
        <i data-lucide="sparkles" style="width:13px;height:13px;flex-shrink:0"></i>
        <span>Navigation moved to top</span>
      </div>
      <div class="store-selector">
        <i data-lucide="shopping-bag" style="width:13px;height:13px;flex-shrink:0"></i>
        <span>${pending} pending orders</span>
      </div>
    </div>
    <div class="nav-bottom">
      <div class="store-selector">
        <i data-lucide="store" style="width:12px;height:12px;flex-shrink:0"></i>
        <span>Bangalore · HSR Layout</span>
      </div>
      <div class="user-chip">
        <div class="user-avatar">R</div>
        <div style="min-width:0;flex:1">
          <div style="font-size:var(--text-sm);font-weight:600;color:var(--text-primary)">Ramesh</div>
          <div style="font-size:var(--text-xs);color:var(--text-tertiary)">Store Manager</div>
        </div>
        <i data-lucide="chevron-up" style="width:12px;height:12px;color:var(--text-tertiary)"></i>
      </div>
    </div>`;

  window.lucide?.createIcons?.();
  _highlightActive();
  return pending;
}

export function updatePendingBadge(count) {
  const badge = document.getElementById('orders-badge');
  if (!badge) return;
  if (count > 0) {
    badge.textContent = count;
    badge.style.display = '';
  } else {
    badge.style.display = 'none';
  }
}

export function _highlightActive() {
  const path = window.location.hash.slice(1) || '/demo';
  document.querySelectorAll('.nav-item, .top-nav-link').forEach(el => {
    el.classList.toggle('active', el.dataset.route === path);
  });
}

// Listen for route changes to update active state
window.addEventListener('hashchange', _highlightActive);
