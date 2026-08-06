import { AppState } from '../state.js';
import { showToast } from '../components/toast.js';

let _activeTab = 'params';

export async function render(container) {
  container.innerHTML = `
    <div class="animate-in">
      <div class="screen-header">
        <div>
          <h1>Settings</h1>
          <div class="subtitle">Configure business parameters and preferences</div>
        </div>
      </div>

      <div class="tabs" id="settings-tabs">
        <div class="tab-item ${_activeTab==='params'?'active':''}" data-tab="params">Business Parameters</div>
        <div class="tab-item ${_activeTab==='notif'?'active':''}" data-tab="notif">Notifications</div>
        <div class="tab-item ${_activeTab==='about'?'active':''}" data-tab="about">About</div>
      </div>

      <div id="tab-content">${renderTab(_activeTab)}</div>
    </div>`;

  document.getElementById('settings-tabs').addEventListener('click', e => {
    const tab = e.target.closest('.tab-item');
    if (!tab) return;
    _activeTab = tab.dataset.tab;
    document.querySelectorAll('#settings-tabs .tab-item').forEach(t => t.classList.toggle('active', t.dataset.tab === _activeTab));
    document.getElementById('tab-content').innerHTML = renderTab(_activeTab);
    _bindTab();
  });

  _bindTab();
}

function renderTab(tab) {
  const s = AppState.getSettings();
  if (tab === 'params') return `
    <div style="max-width:640px">
      <div class="settings-card">
        <div class="settings-card-header">
          <div style="font-size:var(--text-sm);font-weight:600;color:var(--text-primary)">Cost Parameters</div>
          <div style="font-size:var(--text-xs);color:var(--text-tertiary);margin-top:2px">Demo defaults — production deployment connects these to the backend optimization engine (future work)</div>
        </div>
        <div class="settings-card-body">
          <div class="settings-row">
            <div>
              <div class="settings-label">Holding cost per unit per day</div>
              <div class="settings-desc">Cost of storing unsold inventory</div>
            </div>
            <div class="settings-value" style="display:flex;align-items:center;gap:var(--space-2)">
              <span style="color:var(--text-tertiary)">₹</span>
              <input class="input" id="hold-cost" type="number" min="0.01" max="1" step="0.01" value="${s.holdCost}" style="width:80px;text-align:right">
            </div>
          </div>
          <div class="settings-row">
            <div>
              <div class="settings-label">Stockout penalty per unit</div>
              <div class="settings-desc">Cost when demand cannot be met</div>
            </div>
            <div class="settings-value" style="display:flex;align-items:center;gap:var(--space-2)">
              <span style="color:var(--text-tertiary)">₹</span>
              <input class="input" id="stockout-penalty" type="number" min="0.5" max="20" step="0.5" value="${s.stockoutPenalty}" style="width:80px;text-align:right">
            </div>
          </div>
        </div>
      </div>

      <div class="settings-card">
        <div class="settings-card-header">
          <div style="font-size:var(--text-sm);font-weight:600;color:var(--text-primary)">AI Model Preference</div>
        </div>
        <div class="settings-card-body">
          <div class="settings-row">
            <div>
              <div class="settings-label">Preferred forecasting model</div>
              <div class="settings-desc">Chronos-Bolt has the lowest RMSE (23.21)</div>
            </div>
            <div class="settings-value">
              <select class="select" id="pref-model" title="Ensemble (avg) = mean of Chronos-Bolt and N-HITS forecasts">
                <option value="auto" ${s.preferredModel==='auto'?'selected':''}>Auto (best RMSE)</option>
                <option value="chronos" ${s.preferredModel==='chronos'?'selected':''}>Chronos-Bolt</option>
                <option value="nhits" ${s.preferredModel==='nhits'?'selected':''}>N-HITS</option>
                <option value="lightgbm" ${s.preferredModel==='lightgbm'?'selected':''}>LightGBM</option>
                <option value="ensemble" ${s.preferredModel==='ensemble'?'selected':''} title="Mean of Chronos-Bolt and N-HITS forecasts">Ensemble (avg)</option>
              </select>
            </div>
          </div>
          <div class="settings-row">
            <div>
              <div class="settings-label">Alert threshold (days of stock)</div>
              <div class="settings-desc">Show critical alert below this many days</div>
            </div>
            <div class="settings-value">
              <div style="display:flex;align-items:center;gap:var(--space-2)">
                <input class="slider" id="dos-slider" type="range" min="1" max="7" value="${s.dosFactor}" style="width:100px">
                <span id="dos-val" style="font-family:var(--font-mono);color:var(--text-primary)">${s.dosFactor}d</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:var(--space-5)">
        <button class="btn btn-ghost btn-sm" id="reset-defaults">Reset to defaults</button>
        <div style="display:flex;align-items:center;gap:var(--space-2)">
          <span class="dot-success" style="width:6px;height:6px;border-radius:50%;background:var(--success-fg);display:inline-block"></span>
          <span id="settings-apply-state" style="font-size:var(--text-xs);color:var(--text-tertiary)">Live · auto-saves</span>
        </div>
      </div>
    </div>`;

  if (tab === 'notif') return `
    <div style="max-width:640px">
      <div class="settings-card">
        <div class="settings-card-header">
          <div style="font-size:var(--text-sm);font-weight:600;color:var(--text-primary)">Alert Preferences</div>
          <div style="font-size:var(--text-xs);color:var(--text-tertiary);margin-top:2px">Demo mode — notifications shown in-app only</div>
        </div>
        <div class="settings-card-body">
          ${notifRow('Stockout risk alerts', 'Critical stock level warnings', true)}
          ${notifRow('Demand spike alerts', 'Unusual demand pattern detected', true)}
          ${notifRow('Model disagreement alerts', 'When forecasters diverge >20%', true)}
          ${notifRow('Daily summary', 'Morning briefing digest', false)}
          ${notifRow('Order completion', 'When all daily orders are reviewed', true)}
        </div>
      </div>
      <div class="card" style="margin-top:var(--space-4)">
        <div style="font-size:var(--text-sm);color:var(--text-secondary)">
          Push notifications, email digests, and Slack integration are available in the production version.
          This demo shows all notifications in-app as toast messages.
        </div>
      </div>
    </div>`;

  if (tab === 'about') return `
    <div style="max-width:640px">
      <div class="settings-card">
        <div class="settings-card-header">
          <div style="font-size:var(--text-sm);font-weight:600;color:var(--text-primary)">SmartStock</div>
          <span class="badge badge-neutral">v1.0 · May 2026</span>
        </div>
        <div class="settings-card-body">
          <p style="font-size:var(--text-sm);color:var(--text-secondary);line-height:1.65;margin-bottom:var(--space-4)">
            SmartStock is a research prototype demonstrating explainable AI for retail inventory replenishment.
            It combines classical (s,S) inventory policies with modern AI demand forecasting — LightGBM,
            N-HITS, and Amazon Chronos-Bolt — to produce daily ordering recommendations with full explainability.
          </p>
          <p style="font-size:var(--text-sm);color:var(--text-secondary);line-height:1.65">
            Built on real M5 Walmart competition data (FOODS_3_090, CA_1 store, 1,941 days).
            All results use seed 42 and a strict 60/20/20 train/val/test split with zero test-set leakage.
            Policy benchmarking compares Grid-Tuned classical (s,S) against CMA-ES across six disruption scenarios.
            Grid-Tuned wins all six — a reward-hacking case study reported transparently. OAT sensitivity analysis included.
          </p>
        </div>
      </div>

      <div class="settings-card">
        <div class="settings-card-header"><div style="font-size:var(--text-sm);font-weight:600;color:var(--text-primary)">Team</div></div>
        <div class="settings-card-body">
          <div class="settings-row">
            <div><div class="settings-label">Ashrit K</div><div class="settings-desc">Architecture, Policy, Benchmarking, Testing — designed adaptive (s,S) policy, ran Grid-Tuned vs CMA-ES benchmark, wrote 51-test pytest suite</div></div>
            <span class="badge badge-accent">LEAD</span>
          </div>
          <div class="settings-row">
            <div><div class="settings-label">Kumar Videh</div><div class="settings-desc">LightGBM, N-HITS, Chronos-Bolt evaluation on 389-day M5 test set</div></div>
            <span class="badge badge-neutral">FORECASTING</span>
          </div>
          <div class="settings-row">
            <div><div class="settings-label">Abhishek Tiwary</div><div class="settings-desc">Eight-screen dashboard, explainability layer, audit hooks</div></div>
            <span class="badge badge-neutral">FRONTEND</span>
          </div>
        </div>
      </div>

      <div class="settings-card">
        <div class="settings-card-header"><div style="font-size:var(--text-sm);font-weight:600;color:var(--text-primary)">Tech Stack</div></div>
        <div class="settings-card-body">
          <div style="display:flex;flex-wrap:wrap;gap:var(--space-2)">
            ${['Vanilla JS ES6 Modules','Chart.js 4.4','Lucide Icons','CSS Custom Properties','localStorage state'].map(t => `<span class="badge badge-neutral">${t}</span>`).join('')}
          </div>
        </div>
      </div>

      <div style="display:flex;gap:var(--space-3);margin-top:var(--space-4);flex-wrap:wrap">
        <a class="btn btn-secondary btn-sm" href="https://github.com/Videhhh30/smartstock-inventory" target="_blank">
          <i data-lucide="github" style="width:13px;height:13px"></i>
          GitHub
        </a>
        <a class="btn btn-secondary btn-sm" href="https://www.kaggle.com/competitions/m5-forecasting-accuracy" target="_blank">
          M5 Dataset (Kaggle)
        </a>
        <span class="badge badge-neutral">MIT License</span>
      </div>
    </div>`;

  return '';
}

function notifRow(label, desc, defaultOn) {
  const id = 'notif-' + label.replace(/\s+/g, '-').toLowerCase();
  return `<div class="settings-row">
    <div>
      <div class="settings-label">${label}</div>
      <div class="settings-desc">${desc}</div>
    </div>
    <div class="toggle ${defaultOn ? 'on' : ''}" id="${id}" onclick="this.classList.toggle('on')"></div>
  </div>`;
}

function _bindTab() {
  window.lucide?.createIcons?.();

  let applyTimer = null;
  const readPatch = () => {
    const patch = {};
    const hc = document.getElementById('hold-cost');
    const sp = document.getElementById('stockout-penalty');
    const pm = document.getElementById('pref-model');
    const ds = document.getElementById('dos-slider');
    if (hc) patch.holdCost = parseFloat(hc.value) || 0.02;
    if (sp) patch.stockoutPenalty = parseFloat(sp.value) || 2.00;
    if (pm) patch.preferredModel = pm.value;
    if (ds) patch.dosFactor = parseInt(ds.value) || 3;
    return patch;
  };

  const markApplied = text => {
    const state = document.getElementById('settings-apply-state');
    if (state) state.textContent = text;
  };

  const applyLive = () => {
    clearTimeout(applyTimer);
    markApplied('Saving…');
    applyTimer = setTimeout(() => {
      AppState.updateSettings(readPatch());
      markApplied('Live · auto-saves');
    }, 120);
  };

  document.getElementById('dos-slider')?.addEventListener('input', e => {
    document.getElementById('dos-val').textContent = e.target.value + 'd';
    applyLive();
  });

  ['hold-cost', 'stockout-penalty'].forEach(id => {
    document.getElementById(id)?.addEventListener('input', applyLive);
    document.getElementById(id)?.addEventListener('change', applyLive);
  });
  document.getElementById('pref-model')?.addEventListener('change', applyLive);

  document.getElementById('reset-defaults')?.addEventListener('click', () => {
    AppState.updateSettings({ holdCost: 0.02, stockoutPenalty: 2.00, preferredModel: 'auto', dosFactor: 3 });
    document.getElementById('tab-content').innerHTML = renderTab('params');
    _bindTab();
    showToast('Settings reset to defaults', 'info');
  });
}
