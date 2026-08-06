import { initRouter, onRouteChange } from './router.js?v=accuracy4';
import { renderSidebar }            from './components/sidebar.js?v=navfix5';
import { renderHeader }             from './components/header.js?v=navfix5';
import { applyDefaults }            from './components/chart-helpers.js?v=accuracy4';
import { AppState }                 from './state.js';
import { API }                      from './api.js';

async function boot() {
  // Apply Chart.js defaults early
  applyDefaults();

  // Render persistent layout components
  const pending = await renderSidebar();
  renderHeader(window.location.hash.slice(1) || '/demo', { pending });

  // Update header on route changes
  onRouteChange(path => {
    renderHeader(path);
    // Close mobile sidebar on navigate
    document.getElementById('sidebar')?.classList.remove('open');
  });

  // Seed history on first load
  await _seedHistory();

  // Command palette
  _initCommandPalette();

  // Global keyboard shortcuts
  document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      document.getElementById('cmd-overlay')?.classList.toggle('open');
      document.querySelector('.cmd-input')?.focus();
    }
  });

  // Start router last (triggers first screen render)
  initRouter();
}

async function _seedHistory() {
  // Only seed if no decisions exist yet (first-time visit)
  const existing = AppState.getDecisions();
  if (existing.length > 0) return;
  // Don't add seed data to decisions — history screen merges seed JSON directly
}

function _initCommandPalette() {
  const ov = document.getElementById('cmd-overlay');
  if (!ov) return;

  const COMMANDS = [
    { label: 'Showcase',       action: () => location.hash = '/demo',      kbd: 'S' },
    { label: 'Command',        action: () => location.hash = '/dashboard', kbd: 'C' },
    { label: 'Stock Ops',      action: () => location.hash = '/inventory', kbd: 'I' },
    { label: 'Orders',         action: () => location.hash = '/orders',    kbd: 'O' },
    { label: 'Logs',           action: () => location.hash = '/history',   kbd: 'R' },
    { label: 'Demand AI',      action: () => location.hash = '/forecasts', kbd: 'D' },
    { label: 'Risk Lab',       action: () => location.hash = '/stress',    kbd: 'K' },
    { label: 'Setup',          action: () => location.hash = '/settings',  kbd: ',' },
    { label: 'Export History CSV', action: () => { location.hash = '/history'; } },
  ];

  ov.innerHTML = `
    <div class="cmd-box">
      <div class="cmd-input-wrap">
        <i data-lucide="search" style="width:15px;height:15px;color:var(--text-tertiary);flex-shrink:0"></i>
        <input class="cmd-input" placeholder="Jump to screen or action..." autocomplete="off" spellcheck="false">
      </div>
      <div class="cmd-results" id="cmd-results">
        ${COMMANDS.map(c => `
          <div class="cmd-item" data-label="${c.label}">
            <i data-lucide="chevron-right" style="width:13px;height:13px;opacity:.4"></i>
            <span>${c.label}</span>
            ${c.kbd ? `<span class="cmd-kbd">${c.kbd}</span>` : ''}
          </div>`).join('')}
      </div>
      <div class="cmd-footer">
        <span class="cmd-hint"><span class="cmd-key">↑↓</span> navigate</span>
        <span class="cmd-hint"><span class="cmd-key">↵</span> select</span>
        <span class="cmd-hint"><span class="cmd-key">Esc</span> close</span>
      </div>
    </div>`;

  window.lucide?.createIcons?.();

  const input = ov.querySelector('.cmd-input');
  let focused = 0;

  function getVisible() {
    return Array.from(document.querySelectorAll('.cmd-item:not([style*="display:none"])'));
  }

  function updateFocus(items) {
    items.forEach((el, i) => el.classList.toggle('focused', i === focused));
  }

  input.addEventListener('input', () => {
    const q = input.value.toLowerCase();
    document.querySelectorAll('.cmd-item').forEach(el => {
      el.style.display = el.dataset.label.toLowerCase().includes(q) ? '' : 'none';
    });
    focused = 0;
    updateFocus(getVisible());
  });

  input.addEventListener('keydown', e => {
    const items = getVisible();
    if (e.key === 'ArrowDown') { e.preventDefault(); focused = (focused + 1) % items.length; updateFocus(items); }
    if (e.key === 'ArrowUp')   { e.preventDefault(); focused = (focused - 1 + items.length) % items.length; updateFocus(items); }
    if (e.key === 'Enter') {
      const label = items[focused]?.dataset.label;
      const cmd = COMMANDS.find(c => c.label === label);
      if (cmd) { cmd.action(); _closeCmd(); }
    }
    if (e.key === 'Escape') _closeCmd();
  });

  document.querySelectorAll('.cmd-item').forEach((el, i) => {
    el.addEventListener('click', () => {
      const cmd = COMMANDS.find(c => c.label === el.dataset.label);
      if (cmd) { cmd.action(); _closeCmd(); }
    });
  });

  ov.addEventListener('click', e => { if (e.target === ov) _closeCmd(); });

  function _closeCmd() {
    ov.classList.remove('open');
    input.value = '';
    document.querySelectorAll('.cmd-item').forEach(el => { el.style.display = ''; el.classList.remove('focused'); });
    focused = 0;
  }
}

// Bootstrap
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
