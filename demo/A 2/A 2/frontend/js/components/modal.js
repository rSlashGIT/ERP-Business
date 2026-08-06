let _onClose = null;

export function openModal(html, opts = {}) {
  _onClose = opts.onClose || null;
  const ov = document.getElementById('modal-overlay');
  ov.innerHTML = `
    <div class="modal animate-in" style="padding:0">
      <div class="modal-header">
        <span class="modal-title">${opts.title || ''}</span>
        <button class="header-btn" id="modal-x" style="font-size:18px;line-height:1">×</button>
      </div>
      <div class="modal-body">${html}</div>
      ${opts.footer ? `<div class="modal-footer">${opts.footer}</div>` : ''}
    </div>`;
  ov.classList.add('open');
  ov.querySelector('#modal-x').onclick = closeModal;
  ov.addEventListener('click', _bgClick);
  document.addEventListener('keydown', _esc);
}

export function closeModal() {
  const ov = document.getElementById('modal-overlay');
  ov.classList.remove('open');
  ov.innerHTML = '';
  ov.removeEventListener('click', _bgClick);
  document.removeEventListener('keydown', _esc);
  if (_onClose) { _onClose(); _onClose = null; }
}

export function openDrawer(html) {
  const d = document.getElementById('drawer');
  const ov = document.getElementById('drawer-overlay');
  d.innerHTML = html;
  d.classList.add('open');
  if (ov) { ov.classList.add('open'); ov.onclick = closeDrawer; }
}

export function closeDrawer() {
  document.getElementById('drawer')?.classList.remove('open');
  const ov = document.getElementById('drawer-overlay');
  if (ov) { ov.classList.remove('open'); ov.onclick = null; }
}

function _bgClick(e) {
  if (e.target === document.getElementById('modal-overlay')) closeModal();
}
function _esc(e) {
  if (e.key === 'Escape') closeModal();
}
