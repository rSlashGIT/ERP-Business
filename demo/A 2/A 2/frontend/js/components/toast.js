const ICONS = { success: '✓', warning: '⚠', error: '✕', info: 'ℹ' };

export function showToast(msg, type = 'info', ms = 3500) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `
    <span class="toast-icon">${ICONS[type] || 'ℹ'}</span>
    <span class="toast-msg">${msg}</span>
    <button class="toast-close" title="Dismiss">×</button>
  `;
  el.querySelector('.toast-close').onclick = () => _dismiss(el);
  container.appendChild(el);
  setTimeout(() => _dismiss(el), ms);
}

function _dismiss(el) {
  if (!el.isConnected) return;
  el.style.transition = 'opacity 200ms ease, transform 200ms ease';
  el.style.opacity = '0';
  el.style.transform = 'translateX(12px)';
  setTimeout(() => el.remove(), 200);
}
