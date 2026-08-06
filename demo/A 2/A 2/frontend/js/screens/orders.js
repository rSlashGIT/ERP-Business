import { API } from '../api.js';
import { AppState } from '../state.js';
import { showToast } from '../components/toast.js';
import { openModal, closeModal } from '../components/modal.js';
import { applyDefaults, lineChart, sparkline, C } from '../components/chart-helpers.js?v=accuracy4';
import { updatePendingBadge } from '../components/sidebar.js';
import { deriveRecommendation, inventoryStatus, projectedInventorySeries, selectedForecastColor, selectedForecastLabel, selectedForecastSeries } from '../domain/policy.js?v=accuracy4';

let _recs = [], _skus = [], _inv = {}, _fc = {}, _ex = {};
let _filter = 'all';
let _selectedId = null;
let _settings = null;

export async function render(container) {
  applyDefaults();
  [_recs, _skus, _inv, _fc, _ex] = await Promise.all([
    API.recommendations(), API.skus(), API.inventory(),
    API.forecasts(), API.explanations(),
  ]);
  _settings = AppState.getSettings();

  // Check for pre-selected SKU from navigation
  const preSelect = sessionStorage.getItem('orders_sku');
  if (preSelect) { _selectedId = preSelect; sessionStorage.removeItem('orders_sku'); }
  if (!_selectedId && _recs.length) _selectedId = _recs[0].sku_id;

  container.innerHTML = `
    <div id="orders-layout">
      <!-- LEFT PANE -->
      <div id="orders-left">
        <div style="padding:var(--space-4) var(--space-4) var(--space-3);border-bottom:1px solid var(--border-subtle)">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
            <div style="font-size:var(--text-sm);font-weight:600;color:var(--text-primary)">Today's Orders</div>
            <span class="badge badge-neutral" id="order-count">${_recs.length}</span>
          </div>
          <div class="filter-pills" id="order-filter">
            ${['all','pending','approved','modified','skipped'].map(f =>
              `<span class="pill ${f === _filter ? 'active' : ''}" data-f="${f}">${f.charAt(0).toUpperCase()+f.slice(1)}</span>`
            ).join('')}
          </div>
        </div>
        <div style="flex:1;overflow-y:auto" id="sku-list">
          ${buildSkuList()}
        </div>
      </div>

      <!-- CENTER PANE -->
      <div id="orders-center" id="orders-center-pane">
        <div id="center-content">${_selectedId ? '' : emptyCenter()}</div>
      </div>

      <!-- RIGHT PANE -->
      <div id="orders-right">
        <div id="right-content">${_selectedId ? '' : ''}</div>
      </div>
    </div>`;

  window.lucide?.createIcons?.();

  // Filter pills
  document.getElementById('order-filter').addEventListener('click', e => {
    const pill = e.target.closest('.pill');
    if (!pill) return;
    _filter = pill.dataset.f;
    document.querySelectorAll('#order-filter .pill').forEach(p => p.classList.toggle('active', p.dataset.f === _filter));
    document.getElementById('sku-list').innerHTML = buildSkuList();
    _bindSkuList();
  });

  _bindSkuList();
  if (_selectedId) loadSku(_selectedId);

  const offSettings = AppState.on('settings', settings => {
    _settings = settings;
    document.getElementById('sku-list').innerHTML = buildSkuList();
    _bindSkuList();
    if (_selectedId) loadSku(_selectedId);
  });
  container._cleanup = () => offSettings?.();
}

function buildSkuList() {
  const decisions = AppState.getDecisions();
  const decided = new Map(decisions.map(d => [d.sku_id, d]));

  const rows = _recs.filter(r => {
    if (_filter === 'all') return true;
    const d = decided.get(r.sku_id);
    if (_filter === 'pending') return !d;
    return d?.action === _filter;
  });

  if (!rows.length) return `<div class="empty-state" style="padding:var(--space-8) var(--space-4)">
    <div class="empty-state-title">No orders in this filter</div>
  </div>`;

  return rows.map(r => {
    const sku = _skus.find(s => s.id === r.sku_id);
    const liveRec = deriveRecommendation(r, sku, _inv[r.sku_id], _fc[r.sku_id], _settings);
    const d   = decided.get(r.sku_id);
    const dotCls = d ? (d.action === 'approved' ? 'dot-success' : d.action === 'modified' ? 'dot-info' : 'dot-warning') : 'dot-warning';
    const urgBars = { low: 1, medium: 2, high: 3, critical: 3 }[liveRec.urgency] || 1;
    const isActive = r.sku_id === _selectedId;
    return `<div class="order-sku-card ${isActive ? 'active' : ''}" data-sku="${r.sku_id}">
      <div class="order-sku-icon" style="background:${catColor(sku?.category)}">${catEmoji(sku?.category)}</div>
      <div style="flex:1;min-width:0">
        <div style="font-size:var(--text-sm);font-weight:600;color:var(--text-primary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${sku?.name || r.sku_id}</div>
        <div style="font-size:var(--text-xs);font-family:var(--font-mono);color:var(--text-tertiary)">${r.sku_id}</div>
        <div style="font-size:var(--text-xs);color:var(--text-tertiary);display:flex;align-items:center;gap:6px;margin-top:2px">
          <span style="font-family:var(--font-mono);font-weight:600;color:var(--text-primary)">${d?.qty || liveRec.recommended_order_qty}</span> units
          ${d ? `<span class="badge ${d.action==='approved'?'badge-success':d.action==='modified'?'badge-info':'badge-warning'}" style="font-size:10px;padding:1px 5px">${d.action}</span>` : ''}
        </div>
      </div>
      <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">
        <span class="severity-dot ${dotCls}"></span>
        <div style="display:flex;gap:2px">${Array(3).fill(0).map((_,i) =>
          `<div style="width:3px;height:${8 + i*4}px;border-radius:2px;background:${i < urgBars ? 'var(--accent-400)' : 'var(--bg-3)'}"></div>`
        ).join('')}</div>
      </div>
    </div>`;
  }).join('');
}

function _bindSkuList() {
  document.querySelectorAll('.order-sku-card').forEach(card => {
    card.addEventListener('click', () => {
      _selectedId = card.dataset.sku;
      document.querySelectorAll('.order-sku-card').forEach(c => c.classList.toggle('active', c.dataset.sku === _selectedId));
      loadSku(_selectedId);
    });
  });
}

async function loadSku(skuId) {
  const sourceRec  = _recs.find(r => r.sku_id === skuId);
  const sku  = _skus.find(s => s.id === skuId);
  const inv  = _inv[skuId] || {};
  const fc   = _fc[skuId]  || {};
  const ex   = _ex[skuId]  || {};
  const d    = AppState.getDecision(skuId);
  if (!sourceRec || !sku) return;
  const rec = deriveRecommendation(sourceRec, sku, inv, fc, _settings);

  const decided  = !!d;
  const pct      = Math.min(100, ((inv.total_stock || 0) / (inv.max_stock || 1)) * 100);
  const rpPct    = Math.min(100, ((inv.reorder_point || 0) / (inv.max_stock || 1)) * 100);
  const days     = ['Today','D+1','D+2','D+3','D+4','D+5','D+6'];
  const orderId  = `ORD-${new Date().toISOString().slice(0,10).replace(/-/g,'')}-${skuId.slice(-3)}`;
  const st = inventoryStatus(inv.days_of_stock, _settings);
  const stBadge = { critical: 'badge-danger', warning: 'badge-warning', healthy: 'badge-success', over: 'badge-info' }[st];
  const selectedDemand = selectedForecastSeries(fc, _settings);
  const projectedStock = projectedInventorySeries(inv, rec.recommended_order_qty, fc, _settings);
  // Stock trajectory if NO order is placed — shows the stockout risk visually.
  // Clip at 0 so the chart's Y-axis stays positive (the policy still uses the
  // raw value for decisions; this is purely a display choice).
  const projectedStockNoOrder = projectedInventorySeries(inv, 0, fc, _settings)
    .map(v => Math.max(0, v));
  const reorderLine = days.map(() => inv.reorder_point || 0);

  document.getElementById('center-content').innerHTML = `
    <div class="animate-in">
      <!-- SKU header -->
      <div style="margin-bottom:var(--space-5)">
        <div style="font-size:var(--text-xs);color:var(--text-tertiary);font-family:var(--font-mono);margin-bottom:4px">${sku.category} / ${skuId}</div>
        <div style="font-size:var(--text-xl);font-weight:700;color:var(--text-primary);letter-spacing:-.02em">${sku.name}</div>
        <div style="font-size:var(--text-xs);color:var(--text-tertiary);margin-top:4px">Supplier: ${sku.supplier} · Cost: ₹${sku.unit_cost}/unit</div>
      </div>

      <!-- Stock bar -->
      <div class="card" style="margin-bottom:var(--space-5)">
        <div class="card-header" style="margin-bottom:var(--space-3)">
          <div class="card-title">Current Stock Position</div>
          <span class="badge ${stBadge}">${inv.days_of_stock?.toFixed(1)}d of stock · threshold ${_settings.dosFactor}d</span>
        </div>
        <div class="grid grid-cols-3 gap-3" style="margin-bottom:var(--space-4)">
          <div><div style="font-size:var(--text-xs);color:var(--text-tertiary)">Total Stock</div>
            <div style="font-size:var(--text-2xl);font-weight:700;font-family:var(--font-mono);color:var(--text-primary)">${inv.total_stock || 0}</div></div>
          <div><div style="font-size:var(--text-xs);color:var(--text-tertiary)">Avg Daily Demand</div>
            <div style="font-size:var(--text-2xl);font-weight:700;font-family:var(--font-mono);color:var(--text-primary)">${inv.avg_daily_demand || '—'}</div></div>
          <div><div style="font-size:var(--text-xs);color:var(--text-tertiary)">Reorder Point</div>
            <div style="font-size:var(--text-2xl);font-weight:700;font-family:var(--font-mono);color:var(--text-primary)">${inv.reorder_point || '—'}</div></div>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:var(--text-xs);color:var(--text-tertiary);margin-bottom:4px">
          <span>0</span><span>Reorder: ${inv.reorder_point}</span><span>Max: ${inv.max_stock}</span>
        </div>
        <div class="stock-range-bar">
          <div class="stock-range-fill" style="width:${pct}%;background:${inv.days_of_stock<=2?'var(--danger-fg)':inv.days_of_stock<=5?'var(--warning-fg)':'var(--accent-400)'}"></div>
          <div class="stock-marker" style="left:${rpPct}%"></div>
        </div>
      </div>

      <!-- Forecast chart -->
      <div class="chart-container" style="margin-bottom:var(--space-5)">
        <div class="card-header" style="margin-bottom:var(--space-4)">
          <div class="card-title">Projected Stock — Next 7 Days</div>
          <span class="badge badge-accent">${rec.selected_model_label} · ${rec.recommended_order_qty} unit order</span>
        </div>
        <div class="chart-wrap" style="height:180px">
          <canvas id="orders-chart"></canvas>
        </div>
      </div>

      <!-- AI Recommendation -->
      <div class="card card-accent" style="margin-bottom:var(--space-5)">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:var(--space-4)">
          <div>
            <div style="font-size:var(--text-xs);color:var(--text-tertiary);text-transform:uppercase;letter-spacing:.06em;margin-bottom:var(--space-2)">AI Recommendation</div>
            <div style="font-size:var(--text-4xl);font-weight:700;font-family:var(--font-mono);color:var(--text-primary);letter-spacing:-.04em">${rec.recommended_order_qty}</div>
            <div style="font-size:var(--text-sm);color:var(--text-secondary);margin-top:4px">units · covers ${rec.expected_coverage_days?.toFixed(1)} days · est. ₹${rec.estimated_cost?.toLocaleString('en-IN')}</div>
          </div>
          <div style="text-align:right;flex-shrink:0">
            <div style="font-size:var(--text-xs);color:var(--text-tertiary);margin-bottom:4px">Confidence</div>
            <div style="font-size:var(--text-2xl);font-weight:700;font-family:var(--font-mono);color:var(--accent-400)">${(rec.confidence * 100).toFixed(0)}%</div>
            <div style="font-size:var(--text-xs);color:var(--text-tertiary)">Urgency: ${rec.urgency}</div>
          </div>
        </div>
        <div style="margin-top:var(--space-3)">
          <div class="progress-bar"><div class="progress-fill" style="width:${(rec.confidence*100).toFixed(0)}%"></div></div>
        </div>
      </div>

      <!-- Decision buttons -->
      ${decided ? `
        <div style="background:var(--bg-2);border:1px solid var(--border-subtle);border-radius:var(--radius-lg);padding:var(--space-4);margin-bottom:var(--space-4)">
          <div style="font-size:var(--text-sm);font-weight:500;color:var(--text-primary)">
            ✓ Decision recorded: <strong>${d.action}</strong> — ${d.qty} units
          </div>
          <div style="font-size:var(--text-xs);color:var(--text-tertiary);margin-top:4px">
            ${d.timestamp} · <a href="#/history" class="section-link">View in Order History →</a>
          </div>
        </div>
      ` : `
        <div class="decision-btn-row" style="margin-bottom:var(--space-4)">
          <button class="btn btn-primary" id="approve-btn" style="flex:1">
            <i data-lucide="check" style="width:14px;height:14px"></i>
            Approve ${rec.recommended_order_qty} units
          </button>
          <button class="btn btn-secondary" id="modify-btn">
            <i data-lucide="edit-2" style="width:14px;height:14px"></i>
            Modify
          </button>
          <button class="btn btn-ghost" id="skip-btn">Skip</button>
        </div>
      `}

      ${decided ? `
        <div style="font-size:var(--text-xs);color:var(--text-tertiary)">
          Order ID: <span style="font-family:var(--font-mono)">${orderId}</span>
        </div>
      ` : ''}
    </div>`;

  window.lucide?.createIcons?.();

  // Draw forecast chart
  if (fc.chronos) {
    setTimeout(() => {
      const canvas = document.getElementById('orders-chart');
      if (canvas) lineChart(canvas, days, [
        { label: `Projected stock with ${rec.recommended_order_qty} unit order`, data: projectedStock, borderColor: C.green, backgroundColor: C.greenA, borderWidth: 2.6, pointRadius: 2, fill: { target: 'origin', above: 'hsla(160,75%,52%,0.08)' }, tension: 0.25 },
        { label: 'Projected stock without order', data: projectedStockNoOrder, borderColor: C.red, borderWidth: 1.6, pointRadius: 2, fill: false, tension: 0.25, borderDash: [4, 3] },
        { label: `Reorder point (${inv.reorder_point || 0})`, data: reorderLine, borderColor: 'hsl(220,10%,55%)', borderWidth: 1, pointRadius: 0, fill: false, tension: 0, borderDash: [2, 4] },
      ]);
    }, 50);
  }

  // Right pane
  loadRightPane(rec, fc, ex, sku);

  // Decision handlers
  if (!decided) {
    document.getElementById('approve-btn')?.addEventListener('click', () => approveOrder(rec, sku));
    document.getElementById('modify-btn')?.addEventListener('click', () => openModifyModal(rec, sku, inv));
    document.getElementById('skip-btn')?.addEventListener('click',   () => showSkipReason(rec, sku));
  }
}

function loadRightPane(rec, fc, ex, sku) {
  const factors = ex?.top_factors || [];
  const cfs     = ex?.counterfactuals || [];
  const mq      = rec.model_quantities || {};
  const consensus_pct = rec.consensus_status === 'high' ? 7 : rec.consensus_status === 'medium' ? 15 : 25;

  document.getElementById('right-content').innerHTML = `
    <div class="animate-right">
      <div style="font-size:var(--text-sm);font-weight:600;color:var(--text-primary);margin-bottom:4px">Why this recommendation?</div>
      <div style="font-size:var(--text-xs);color:var(--text-tertiary);margin-bottom:var(--space-5)">AI Decision Explainer · OAT perturbation analysis</div>

      <!-- Key factors -->
      <div style="margin-bottom:var(--space-5)">
        <div style="font-size:var(--text-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--text-tertiary);margin-bottom:var(--space-3)">Key Factors</div>
        ${factors.map(f => `
          <div class="factor-bar-row">
            <div class="factor-label" style="font-size:var(--text-xs)">${f.label}</div>
            <div class="factor-bar-wrap"><div class="factor-bar-fill" style="width:${(f.weight*100).toFixed(0)}%;background:${f.direction==='up'?'var(--accent-400)':'var(--danger-fg)'}"></div></div>
            <div class="factor-pct">${(f.weight*100).toFixed(0)}%</div>
            <div class="factor-dir" style="color:${f.direction==='up'?'var(--success-fg)':'var(--danger-fg)'}">${f.direction==='up'?'↑':'↓'}</div>
          </div>`).join('')}
      </div>

      <div class="divider"></div>

      <!-- Counterfactuals -->
      <div style="margin:var(--space-4) 0">
        <div style="font-size:var(--text-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--text-tertiary);margin-bottom:var(--space-3)">What if...</div>
        ${cfs.map(cf => {
          const deltaNum = parseInt(cf.delta);
          const deltaColor = deltaNum > 0 ? 'var(--success-fg)' : (deltaNum < 0 ? 'var(--danger-fg)' : 'var(--text-tertiary)');
          const deltaStr = deltaNum === 0 ? '±0' : cf.delta;
          return `
          <div class="cf-item" onclick="this.querySelector('.cf-detail').style.display=this.querySelector('.cf-detail').style.display==='none'?'block':'none'">
            <div style="display:flex;justify-content:space-between;align-items:center;gap:var(--space-2)">
              <span style="color:var(--text-secondary)">${cf.change}</span>
              <span style="font-family:var(--font-mono);font-size:var(--text-xs)">
                <strong style="color:var(--text-primary);font-size:var(--text-sm)">${cf.new_qty} units</strong>
                <span style="color:${deltaColor};margin-left:6px">(Δ ${deltaStr})</span>
              </span>
            </div>
            <div class="cf-detail" style="display:none;margin-top:6px;font-size:var(--text-xs);color:var(--text-tertiary)">
              AI would order <strong style="color:var(--text-primary)">${cf.new_qty}</strong> units under this scenario (current: ${rec.recommended_order_qty}).
            </div>
          </div>`;
        }).join('')}
      </div>

      <div class="divider"></div>

      <!-- Model consensus -->
      <div style="margin:var(--space-4) 0">
        <div style="font-size:var(--text-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--text-tertiary);margin-bottom:var(--space-3)">Model Consensus</div>
        <div class="consensus-pills">
          ${mq.lightgbm ? `<div class="model-pill"><span class="name">LightGBM</span><span class="val">${mq.lightgbm}</span></div>` : ''}
          ${mq.nhits    ? `<div class="model-pill"><span class="name">N-HITS</span><span class="val">${mq.nhits}</span></div>` : ''}
          ${mq.chronos  ? `<div class="model-pill"><span class="name">Chronos</span><span class="val">${mq.chronos}</span></div>` : ''}
        </div>
        <div style="margin-top:var(--space-3);font-size:var(--text-xs);color:${rec.consensus_status==='high'?'var(--success-fg)':'var(--warning-fg)'}">
          ${rec.consensus_status === 'high' ? `✓ All 3 models agree within ${consensus_pct}%` : `⚠ Models diverge by ~${consensus_pct}% — review manually`}
        </div>
      </div>

      <div class="divider"></div>

      <!-- Historical performance sparkline -->
      <div style="margin-top:var(--space-4)">
        <div style="font-size:var(--text-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--text-tertiary);margin-bottom:var(--space-3)">Recent Demand (14 days)</div>
        <div style="height:48px;position:relative">
          <canvas id="hist-spark"></canvas>
        </div>
        <div style="font-size:var(--text-xs);color:var(--text-tertiary);margin-top:6px">
          Accuracy last 14d: <strong style="color:var(--text-primary)">${formatAccuracy(ex?.accuracy_last_14d)}</strong>
        </div>
      </div>
    </div>`;

  // Sparkline
  if (fc.actual_recent_30) {
    setTimeout(() => {
      const canvas = document.getElementById('hist-spark');
      if (canvas) sparkline(canvas, fc.actual_recent_30.slice(-14), C.blue);
    }, 50);
  }
}

function approveOrder(rec, sku) {
  const ts = new Date().toLocaleTimeString('en-IN', { hour:'2-digit', minute:'2-digit', second:'2-digit' });
  AppState.recordDecision({
    sku_id: rec.sku_id,
    sku_name: sku?.name || rec.sku_id,
    action: 'approved',
    qty: rec.recommended_order_qty,
    ai_recommendation: rec.recommended_order_qty,
    timestamp: ts,
    date: new Date().toISOString().split('T')[0],
    your_decision: 'approved',
    outcome: 'healthy',
    cost: rec.estimated_cost || 0,
    performance_score: rec.confidence,
  });

  showToast(`✓ Order approved: ${rec.recommended_order_qty} units of ${sku?.name || rec.sku_id}`, 'success');
  _refreshListAndBadge();
  loadSku(rec.sku_id);

  // Auto-advance to next pending
  setTimeout(() => {
    const nextPending = _recs.find(r => r.sku_id !== rec.sku_id && !AppState.isDecided(r.sku_id));
    if (nextPending) {
      _selectedId = nextPending.sku_id;
      document.querySelectorAll('.order-sku-card').forEach(c => c.classList.toggle('active', c.dataset.sku === _selectedId));
      loadSku(_selectedId);
    } else {
      showAllDoneMessage();
    }
  }, 900);
}

function showAllDoneMessage() {
  document.getElementById('center-content').innerHTML = `
    <div class="empty-state" style="height:100%">
      <div style="font-size:48px">✓</div>
      <div class="empty-state-title">All orders reviewed for today</div>
      <div class="empty-state-sub">Great work, Ramesh. All ${_recs.length} recommendations have been actioned.</div>
      <a href="#/history" class="btn btn-primary" style="margin-top:var(--space-4)">View Order History</a>
    </div>`;
  document.getElementById('right-content').innerHTML = '';
}

function openModifyModal(rec, sku, inv) {
  const aiQty = rec.recommended_order_qty;
  openModal(`
    <div>
      <div style="margin-bottom:var(--space-4)">
        <div style="font-size:var(--text-sm);color:var(--text-tertiary)">AI Recommendation: <strong style="color:var(--text-primary)">${aiQty} units</strong></div>
      </div>
      <div style="margin-bottom:var(--space-4)">
        <div style="font-size:var(--text-xs);color:var(--text-tertiary);margin-bottom:6px">Your Order Quantity</div>
        <div class="number-input-wrap">
          <button class="stepper-btn" id="qty-minus">−</button>
          <input class="input stepper-input" id="mod-qty" type="number" min="0" max="5000" value="${aiQty}">
          <button class="stepper-btn" id="qty-plus">+</button>
        </div>
        <div id="delta-info" style="font-size:var(--text-xs);color:var(--text-tertiary);margin-top:var(--space-2)">No change from AI recommendation</div>
      </div>
      <div style="margin-bottom:var(--space-4)">
        <div style="font-size:var(--text-xs);color:var(--text-tertiary);margin-bottom:6px">Reason for modification</div>
        <select class="select" id="mod-reason" style="width:100%;margin-bottom:var(--space-2)">
          <option value="">Select reason...</option>
          <option value="budget">Budget constraint</option>
          <option value="storage">Storage limit</option>
          <option value="demand">My demand estimate differs</option>
          <option value="supplier">Supplier constraint</option>
          <option value="other">Other</option>
        </select>
      </div>
      <div style="display:flex;gap:var(--space-3);justify-content:flex-end">
        <button class="btn btn-ghost" onclick="(()=>{const m=document.getElementById('modal-overlay');m.classList.remove('open');m.innerHTML=''})()">Cancel</button>
        <button class="btn btn-primary" id="save-mod-btn">Save Modification</button>
      </div>
    </div>
  `, { title: `Modify Order — ${sku?.name || rec.sku_id}` });

  const qtyInput = document.getElementById('mod-qty');
  const deltaInfo = document.getElementById('delta-info');

  function updateDelta() {
    const qty = parseInt(qtyInput.value) || 0;
    const diff = qty - aiQty;
    const days = inv.avg_daily_demand ? (qty / inv.avg_daily_demand).toFixed(1) : '—';
    const cost = qty * (sku?.unit_cost || 0);
    deltaInfo.innerHTML = diff === 0
      ? 'No change from AI recommendation'
      : `<span style="color:${diff > 0 ? 'var(--success-fg)' : 'var(--danger-fg)'}">${diff > 0 ? '+' : ''}${diff} units vs AI</span> · ${days}d coverage · ₹${cost.toLocaleString('en-IN')}`;
  }

  qtyInput.addEventListener('input', updateDelta);
  document.getElementById('qty-minus').onclick = () => { qtyInput.value = Math.max(0, parseInt(qtyInput.value || 0) - 50); updateDelta(); };
  document.getElementById('qty-plus').onclick  = () => { qtyInput.value = parseInt(qtyInput.value || 0) + 50; updateDelta(); };

  document.getElementById('save-mod-btn').onclick = () => {
    const qty = parseInt(qtyInput.value) || 0;
    const reason = document.getElementById('mod-reason').value;
    if (!reason) { showToast('Please select a reason for modification', 'warning'); return; }

    AppState.recordDecision({
      sku_id: rec.sku_id,
      sku_name: sku?.name || rec.sku_id,
      action: 'modified',
      qty,
      ai_recommendation: aiQty,
      timestamp: new Date().toLocaleTimeString('en-IN'),
      date: new Date().toISOString().split('T')[0],
      your_decision: 'modified',
      final_qty: qty,
      reason,
      outcome: 'healthy',
      cost: qty * (sku?.unit_cost || 0),
      performance_score: 0.75,
    });

    closeModal();
    showToast(`Order modified: ${qty} units of ${sku?.name || rec.sku_id}`, 'info');
    _refreshListAndBadge();
    loadSku(rec.sku_id);
  };
}

function showSkipReason(rec, sku) {
  const reasons = ['Out of budget', 'Already ordered', 'Demand forecast too high', 'Other'];
  document.getElementById('center-content').insertAdjacentHTML('afterbegin', `
    <div id="skip-panel" style="background:var(--bg-2);border:1px solid var(--border-subtle);border-radius:var(--radius-lg);padding:var(--space-4);margin-bottom:var(--space-4)">
      <div style="font-size:var(--text-sm);font-weight:500;color:var(--text-primary);margin-bottom:var(--space-3)">Why are you skipping this order?</div>
      <div style="display:flex;flex-direction:column;gap:var(--space-2)">
        ${reasons.map(r => `<button class="btn btn-secondary btn-sm skip-reason-btn" data-reason="${r}">${r}</button>`).join('')}
      </div>
    </div>`);

  document.querySelectorAll('.skip-reason-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      AppState.recordDecision({
        sku_id: rec.sku_id,
        sku_name: sku?.name || rec.sku_id,
        action: 'skipped',
        qty: 0,
        ai_recommendation: rec.recommended_order_qty,
        timestamp: new Date().toLocaleTimeString('en-IN'),
        date: new Date().toISOString().split('T')[0],
        your_decision: 'skipped',
        final_qty: 0,
        reason: btn.dataset.reason,
        outcome: 'healthy',
        cost: 0,
        performance_score: 0.5,
      });

      document.getElementById('skip-panel')?.remove();
      showToast(`Skipped: ${sku?.name || rec.sku_id} — ${btn.dataset.reason}`, 'warning');
      _refreshListAndBadge();
      loadSku(rec.sku_id);
    });
  });
}

function _refreshListAndBadge() {
  document.getElementById('sku-list').innerHTML = buildSkuList();
  _bindSkuList();
  const pending = AppState.pendingCount(_recs);
  updatePendingBadge(pending);
}

function emptyCenter() {
  return `<div class="empty-state" style="height:100%;padding:var(--space-12) 0">
    <div class="empty-state-icon">📋</div>
    <div class="empty-state-title">Select a SKU from the left</div>
    <div class="empty-state-sub">Review the AI recommendation and make your decision</div>
  </div>`;
}

function formatAccuracy(acc) {
  if (typeof acc === 'number') return `${(acc * 100).toFixed(0)}%`;
  if (acc && typeof acc.score === 'number') return `${(acc.score * 100).toFixed(0)}%`;
  if (acc && acc.total) return `${Math.round((acc.within_20pct || 0) / acc.total * 100)}%`;
  return '—';
}

function catColor(cat) {
  return { FOODS: 'hsl(160,40%,18%)', HOBBIES: 'hsl(210,40%,18%)', HOUSEHOLD: 'hsl(40,40%,18%)' }[cat] || 'var(--bg-3)';
}

function catEmoji(cat) {
  return { FOODS: '🛒', HOBBIES: '🎯', HOUSEHOLD: '🏠' }[cat] || '📦';
}
