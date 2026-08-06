import { NAV } from './sidebar.js?v=navfix5';
import { API } from '../api.js';

const LABELS = {
  '/demo':      'Showcase',
  '/dashboard': 'Command',
  '/inventory': 'Stock Ops',
  '/orders':    'Orders',
  '/history':   'Logs',
  '/forecasts': 'Demand AI',
  '/stress':    'Risk Lab',
  '/settings':  'Setup',
};

let _notifications = null;
let _loadingNotifications = false;

export function renderHeader(path, opts = {}) {
  const header = document.getElementById('top-header');
  if (!header) return;
  const label = LABELS[path] || 'SmartStock';
  const pending = Number(opts.pending ?? document.getElementById('orders-badge')?.textContent ?? 0);
  const notifications = _notifications || [];

  if (_notifications === null && !_loadingNotifications) {
    _loadingNotifications = true;
    API.alerts()
      .then(alerts => { _notifications = alerts.filter(a => a.active).slice(0, 4).map(alertToNotification); })
      .catch(() => { _notifications = []; })
      .finally(() => {
        _loadingNotifications = false;
        renderHeader(window.location.hash.slice(1) || '/demo', { pending });
      });
  }

  const now = new Date();
  const date = now.toLocaleDateString('en-IN', {
    weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
  });

  header.innerHTML = `
    <button class="header-btn hamburger" id="sidebar-toggle" aria-label="Toggle menu">
      <i data-lucide="menu" style="width:16px;height:16px"></i>
    </button>
    <a class="top-brand" href="#/demo" aria-label="SmartStock Showcase">
      <span>SS</span>
      <div>
        <strong>SmartStock</strong>
        <small>AI Inventory OS</small>
      </div>
    </a>
    <nav class="top-nav" aria-label="Primary navigation">
      ${NAV.map(n => `
        <a class="top-nav-link ${n.route === path ? 'active' : ''}" data-route="${n.route}" href="#${n.route}" title="${n.label}">
          <i data-lucide="${n.icon}" class="nav-icon" style="width:15px;height:15px;flex-shrink:0"></i>
          <span>${n.label}</span>
          ${n.badge ? `<span class="nav-badge" id="orders-badge" style="${pending > 0 ? '' : 'display:none'}">${pending}</span>` : ''}
        </a>
      `).join('')}
    </nav>
    <div class="top-route-chip">
      <span>${label}</span>
    </div>
    <div class="date-display" style="display:flex;align-items:center;gap:4px">
      <i data-lucide="calendar" style="width:12px;height:12px;opacity:.5"></i>
      ${date}
    </div>
    <div class="sync-pill">
      <span class="dot"></span>
      M5 Data
    </div>
    <button class="header-btn" id="notif-btn" aria-label="Notifications" style="position:relative">
      <i data-lucide="bell" style="width:15px;height:15px"></i>
      <span class="notification-dot" style="${notifications.length ? '' : 'display:none'}"></span>
    </button>
    <div class="notification-panel" id="notification-panel" aria-hidden="true">
      <div class="notification-panel-head">
        <div>
          <strong>Alerts center</strong>
          <span>${notifications.length} M5/model-derived signals from SmartStock</span>
        </div>
        <button class="header-btn compact" id="notif-close" aria-label="Close notifications">
          <i data-lucide="x" style="width:14px;height:14px"></i>
        </button>
      </div>
      <div class="notification-list">
        ${notifications.length ? notifications.map(n => `
          <button class="notification-item ${n.tone}" data-route="${n.route}">
            <span class="notification-pulse"></span>
            <div>
              <strong>${n.title}</strong>
              <p>${n.message}</p>
              <small>${n.action}</small>
            </div>
          </button>
        `).join('') : `<div class="notification-empty">No active alerts in the current dataset snapshot.</div>`}
      </div>
    </div>
    <button class="header-btn" id="cmd-btn" aria-label="Command palette" title="⌘K">
      <i data-lucide="search" style="width:15px;height:15px"></i>
    </button>`;

  window.lucide?.createIcons?.();

  document.getElementById('sidebar-toggle')?.addEventListener('click', () => {
    document.querySelector('.top-nav')?.scrollIntoView({ behavior: 'auto', block: 'nearest', inline: 'start' });
  });

  _bindNotifications();
  document.getElementById('cmd-btn')?.addEventListener('click', _openCmd);
}

function alertToNotification(alert) {
  const tone = { critical: 'danger', warning: 'warning', info: 'info', success: 'success' }[alert.severity] || 'info';
  const route = {
    review_order: '/orders',
    review_forecast: '/forecasts',
    review_inventory: '/inventory',
  }[alert.action] || '/dashboard';
  const action = {
    review_order: 'Review orders',
    review_forecast: 'Open Demand AI',
    review_inventory: 'Open inventory',
  }[alert.action] || 'Open dashboard';
  return {
    tone,
    title: labelize(alert.type),
    message: alert.message,
    route,
    action,
  };
}

function labelize(s = '') {
  return s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function _bindNotifications() {
  const btn = document.getElementById('notif-btn');
  const panel = document.getElementById('notification-panel');
  const close = document.getElementById('notif-close');
  if (!btn || !panel) return;

  function setOpen(open) {
    panel.classList.toggle('open', open);
    panel.setAttribute('aria-hidden', open ? 'false' : 'true');
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  btn.addEventListener('click', e => {
    e.stopPropagation();
    setOpen(!panel.classList.contains('open'));
  });

  close?.addEventListener('click', e => {
    e.stopPropagation();
    setOpen(false);
  });

  panel.addEventListener('click', e => {
    e.stopPropagation();
    const item = e.target.closest('[data-route]');
    if (!item) return;
    location.hash = item.dataset.route;
    setOpen(false);
  });

  document.addEventListener('click', e => {
    if (!panel.contains(e.target) && e.target !== btn) setOpen(false);
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') setOpen(false);
  });
}

function _openCmd() {
  const ov = document.getElementById('cmd-overlay');
  if (!ov) return;
  ov.classList.add('open');
  ov.querySelector?.('.cmd-input')?.focus();
}
