import { API } from '../api.js';
import { AppState } from '../state.js';

let _all = [], _range = 14, _limit = 25;
const DEFAULT_LIMIT = 25;

export async function render(container) {
  const seed = await API.historySeed();
  _build(container, seed);
}

function _build(container, seed) {
  const live = AppState.getDecisions().map(d => ({
    order_id: `ORD-${d.date?.replace(/-/g, '') || 'NOW'}-${(d.sku_id || '').slice(-3)}`,
    date: d.date || new Date().toISOString().split('T')[0],
    sku_id: d.sku_id,
    sku_name: d.sku_name || d.sku_id,
    ai_recommendation: d.ai_recommendation || d.qty,
    your_decision: d.action || d.your_decision,
    final_qty: d.qty || d.final_qty,
    reason: d.reason || null,
    outcome: d.outcome || 'healthy',
    cost: d.cost || 0,
    performance_score: d.performance_score || 0.8,
  }));

  _all = [...live, ...seed].sort((a, b) => b.date.localeCompare(a.date));
  _renderView(container);
}

function _renderView(container) {
  const cutoff  = new Date(Date.now() - _range * 86400000).toISOString().split('T')[0];
  const rows    = _all.filter(r => r.date >= cutoff);
  const approved = rows.filter(r => r.your_decision === 'approved').length;
  const modified = rows.filter(r => r.your_decision === 'modified').length;
  const skipped  = rows.filter(r => r.your_decision === 'skipped').length;
  const total    = rows.length;
  const acceptRate = total ? Math.round((approved / total) * 100) : 0;
  const totalCost  = rows.reduce((s, r) => s + (r.cost || 0), 0);
  // Directional agreement: approved OR modified within ±20% of AI rec.
  // Honest counter to acceptance rate — managers usually follow the AI's direction.
  const directional = rows.filter(r => {
    if (r.your_decision === 'approved') return true;
    if (r.your_decision === 'modified') {
      const ai = Number(r.ai_recommendation) || 0;
      const fq = Number(r.final_qty);
      if (!ai || !Number.isFinite(fq)) return false;
      return Math.abs(fq - ai) / ai <= 0.20;
    }
    return false;
  }).length;
  const dirRate = total ? Math.round((directional / total) * 100) : 0;
  // BUG 2: contextual sub-label for Skipped Orders
  const skipSub = skipped === 0 ? 'All AI recs reviewed'
                 : skipped < 5  ? 'Light review activity'
                                : 'Consider AI trust settings';
  const skipBadge = skipped === 0 ? 'badge-success' : skipped < 5 ? 'badge-neutral' : 'badge-warning';
  // BUG 4: limit visible rows so the table doesn't run off the page with no count indicator
  const visibleRows = rows.slice(0, _limit);

  container.innerHTML = `
    <div class="animate-in">
      <div class="screen-header">
        <div>
          <h1>Order History</h1>
          <div class="subtitle">Past decisions and their outcomes</div>
        </div>
        <div class="screen-header-actions">
          <div class="filter-pills">
            ${[7,14,30].map(d =>
              `<span class="pill ${d === _range ? 'active' : ''}" data-range="${d}">Last ${d}d</span>`
            ).join('')}
          </div>
          <button class="btn btn-secondary btn-sm" id="csv-export">
            <i data-lucide="download" style="width:13px;height:13px"></i>
            Export CSV
          </button>
        </div>
      </div>

      <!-- Summary KPIs -->
      <div class="grid grid-cols-4 gap-4" style="margin-bottom:var(--space-6)">
        ${kpi('Total Orders', total, 'badge-neutral', `Last ${_range} days`)}
        ${kpi('Acceptance Rate', acceptRate + '%', acceptRate >= 70 ? 'badge-success' : 'badge-warning', `${approved} approved, ${modified} modified`,
              `Directional agreement: <strong style="color:var(--text-secondary)">${dirRate}%</strong><br>(approved OR modified within ±20% of AI rec)`)}
        ${kpi('Skipped Orders', skipped, skipBadge, skipSub)}
        ${kpi('Total Order Value', '₹' + fmtNum(totalCost), '', '')}
      </div>

      <!-- Decision breakdown bar -->
      <div class="card" style="margin-bottom:var(--space-5)">
        <div class="card-title" style="margin-bottom:var(--space-3)">Decision Breakdown</div>
        <div style="display:flex;gap:0;border-radius:var(--radius-full);overflow:hidden;height:8px;margin-bottom:var(--space-3)">
          <div style="flex:${approved};background:var(--success-fg)" title="Approved: ${approved}"></div>
          <div style="flex:${modified};background:var(--info-fg)" title="Modified: ${modified}"></div>
          <div style="flex:${Math.max(skipped,0.01)};background:var(--warning-fg)" title="Skipped: ${skipped}"></div>
        </div>
        <div style="display:flex;gap:var(--space-5);font-size:var(--text-xs);color:var(--text-secondary)">
          <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;border-radius:2px;background:var(--success-fg);display:inline-block"></span>Approved (${approved})</span>
          <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;border-radius:2px;background:var(--info-fg);display:inline-block"></span>Modified (${modified})</span>
          <span style="display:flex;align-items:center;gap:4px"><span style="width:8px;height:8px;border-radius:2px;background:var(--warning-fg);display:inline-block"></span>Skipped (${skipped})</span>
        </div>
      </div>

      <!-- Table -->
      <div class="table-wrap table-overflow" style="margin-bottom:var(--space-2)">
        <table class="data-table" id="hist-table">
          <thead><tr>
            <th>Date</th>
            <th>Product</th>
            <th>AI Rec</th>
            <th>Your Decision</th>
            <th>Outcome</th>
            <th title="Decision quality: how well the final order matched the realized 7-day demand. Higher = closer to optimal. 0% = severe over/understock.">Score</th>
          </tr></thead>
          <tbody>
            ${rows.length === 0
              ? `<tr><td colspan="6" class="empty-state" style="text-align:center;padding:var(--space-8)">No orders in this period</td></tr>`
              : visibleRows.map(r => histRow(r)).join('')}
          </tbody>
        </table>
      </div>

      <!-- Row count footer + Show all -->
      <div style="display:flex;justify-content:space-between;align-items:center;padding:var(--space-2) 0;font-size:var(--text-xs);color:var(--text-tertiary);margin-bottom:var(--space-3)">
        <span>
          ${rows.length === 0
            ? 'No orders to display'
            : visibleRows.length === rows.length
              ? `Showing all <strong style="color:var(--text-secondary)">${rows.length}</strong> orders`
              : `Showing <strong style="color:var(--text-secondary)">${visibleRows.length}</strong> of <strong style="color:var(--text-secondary)">${rows.length}</strong> orders — older entries available, export CSV for full history`}
        </span>
        ${visibleRows.length < rows.length ? `<button class="btn btn-secondary btn-sm" id="show-all-rows">Show all ${rows.length}</button>` : ''}
      </div>

      <!-- Score legend -->
      <div style="font-size:var(--text-xs);color:var(--text-tertiary);margin-bottom:var(--space-6);line-height:1.5">
        <strong>Score</strong> = quality of the final order vs. actual demand realized in the following 7 days. Higher is better (≥80% green, 60-79% amber, &lt;60% red). A 0% score flags a severe over- or understock and is marked with a <span class="badge badge-danger" style="font-size:10px">⚠ severe miss</span> badge.
      </div>

      <!-- Insights -->
      <div class="section-header">
        <span class="section-title">Insights from your decisions</span>
      </div>
      <div class="grid grid-cols-2 gap-4">
        ${insightCard(rows, approved, modified, skipped)}
      </div>
    </div>`;

  window.lucide?.createIcons?.();

  // Range filter
  container.querySelectorAll('[data-range]').forEach(el => {
    el.addEventListener('click', () => {
      _range = parseInt(el.dataset.range);
      _limit = DEFAULT_LIMIT;
      _renderView(container);
    });
  });

  // Show all rows
  document.getElementById('show-all-rows')?.addEventListener('click', () => {
    _limit = Infinity;
    _renderView(container);
  });

  // CSV export
  document.getElementById('csv-export')?.addEventListener('click', () => exportCSV(rows));

  // Row expand
  document.getElementById('hist-table')?.addEventListener('click', e => {
    const row = e.target.closest('tr[data-id]');
    if (!row) return;
    const detail = row.nextElementSibling;
    if (detail?.classList.contains('expand-row')) {
      detail.remove();
    } else {
      const r = rows.find(x => x.order_id === row.dataset.id);
      if (!r) return;
      const expandRow = document.createElement('tr');
      expandRow.className = 'expand-row';
      expandRow.innerHTML = `<td colspan="6" style="background:var(--bg-1);padding:var(--space-4) var(--space-5)">
        <div style="font-size:var(--text-sm);color:var(--text-secondary)">
          <strong>Order ID:</strong> <span style="font-family:var(--font-mono)">${r.order_id}</span> ·
          <strong>Cost:</strong> ₹${(r.cost || 0).toLocaleString('en-IN')} ·
          <strong>Reason:</strong> ${r.reason || 'Not specified'}
        </div>
        <div style="font-size:var(--text-xs);color:var(--text-tertiary);margin-top:4px">
          AI recommended ${r.ai_recommendation} · You ordered ${r.final_qty || r.ai_recommendation} · Outcome: ${r.outcome}
        </div>
      </td>`;
      row.insertAdjacentElement('afterend', expandRow);
    }
  });
}

function histRow(r) {
  const decCls  = { approved: 'badge-success', modified: 'badge-info', skipped: 'badge-warning' }[r.your_decision] || 'badge-neutral';
  const outCls  = { healthy: 'badge-success', stockout: 'badge-danger', overstock: 'badge-warning' }[r.outcome] || 'badge-neutral';
  const score   = r.performance_score || 0;
  const scoreCls = score >= 0.8 ? 'var(--success-fg)' : score >= 0.6 ? 'var(--warning-fg)' : 'var(--danger-fg)';
  // For 'modified' decisions, always show the final quantity (the user's order).
  // For 'approved' decisions, show the modified qty only when it differs from AI rec.
  const qtyText = r.your_decision === 'modified'
    ? (r.final_qty != null ? `${r.final_qty} units` : '')
    : (r.final_qty != null && r.final_qty !== r.ai_recommendation ? `${r.final_qty} units` : '');
  const isSevereMiss = score === 0 || score < 0.05;
  return `<tr data-id="${r.order_id}" style="cursor:pointer">
    <td style="font-family:var(--font-mono);font-size:var(--text-xs)">${r.date}</td>
    <td>
      <div style="font-size:var(--text-sm);font-weight:500;color:var(--text-primary)">${r.sku_name}</div>
      <div style="font-size:var(--text-xs);color:var(--text-tertiary);font-family:var(--font-mono)">${r.sku_id}</div>
    </td>
    <td style="font-family:var(--font-mono)">${r.ai_recommendation}</td>
    <td>
      <span class="badge ${decCls}">${r.your_decision}</span>
      ${qtyText ? `<span style="font-size:var(--text-xs);color:var(--text-tertiary);margin-left:4px">${qtyText}</span>` : ''}
    </td>
    <td><span class="badge ${outCls}">${r.outcome || '—'}</span></td>
    <td>
      <div style="display:flex;align-items:center;gap:6px">
        <span style="font-size:var(--text-sm);font-family:var(--font-mono);color:${scoreCls}">${(score * 100).toFixed(0)}%</span>
        ${isSevereMiss ? `<span class="badge badge-danger" style="font-size:10px" title="Final order was far from optimal vs. realized 7-day demand">⚠ severe miss</span>` : ''}
      </div>
    </td>
  </tr>`;
}

function insightCard(rows, approved, modified, skipped) {
  const cards = [];
  const total = rows.length;

  if (modified > 0) {
    const modRows = rows.filter(r => r.your_decision === 'modified');
    const avgDelta = modRows.reduce((s, r) => s + ((r.final_qty || 0) - (r.ai_recommendation || 0)), 0) / modRows.length;
    cards.push(`<div class="insight-card">
      <div class="insight-text">You modified AI recommendations ${modified} times in this period. Average change: <strong>${avgDelta > 0 ? '+' : ''}${avgDelta.toFixed(0)} units</strong> vs AI. ${avgDelta < 0 ? 'Consider trusting the AI more for high-demand SKUs.' : 'Your upward adjustments may reflect local knowledge the model lacks.'}</div>
    </div>`);
  }

  if (skipped > 0) {
    const skipRows = rows.filter(r => r.your_decision === 'skipped' && r.outcome === 'stockout');
    cards.push(`<div class="insight-card">
      <div class="insight-text">${skipped > 0 ? `${skipped} orders were skipped.` : ''} ${skipRows.length > 0 ? `<strong style="color:var(--danger-fg)">${skipRows.length} stockout${skipRows.length > 1 ? 's' : ''}</strong> occurred after a skip decision. Skipping AI orders for critical SKUs increases stockout risk.` : 'No stockouts from skipped orders in this period.'}</div>
    </div>`);
  }

  if (approved > 0) {
    const healthyRate = Math.round((rows.filter(r => r.outcome === 'healthy').length / total) * 100);
    cards.push(`<div class="insight-card">
      <div class="insight-text">Healthy outcome rate: <strong style="color:var(--success-fg)">${healthyRate}%</strong>. AI acceptance rate: <strong>${Math.round(approved/total*100)}%</strong>. ${healthyRate >= 75 ? 'Your ordering strategy is performing well.' : 'Consider reviewing the cases where outcomes were suboptimal.'}</div>
    </div>`);
  }

  if (cards.length < 2) cards.push(`<div class="insight-card">
    <div class="insight-text">Make more decisions to unlock detailed performance insights. The system learns from your ordering patterns over time.</div>
  </div>`);

  return cards.slice(0, 4).join('');
}

function kpi(label, value, badge, sub, note) {
  return `<div class="kpi-card">
    <div class="kpi-label">${label}</div>
    <div class="kpi-value">${value}</div>
    ${sub ? `<div class="kpi-delta"><span class="badge ${badge || 'badge-neutral'}">${sub}</span></div>` : ''}
    ${note ? `<div style="font-size:var(--text-xs);color:var(--text-tertiary);margin-top:6px;line-height:1.4">${note}</div>` : ''}
  </div>`;
}

function fmtNum(n) {
  if (n >= 100000) return (n / 100000).toFixed(1) + 'L';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return n.toLocaleString('en-IN');
}

function exportCSV(rows) {
  const hdr = 'Date,SKU ID,Product,AI Recommendation,Your Decision,Final Qty,Outcome,Score,Cost\n';
  const body = rows.map(r =>
    [r.date, r.sku_id, `"${r.sku_name}"`, r.ai_recommendation, r.your_decision,
     r.final_qty || r.ai_recommendation, r.outcome, r.performance_score?.toFixed(2), r.cost || 0].join(',')
  ).join('\n');
  const blob = new Blob([hdr + body], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `smartstock_history_${new Date().toISOString().split('T')[0]}.csv`;
  a.click();
}
