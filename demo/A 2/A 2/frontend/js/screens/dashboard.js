import { API } from '../api.js';
import { AppState } from '../state.js';
import { applyDefaults, sparkline, lineChart, C } from '../components/chart-helpers.js?v=accuracy4';
import { deriveRecommendation, projectedInventorySeries, selectedForecastColor, selectedForecastLabel, selectedForecastSeries } from '../domain/policy.js?v=accuracy4';

export async function render(container) {
  applyDefaults();

  const [skus, inv, recs, alerts, forecasts, historySeed, tol] = await Promise.all([
    API.skus(), API.inventory(), API.recommendations(),
    API.alerts(), API.forecasts(), API.historySeed(),
    API.tolerance().catch(() => null),
  ]);
  const settings = AppState.getSettings();

  // KPI calculations
  const totalValue = skus.reduce((sum, s) => {
    const i = inv[s.id];
    return sum + (i ? i.total_stock * s.unit_cost : 0);
  }, 0);
  const atRisk    = Object.values(inv).filter(i => i.days_of_stock <= settings.dosFactor + 3).length;
  const pending   = AppState.pendingCount(recs);
  const avgConf   = recs.length ? (recs.reduce((s, r) => s + r.confidence, 0) / recs.length * 100).toFixed(0) : 0;
  const activeAlerts = alerts.filter(a => a.active);

  const decisions = AppState.getDecisions();
  const today = new Date().toLocaleDateString('en-IN', { weekday:'long', day:'numeric', month:'long', year:'numeric' });

  container.innerHTML = `
    <div class="animate-in">
      <div class="screen-header">
        <div>
          <h1>Good morning, Ramesh</h1>
          <div class="subtitle">${today}</div>
        </div>
        <div class="screen-header-actions">
          <button class="btn btn-primary" id="run-recs-btn">
            <i data-lucide="play" style="width:14px;height:14px"></i>
            Run Daily Recommendations
          </button>
        </div>
      </div>

      <!-- KPI cards -->
      <div class="grid grid-cols-4 gap-4" style="margin-bottom:var(--space-6)" id="kpi-row">
        ${kpiCard('Inventory Value', '₹' + fmt(totalValue), 'badge-accent', inventoryDeltaLabel(totalValue), 'kpi-val')}
        ${kpiCard('SKUs Needing Attention', atRisk, 'badge-warning', `days of stock ≤ ${settings.dosFactor + 3}`, 'kpi-at-risk')}
        ${kpiCard("Today's Recommendations", recs.length, 'badge-accent', `${pending} decisions pending`, 'kpi-recs')}
        ${kpiCard('Avg AI Confidence', avgConf + '%', 'badge-success', 'Across all models', 'kpi-conf')}
      </div>

      <!-- Operational accuracy ribbon (real test-set tolerance, read from tolerance_accuracy.json) -->
      ${tol ? operationalRibbon(tol) : ''}

      <div class="grid gap-6" style="grid-template-columns:1fr 320px">
        <div>
          <!-- Alerts -->
          <div class="section-header">
            <span class="section-title">Active Alerts</span>
            <span class="badge badge-neutral">${activeAlerts.length}</span>
          </div>
          <div style="display:flex;flex-direction:column;gap:var(--space-2);margin-bottom:var(--space-6)" id="alerts-list">
            ${activeAlerts.length === 0
              ? `<div class="alert-card success"><span class="severity-dot dot-success"></span><div><div class="alert-message">All clear — no active alerts</div></div></div>`
              : activeAlerts.map(a => alertCard(a)).join('')}
          </div>

          <!-- Forecast chart -->
          <div class="chart-container">
            <div class="card-header">
              <div class="card-title">${skus.find(s => s.id === 'FOODS_3_090')?.name || 'FOODS_3_090'} — demand + projected stock after setup</div>
              <span class="badge badge-accent">${selectedForecastLabel(settings)}</span>
            </div>
            <div class="chart-wrap" style="height:180px">
              <canvas id="dash-chart"></canvas>
            </div>
          </div>

          <!-- Recs preview -->
          <div class="section-header" style="margin-top:var(--space-6)">
            <span class="section-title">Today's Recommendations</span>
            <a class="section-link" href="#/orders">View all ${recs.length} →</a>
          </div>
          <div class="table-wrap table-overflow">
            <table class="data-table">
              <thead><tr>
                <th>SKU</th><th>Stock</th><th>Order Qty</th>
                <th>Confidence</th><th></th>
              </tr></thead>
              <tbody>
                ${recs.slice(0, 5).map(sourceRec => {
                  const sku = skus.find(s => s.id === sourceRec.sku_id);
                  const r = deriveRecommendation(sourceRec, sku, inv[sourceRec.sku_id], forecasts[sourceRec.sku_id], settings);
                  const decided = AppState.isDecided(r.sku_id);
                  return `<tr onclick="location.hash='/orders'" style="cursor:pointer">
                    <td>
                      <div style="font-size:var(--text-sm);font-weight:500;color:var(--text-primary)">${sku?.name || r.sku_id}</div>
                      <div style="font-size:var(--text-xs);color:var(--text-tertiary);font-family:var(--font-mono)">${r.sku_id}</div>
                    </td>
                    <td style="font-family:var(--font-mono)">${r.current_stock}</td>
                    <td style="font-family:var(--font-mono);font-weight:600;color:var(--text-primary)">${r.recommended_order_qty}</td>
                    <td style="min-width:100px">
                      <div class="confidence-bar">
                        <div class="bar"><div class="progress-bar"><div class="progress-fill" style="width:${(r.confidence*100).toFixed(0)}%"></div></div></div>
                        <div class="val">${(r.confidence*100).toFixed(0)}%</div>
                      </div>
                    </td>
                    <td>${decided ? '<span class="badge badge-success">Done</span>' : '<a class="btn btn-ghost btn-sm" href="#/orders">Review</a>'}</td>
                  </tr>`;
                }).join('')}
              </tbody>
            </table>
          </div>
        </div>

        <!-- Right: activity feed -->
        <div>
          <div class="section-header">
            <span class="section-title">Recent Decisions</span>
            <a class="section-link" href="#/history">All history →</a>
          </div>
          ${activityFeed(decisions, historySeed)}
        </div>
      </div>
    </div>`;

  window.lucide?.createIcons?.();

  // Demand chart
  const fc = forecasts['FOODS_3_090'];
  if (fc) {
    const actual = fc.actual_recent_30 || [];
    const fcast  = selectedForecastSeries(fc, settings);
    const sourceRec = recs.find(r => r.sku_id === 'FOODS_3_090') || {};
    const sku = skus.find(s => s.id === 'FOODS_3_090') || {};
    const rec = deriveRecommendation(sourceRec, sku, inv['FOODS_3_090'], fc, settings);
    const stock = projectedInventorySeries(inv['FOODS_3_090'], rec.recommended_order_qty, fc, settings);
    const actualPad  = [...actual, ...Array(fcast.length).fill(null)];
    const forecastPad = [...Array(actual.length).fill(null), ...fcast];
    const stockPad = [...Array(actual.length).fill(null), ...stock];
    const canvas = document.getElementById('dash-chart');
    if (canvas) lineChart(canvas,
      [...actual.map((_, i) => `D-${actual.length - i}`), ...fcast.map((_, i) => `+${i + 1}`)],
      [
        { label: 'Actual', data: actualPad, borderColor: C.blue, borderWidth: 2, pointRadius: 0, fill: false, tension: 0.2, spanGaps: false },
        { label: selectedForecastLabel(settings), data: forecastPad, borderColor: selectedForecastColor(settings, C), borderWidth: 2.2, pointRadius: 0, fill: false, tension: 0.3, borderDash: [5, 4], spanGaps: false },
        { label: `Projected stock after ${rec.recommended_order_qty} unit order`, data: stockPad, borderColor: C.green, backgroundColor: C.greenA, borderWidth: 2.4, pointRadius: 0, fill: { target: 'origin', above: 'hsla(160,75%,52%,0.08)' }, tension: 0.25, spanGaps: false },
      ]
    );
  }

  // KPI count-up
  countUp('kpi-val', totalValue, true);
  countUp('kpi-at-risk', atRisk, false);
  countUp('kpi-recs', recs.length, false);
  countUp('kpi-conf', parseInt(avgConf), false, '%');

  // Run recs button
  document.getElementById('run-recs-btn')?.addEventListener('click', () => {
    const btn = document.getElementById('run-recs-btn');
    btn.disabled = true;
    btn.textContent = 'Running...';
    setTimeout(() => {
      btn.textContent = '✓ Recommendations ready';
      btn.classList.add('btn-secondary');
      btn.classList.remove('btn-primary');
      setTimeout(() => location.hash = '/orders', 600);
    }, 1800);
  });

  // Alert review buttons
  container.querySelectorAll('[data-goto]').forEach(el => {
    el.addEventListener('click', () => { location.hash = el.dataset.goto; });
  });
}

function kpiCard(label, value, badge, sub, id) {
  return `
    <div class="kpi-card">
      <div class="kpi-label">${label}</div>
      <div class="kpi-value" id="${id}">${value}</div>
      <div class="kpi-delta"><span class="badge ${badge}">${sub}</span></div>
    </div>`;
}

function alertCard(a) {
  const cls = { critical: 'critical', warning: 'warning', info: 'info', success: 'success' }[a.severity] || 'info';
  const dot = { critical: 'dot-critical', warning: 'dot-warning', info: 'dot-info', success: 'dot-success' }[a.severity] || 'dot-info';
  const ts = timeAgo(a.created_at);
  return `
    <div class="alert-card ${cls}">
      <span class="severity-dot ${dot}" style="margin-top:3px;flex-shrink:0"></span>
      <div style="flex:1;min-width:0">
        <div class="alert-type" style="color:${sevColor(a.severity)}">${a.severity.toUpperCase()} · ${a.type.replace(/_/g,' ')}</div>
        <div class="alert-message">${a.message}</div>
        <div class="alert-meta">${ts}${a.affected_skus?.length ? ' · ' + a.affected_skus.join(', ') : ''}</div>
      </div>
      ${a.action ? `<button class="btn btn-ghost btn-sm" data-goto="${alertRoute(a.action)}" style="flex-shrink:0">Review</button>` : ''}
    </div>`;
}

function activityFeed(decisions, historySeed = []) {
  const seed = historySeed.slice(0, 8);
  const rows = (decisions.length > 0 ? decisions.slice(0, 8) : seed).map(d => ({
    name: d.sku_name || d.sku_id,
    decision: d.your_decision,
    outcome: d.outcome || 'healthy',
    date: d.date || d.timestamp?.split('T')[0] || '—',
  }));

  if (!rows.length) return `<div class="empty-state" style="padding:var(--space-8) 0">
    <div class="empty-state-title">No decisions yet</div>
    <a href="#/orders" class="btn btn-primary btn-sm" style="margin-top:var(--space-3)">Go to Orders</a>
  </div>`;

  return `<div style="display:flex;flex-direction:column">
    ${rows.map(d => `
      <div class="activity-item">
        <div class="activity-dot" style="background:${outcomeColor(d.outcome)}"></div>
        <div style="flex:1;min-width:0">
          <div style="font-size:var(--text-sm);font-weight:500;color:var(--text-primary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${d.name}</div>
          <div style="font-size:var(--text-xs);color:var(--text-tertiary);display:flex;align-items:center;gap:6px">
            <span>${d.decision}</span>·<span class="badge ${outcomeCls(d.outcome)}" style="padding:1px 6px">${d.outcome}</span>
          </div>
        </div>
        <div style="font-size:var(--text-xs);color:var(--text-tertiary);flex-shrink:0">${d.date}</div>
      </div>`).join('')}
  </div>`;
}

function countUp(id, target, isCurrency, suffix = '') {
  const el = document.getElementById(id);
  if (!el || typeof target !== 'number') return;
  el.textContent = isCurrency ? '₹' + fmt(Math.round(target)) : Math.round(target) + suffix;
}

function fmt(n) {
  if (n >= 100000) return (n / 100000).toFixed(1) + 'L';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return n.toLocaleString('en-IN');
}

function sevColor(s) {
  return { critical: 'var(--danger-fg)', warning: 'var(--warning-fg)', info: 'var(--info-fg)', success: 'var(--success-fg)' }[s] || 'var(--text-secondary)';
}

function alertRoute(action) {
  return {
    review_order: '/orders',
    review_forecast: '/forecasts',
    review_inventory: '/inventory',
  }[action] || '/dashboard';
}

function outcomeColor(o) {
  return { healthy: 'var(--success-fg)', stockout: 'var(--danger-fg)', overstock: 'var(--warning-fg)' }[o] || 'var(--accent-400)';
}

function outcomeCls(o) {
  return { healthy: 'badge-success', stockout: 'badge-danger', overstock: 'badge-warning' }[o] || 'badge-neutral';
}

function timeAgo(iso) {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const h = Math.floor(diff / 3600000);
  if (h < 1) return 'Just now';
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function inventoryDeltaLabel(currentValue) {
  // Read yesterday's snapshot from localStorage; if absent, store today as baseline
  const SNAP_KEY = 'smartstock_inv_snapshot';
  let snap = null;
  try {
    const raw = localStorage.getItem(SNAP_KEY);
    if (raw) snap = JSON.parse(raw);
  } catch {}
  const today = new Date().toISOString().split('T')[0];
  // If no snapshot or snapshot is from today, write today as the baseline and show neutral label
  if (!snap || snap.date === today) {
    try { localStorage.setItem(SNAP_KEY, JSON.stringify({ date: today, value: currentValue })); } catch {}
    return 'Current snapshot';
  }
  // Compute delta
  const delta = currentValue - snap.value;
  const pct = snap.value > 0 ? (delta / snap.value) * 100 : 0;
  const sign = pct >= 0 ? '+' : '';
  // Refresh baseline if older than 1 day so subsequent loads compute fresh
  try { localStorage.setItem(SNAP_KEY, JSON.stringify({ date: today, value: currentValue })); } catch {}
  return `${sign}${pct.toFixed(1)}% vs ${snap.date}`;
}

function operationalRibbon(tol) {
  // Read real tolerance values from tolerance_accuracy.json
  const c = tol.models?.chronos?.absolute_tolerance?.within_pm25_units?.pct;
  const n = tol.models?.naive?.absolute_tolerance?.within_pm25_units?.pct;
  if (!Number.isFinite(c) || !Number.isFinite(n)) return '';
  const lift = (c - n).toFixed(1);
  const testDays = tol.metadata?.test_days || 389;
  return `
    <div class="card card-accent" style="margin-bottom:var(--space-6);padding:var(--space-4) var(--space-5);display:flex;align-items:center;gap:var(--space-4);background:linear-gradient(135deg, hsla(160,80%,15%,0.4) 0%, hsla(160,80%,10%,0.2) 100%)">
      <div style="font-size:32px;font-weight:700;font-family:var(--font-mono);color:var(--accent-400);letter-spacing:-0.03em;line-height:1">${c.toFixed(1)}%</div>
      <div style="flex:1;min-width:0">
        <div style="font-size:var(--text-sm);font-weight:600;color:var(--text-primary)">Operational accuracy on real test set</div>
        <div style="font-size:var(--text-xs);color:var(--text-secondary);margin-top:2px">Chronos-Bolt predicts demand within ±25 units on ${c.toFixed(1)}% of ${testDays} held-out days — ${lift}pp better than naive baseline</div>
      </div>
      <a class="section-link" href="#/forecasts" style="font-size:var(--text-xs);flex-shrink:0">View details →</a>
    </div>`;
}

