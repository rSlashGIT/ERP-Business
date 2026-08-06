import { API } from '../api.js';
import { applyDefaults, sparkline, lineChart, C } from '../components/chart-helpers.js?v=accuracy4';
import { openDrawer, closeDrawer } from '../components/modal.js';
import { AppState } from '../state.js';
import { inventoryStatus, selectedForecastColor, selectedForecastLabel, selectedForecastSeries } from '../domain/policy.js?v=accuracy4';

let _skus = [], _inv = {}, _fc = {}, _ex = {};
let _search = '', _cat = 'all', _status = 'all', _store = 'all';
let _sortCol = 'dos', _sortDir = 1;
let _selectedIdx = -1;
let _settings = null;

export async function render(container) {
  applyDefaults();
  [_skus, _inv, _fc, _ex] = await Promise.all([
    API.skus(), API.inventory(), API.forecasts(), API.explanations(),
  ]);
  _settings = AppState.getSettings();

  container.innerHTML = `
    <div class="animate-in">
      <div class="screen-header">
        <div>
          <h1>Inventory</h1>
      <div class="subtitle" id="inv-subtitle">${_skus.length} SKUs across 3 stores · critical below ${_settings.dosFactor} days</div>
        </div>
      </div>

      <!-- Controls -->
      <div class="inventory-controls">
        <div class="input-icon-wrap" style="flex:1;min-width:200px;max-width:300px">
          <i data-lucide="search" class="icon-left" style="width:14px;height:14px"></i>
          <input class="input" id="inv-search" placeholder="Search SKUs..." autocomplete="off">
        </div>
        <select class="select" id="inv-cat">
          <option value="all">All Categories</option>
          <option value="FOODS">Foods</option>
          <option value="HOBBIES">Hobbies</option>
          <option value="HOUSEHOLD">Household</option>
        </select>
        <select class="select" id="inv-status">
          <option value="all">All Status</option>
          <option value="critical">Critical</option>
          <option value="at-risk">At Risk</option>
          <option value="healthy">Healthy</option>
          <option value="overstocked">Overstocked</option>
        </select>
      </div>

      <!-- Store tabs -->
      <div class="tabs" id="store-tabs">
        ${['all','A','B','C','WH'].map((s, i) => `
          <div class="tab-item ${s === 'all' ? 'active' : ''}" data-store="${s}">
            ${['All Stores','Store A','Store B','Store C','Warehouse'][i]}
          </div>`).join('')}
      </div>

      <!-- Table -->
      <div class="table-wrap table-overflow" id="inv-table-wrap">
        ${buildTable()}
      </div>
    </div>`;

  window.lucide?.createIcons?.();

  // Events
  let debounce;
  document.getElementById('inv-search').addEventListener('input', e => {
    clearTimeout(debounce);
    debounce = setTimeout(() => { _search = e.target.value.toLowerCase(); refresh(); }, 200);
  });
  document.getElementById('inv-cat').addEventListener('change', e => { _cat = e.target.value; refresh(); });
  document.getElementById('inv-status').addEventListener('change', e => { _status = e.target.value; refresh(); });

  document.getElementById('store-tabs').addEventListener('click', e => {
    const tab = e.target.closest('.tab-item');
    if (!tab) return;
    document.querySelectorAll('#store-tabs .tab-item').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    _store = tab.dataset.store;
    refresh();
  });

  setTimeout(_renderSparklines, 100);

  document.getElementById('inv-table-wrap').addEventListener('click', e => {
    const row = e.target.closest('tr[data-idx]');
    if (row) openSkuDrawer(parseInt(row.dataset.idx));
    const th = e.target.closest('th[data-col]');
    if (th) { sort(th.dataset.col); refresh(); }
  });

  // Keyboard shortcuts
  document.addEventListener('keydown', _keyHandler);
  container._cleanup = () => document.removeEventListener('keydown', _keyHandler);

  // Focus search on /
  document.getElementById('inv-search').addEventListener('keydown', e => {
    if (e.key === 'Escape') { e.target.value = ''; _search = ''; refresh(); }
  });
}

function _keyHandler(e) {
  const tag = document.activeElement?.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  if (e.key === '/') { e.preventDefault(); document.getElementById('inv-search')?.focus(); }
  if (e.key === 'Escape') closeDrawer();
  if (e.key === 'j') { _selectedIdx = Math.min(_selectedIdx + 1, filtered().length - 1); _highlightRow(); }
  if (e.key === 'k') { _selectedIdx = Math.max(_selectedIdx - 1, 0); _highlightRow(); }
  if (e.key === 'Enter' && _selectedIdx >= 0) openSkuDrawer(_selectedIdx);
}

function _highlightRow() {
  document.querySelectorAll('#inv-table-wrap tr[data-idx]').forEach(tr => {
    tr.classList.toggle('selected', parseInt(tr.dataset.idx) === _selectedIdx);
  });
}

function sort(col) {
  if (_sortCol === col) _sortDir *= -1;
  else { _sortCol = col; _sortDir = 1; }
}

function refresh() {
  document.getElementById('inv-table-wrap').innerHTML = buildTable();
  _updateSubtitle();
  setTimeout(_renderSparklines, 50);
}

function _updateSubtitle() {
  const el = document.getElementById('inv-subtitle');
  if (!el) return;
  const storeLabels = { A: 'Store A', B: 'Store B', C: 'Store C', WH: 'Warehouse' };
  if (_store === 'all') {
    el.textContent = `${_skus.length} SKUs across 3 stores · critical below ${_settings.dosFactor} days`;
  } else {
    el.textContent = `${_skus.length} SKUs · viewing ${storeLabels[_store]} · critical below ${_settings.dosFactor} days`;
  }
}

// Compute per-store metrics for a SKU based on the active store filter.
// Per-store demand isn't in the data (only global avg_daily_demand), so we
// split global demand evenly across 3 retail stores. Warehouse doesn't sell,
// so its demand share is 0 — DoS becomes effectively infinite (overstocked).
function _storeMetrics(inv) {
  const NUM_STORES = 3;
  const globalDemand = Number(inv.avg_daily_demand) || 0;
  const isFiltered = _store !== 'all';

  let stock, demand;
  if (_store === 'A')      { stock = inv.store_A   || 0; demand = globalDemand / NUM_STORES; }
  else if (_store === 'B') { stock = inv.store_B   || 0; demand = globalDemand / NUM_STORES; }
  else if (_store === 'C') { stock = inv.store_C   || 0; demand = globalDemand / NUM_STORES; }
  else if (_store === 'WH'){ stock = inv.warehouse || 0; demand = 0; }
  else                     { stock = inv.total_stock || 0; demand = globalDemand; }

  // DoS: stock / demand. If demand is 0 (warehouse) and stock > 0, treat as
  // effectively infinite coverage. If both are 0, DoS = 0 (stockout).
  let dos;
  if (demand > 0) dos = stock / demand;
  else if (stock > 0) dos = 999;
  else dos = 0;

  // Status: 0 stock = Stockout (always), otherwise use shared inventoryStatus()
  const isStockout = stock === 0 && isFiltered;
  const status = isStockout ? 'stockout' : dosStatus(dos);

  return { stock, demand, dos, status, isFiltered, isStockout, globalDemand };
}

function filtered() {
  return _skus.filter(s => {
    if (_search && !s.name.toLowerCase().includes(_search) && !s.id.toLowerCase().includes(_search)) return false;
    if (_cat !== 'all' && s.category !== _cat) return false;
    if (_status !== 'all') {
      const inv = _inv[s.id];
      if (!inv) return false;
      const m = _storeMetrics(inv);
      // Stockout rows surface under "Critical" filter (most severe)
      if (_status === 'critical' && m.status !== 'critical' && m.status !== 'stockout') return false;
      if (_status === 'at-risk' && m.status !== 'warning') return false;
      if (_status === 'healthy' && m.status !== 'healthy') return false;
      if (_status === 'overstocked' && m.status !== 'over') return false;
    }
    return true;
  }).map(s => {
    const inv = _inv[s.id] || {};
    const m = _storeMetrics(inv);
    return { ...s, inv, stock: m.stock, dos: m.dos, demand: m.demand, status: m.status, isFiltered: m.isFiltered, isStockout: m.isStockout, globalDemand: m.globalDemand };
  }).sort((a, b) => {
    if (_sortCol === 'name') return _sortDir * a.name.localeCompare(b.name);
    if (_sortCol === 'stock') return _sortDir * (a.stock - b.stock);
    if (_sortCol === 'dos') return _sortDir * (a.dos - b.dos);
    if (_sortCol === 'demand') return _sortDir * (a.demand - b.demand);
    return 0;
  });
}

function buildTable() {
  const rows = filtered();
  if (!rows.length) return `<div class="empty-state"><div class="empty-state-icon">🔍</div>
    <div class="empty-state-title">No SKUs match filters</div>
    <div class="empty-state-sub"><button class="btn btn-ghost btn-sm" onclick="document.getElementById('inv-search').value='';document.getElementById('inv-cat').value='all';document.getElementById('inv-status').value='all'">Clear filters</button></div>
  </div>`;

  const arrowFor = col => _sortCol === col ? (_sortDir > 0 ? ' ↑' : ' ↓') : '';
  const isFiltered = _store !== 'all';
  const demandHeader = isFiltered ? 'Demand 7d <small style="color:var(--text-tertiary);font-weight:400">(global pattern)</small>' : 'Demand 7d';

  const table = `<table class="data-table">
    <thead><tr>
      <th>SKU</th>
      <th class="sortable" data-col="name">Product${arrowFor('name')}</th>
      <th class="sortable" data-col="stock">Stock${arrowFor('stock')}</th>
      <th>${demandHeader}</th>
      <th class="sortable" data-col="demand">Avg/Day${arrowFor('demand')}</th>
      <th class="sortable" data-col="dos">DoS${arrowFor('dos')}</th>
      <th>Status</th>
    </tr></thead>
    <tbody>
      ${rows.map((r, i) => {
        const st = r.status;
        const stLabel = { stockout: 'Stockout', critical: 'Critical', warning: 'At Risk', healthy: 'Healthy', over: 'Overstocked' }[st];
        const stCls   = { stockout: 'badge-danger', critical: 'badge-danger', warning: 'badge-warning', healthy: 'badge-success', over: 'badge-info' }[st];
        // Stockout shares the critical DoS styling (it's the most severe state)
        const dosBadgeClass = st === 'stockout' ? 'critical' : st;
        const dosCls = `dos-badge dos-${dosBadgeClass}`;
        const dosDisplay = r.dos >= 999 ? '∞' : `${r.dos.toFixed(1)}d`;
        // Per-store reorder point isn't in the data — show "—" with global as tooltip
        const reorderCell = r.isFiltered
          ? `<span title="Global threshold: ${r.inv.reorder_point || 0}">—</span>`
          : (r.inv.reorder_point || 0);
        // Avg/Day: divided demand when filtered (or 0 for warehouse)
        const avgDayDisplay = r.demand > 0
          ? r.demand.toFixed(1)
          : (r.isFiltered ? '0' : (r.inv.avg_daily_demand || '—'));
        const avgDayCell = r.isFiltered && _store !== 'WH'
          ? `<span title="Global avg: ${r.inv.avg_daily_demand || 0}/day · divided across 3 stores">${avgDayDisplay}</span>`
          : (r.isFiltered && _store === 'WH'
              ? `<span title="Warehouse doesn't sell">0</span>`
              : avgDayDisplay);
        return `<tr data-idx="${i}" style="cursor:pointer">
          <td style="font-family:var(--font-mono);font-size:var(--text-xs);color:var(--text-tertiary)">${r.id}</td>
          <td style="font-weight:500;color:var(--text-primary);min-width:160px">${r.name}</td>
          <td>
            <div style="font-family:var(--font-mono);font-weight:600;color:var(--text-primary)">${r.stock}</div>
            <div style="font-size:var(--text-xs);color:var(--text-tertiary)">Reorder: ${reorderCell}</div>
          </td>
          <td style="min-width:80px">
            <div class="sparkline-wrap" style="width:72px;height:28px">
              <canvas class="spark-canvas" data-id="${r.id}"></canvas>
            </div>
          </td>
          <td style="font-family:var(--font-mono)">${avgDayCell}</td>
          <td><span class="${dosCls}">${dosDisplay}</span></td>
          <td><span class="badge ${stCls}">${stLabel}</span></td>
        </tr>`;
      }).join('')}
    </tbody>
  </table>`;

  const footnote = isFiltered
    ? `<div style="margin-top:var(--space-3);font-size:var(--text-xs);color:var(--text-tertiary);font-style:italic">
        Per-store DoS estimates assume demand divides evenly across stores. For per-store demand history, see future work.
      </div>`
    : '';
  return table + footnote;
}

function _renderSparklines() {
  // Renders sparklines in next animation frame — table already painted, INP stays low
  requestAnimationFrame(() => {
    document.querySelectorAll('.spark-canvas').forEach(canvas => {
      const fc = _fc[canvas.dataset.id];
      if (fc?.actual_recent_30) {
        sparkline(canvas, fc.actual_recent_30.slice(-14), C.green);
      }
    });
  });
}

async function openSkuDrawer(idx) {
  _selectedIdx = idx;
  const rows = filtered();
  const r = rows[idx];
  if (!r) return;
  const fc  = _fc[r.id] || {};
  const ex  = _ex[r.id] || {};
  const inv = r.inv;
  const pct = Math.min(100, ((inv.total_stock || 0) / (inv.max_stock || 1)) * 100);
  const st  = dosStatus(r.dos);
  const stCls = { critical: 'badge-danger', warning: 'badge-warning', healthy: 'badge-success', over: 'badge-info' }[st];

  openDrawer(`
    <div class="drawer-header">
      <div>
        <div style="font-size:var(--text-lg);font-weight:600;color:var(--text-primary)">${r.name}</div>
        <div style="font-size:var(--text-xs);font-family:var(--font-mono);color:var(--text-tertiary)">${r.id} · ${r.category}</div>
      </div>
      <div style="display:flex;align-items:center;gap:var(--space-3)">
        <span class="badge ${stCls}">${{ critical:'Critical', warning:'At Risk', healthy:'Healthy', over:'Overstocked' }[st]}</span>
        <button class="header-btn" onclick="(()=>{const m=document.getElementById('drawer');m.classList.remove('open');const o=document.getElementById('drawer-overlay');if(o)o.classList.remove('open')})()">×</button>
      </div>
    </div>

    <div class="drawer-body">
      <!-- Stock summary -->
      <div class="grid grid-cols-3 gap-3" style="margin-bottom:var(--space-5)">
        ${statBlock('Total Stock', inv.total_stock || 0)}
        ${statBlock('Days of Stock', r.dos.toFixed(1) + 'd')}
        ${statBlock('Avg/Day', inv.avg_daily_demand || '—')}
      </div>

      <!-- Stock range bar -->
      <div style="margin-bottom:var(--space-5)">
        <div style="display:flex;justify-content:space-between;font-size:var(--text-xs);color:var(--text-tertiary);margin-bottom:6px">
        <span>Reorder point: ${inv.reorder_point}</span>
          <span>Max: ${inv.max_stock}</span>
        </div>
        <div class="stock-range-bar">
          <div class="stock-range-fill" style="width:${pct}%;background:${st==='critical'?'var(--danger-fg)':st==='warning'?'var(--warning-fg)':'var(--success-fg)'}"></div>
          <div class="stock-marker" style="left:${Math.min(100,((inv.reorder_point||0)/(inv.max_stock||1))*100)}%"></div>
        </div>
      </div>

      <!-- Store breakdown -->
      <div class="divider"></div>
      <div style="font-size:var(--text-sm);font-weight:600;color:var(--text-primary);margin:var(--space-4) 0 var(--space-3)">Store Breakdown</div>
      <div class="grid grid-cols-2 gap-3" style="margin-bottom:var(--space-5)">
        ${statBlock('Warehouse', inv.warehouse || 0)}
        ${statBlock('Store A', inv.store_A || 0)}
        ${statBlock('Store B', inv.store_B || 0)}
        ${statBlock('Store C', inv.store_C || 0)}
      </div>

      <!-- 7-day forecast -->
      <div class="divider"></div>
      <div style="font-size:var(--text-sm);font-weight:600;color:var(--text-primary);margin:var(--space-4) 0 var(--space-3)">7-Day Demand Forecast · ${selectedForecastLabel(_settings)}</div>
      <div style="height:140px;position:relative">
        <canvas id="drawer-chart"></canvas>
      </div>

      <!-- AI Insights -->
      ${ex.top_factors ? `
        <div class="divider" style="margin-top:var(--space-4)"></div>
        <div style="font-size:var(--text-sm);font-weight:600;color:var(--text-primary);margin:var(--space-4) 0 var(--space-3)">AI Key Factors</div>
        ${ex.top_factors.slice(0,3).map(f => `
          <div class="factor-bar-row">
            <div class="factor-label">${f.label}</div>
            <div class="factor-bar-wrap"><div class="factor-bar-fill" style="width:${(f.weight*100).toFixed(0)}%"></div></div>
            <div class="factor-pct">${(f.weight*100).toFixed(0)}%</div>
            <div class="factor-dir">${f.direction === 'up' ? '↑' : '↓'}</div>
          </div>`).join('')}
      ` : ''}
    </div>

    <div class="drawer-footer">
      <a href="#/orders" class="btn btn-primary btn-sm" onclick="document.getElementById('drawer').classList.remove('open')">Order Now</a>
      <button class="btn btn-secondary btn-sm" onclick="document.getElementById('drawer').classList.remove('open')">Close</button>
    </div>
  `);

  window.lucide?.createIcons?.();

  // Draw forecast chart
  if (fc.chronos) {
    const labels = ['Today','D+1','D+2','D+3','D+4','D+5','D+6'];
    setTimeout(() => {
      const canvas = document.getElementById('drawer-chart');
      if (canvas) lineChart(canvas, labels, [
        { label: 'LightGBM', data: fc.lightgbm || [], borderColor: C.amber, borderWidth: 1.2, pointRadius: 2, fill: false, tension: 0.3, borderDash: [4, 3] },
        { label: 'N-HITS',   data: fc.nhits    || [], borderColor: C.blue,  borderWidth: 1.2, pointRadius: 2, fill: false, tension: 0.3, borderDash: [4, 3] },
        { label: selectedForecastLabel(_settings),  data: selectedForecastSeries(fc, _settings), borderColor: selectedForecastColor(_settings, C), borderWidth: 2.4, pointRadius: 2, fill: false, tension: 0.3 },
      ]);
    }, 50);
  }
}

function statBlock(label, value) {
  return `<div style="background:var(--bg-3);border:1px solid var(--border-subtle);border-radius:var(--radius-md);padding:var(--space-3)">
    <div style="font-size:var(--text-xs);color:var(--text-tertiary);text-transform:uppercase;letter-spacing:.06em">${label}</div>
    <div style="font-size:var(--text-lg);font-weight:700;font-family:var(--font-mono);color:var(--text-primary);margin-top:2px">${value}</div>
  </div>`;
}

function dosStatus(dos) {
  return inventoryStatus(dos, _settings);
}
