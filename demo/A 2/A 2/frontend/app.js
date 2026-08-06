/* app.js — main application logic for the inventory optimization frontend */
'use strict';

// ── globals ──────────────────────────────────────────────────────────────────
let DATA = {};
let CHARTS = {};

const POLICY_COLORS = {
  'Default (s,S)':                'hsl(220, 60%, 60%)',
  'Grid-Tuned Classical':         'hsl(40,  85%, 58%)',
  'CMA-ES Per-Node (Robust)':     'hsl(160, 80%, 50%)',
};
const CHART_DEFAULTS = {
  color: 'hsl(220,10%,95%)',
  borderColor: 'hsl(220,14%,22%)',
  backgroundColor: 'transparent',
};

// ── data loading ──────────────────────────────────────────────────────────────
async function loadData() {
  const files = ['overview', 'comparison', 'retailers', 'disruptions',
                 'explainability', 'hack_detection'];
  const base  = 'data/';
  await Promise.all(files.map(async name => {
    try {
      const r = await fetch(base + name + '.json');
      DATA[name] = await r.json();
    } catch(e) {
      console.warn('Could not load', name, e);
      DATA[name] = null;
    }
  }));
}

// ── hero stats ────────────────────────────────────────────────────────────────
function renderOverview() {
  const d = DATA.overview;
  if (!d) return;
  const container = document.getElementById('hero-stats');
  if (!container) return;
  container.innerHTML = d.headline_stats.map(s => `
    <div class="stat-item">
      <div class="stat-value">${s.value}${s.unit ? '<span style="font-size:1rem;color:var(--text-dim)"> '+s.unit+'</span>' : ''}</div>
      <div class="stat-label">${s.label}</div>
    </div>
  `).join('');
}

// ── demo / retailers ──────────────────────────────────────────────────────────
function renderRetailers() {
  const retailers = DATA.retailers;
  if (!retailers) return;

  const tabs   = document.getElementById('retailer-tabs');
  const panels = document.getElementById('retailer-panels');
  if (!tabs || !panels) return;

  tabs.innerHTML = retailers.map((r, i) => `
    <button class="tab-btn${i===0?' active':''}" data-idx="${i}">${r.name}</button>
  `).join('');

  panels.innerHTML = retailers.map((r, i) => {
    const rec = r.today_recommendation;
    const exp = r.explanation;
    const maxSens = Math.max(...Object.values(exp.all_sensitivities || {}), 0.01);
    const top5 = (exp.top_factors || []).slice(0, 5);
    const cfs  = (exp.counterfactuals || []).slice(0, 5);

    const slColor = rec.expected_sl >= 90 ? 'var(--success)' :
                    rec.expected_sl >= 75 ? 'var(--warning)' : 'var(--danger)';

    return `
    <div class="demo-panel${i===0?' active':''}" data-idx="${i}">
      <p style="color:var(--text-dim);margin-bottom:1.25rem;font-size:.9rem">${r.description}</p>
      <div class="demo-grid">
        <div class="recommend-card">
          <div class="order-qty-display">
            <div class="order-qty-number">${rec.order_qty.toFixed(0)}</div>
            <div class="order-label">units to order today</div>
          </div>
          <div class="metric-row">
            <span class="metric-name">Current inventory</span>
            <span class="metric-val">${rec.current_inventory.toFixed(0)} units</span>
          </div>
          <div class="metric-row">
            <span class="metric-name">Days of stock</span>
            <span class="metric-val">${rec.days_of_stock.toFixed(1)} days</span>
          </div>
          <div class="metric-row">
            <span class="metric-name">Expected daily demand</span>
            <span class="metric-val">${rec.expected_daily_demand.toFixed(1)} units</span>
          </div>
          <div class="metric-row">
            <span class="metric-name">30-day service level</span>
            <span class="metric-val" style="color:${slColor}">${rec.expected_sl.toFixed(1)}%</span>
          </div>
          <div class="metric-row">
            <span class="metric-name">Policy</span>
            <span class="metric-val" style="font-size:.75rem;color:var(--text-dim)">${r.policy}</span>
          </div>
          <button class="why-btn" onclick="toggleExplainer(this)">
            Why this recommendation?
          </button>
          <div class="explainer-panel">
            <p style="font-size:.8rem;font-weight:600;color:var(--text);margin-bottom:.75rem">
              Top factors driving this decision
            </p>
            ${top5.map(f => `
              <div class="bar-row">
                <span class="bar-label">${f.feature.replace('_',' ')}</span>
                <div class="bar-track">
                  <div class="bar-fill" style="width:${(f.sensitivity/maxSens*100).toFixed(0)}%"></div>
                </div>
                <span class="bar-val">${(f.share*100).toFixed(0)}%</span>
              </div>
            `).join('')}
            <p style="font-size:.8rem;font-weight:600;color:var(--text);margin:.75rem 0 .5rem">
              What if things were different?
            </p>
            <ul class="cf-list">
              ${cfs.map(c => `<li>${c}</li>`).join('')}
            </ul>
          </div>
        </div>
        <div class="chart-wrap">
          <p style="font-size:.8rem;color:var(--text-dim);margin-bottom:.75rem">
            Inventory level — next 30 days
          </p>
          <canvas id="chart-retailer-${i}" height="220"></canvas>
        </div>
      </div>
    </div>`;
  }).join('');

  // Tab switching
  tabs.addEventListener('click', e => {
    const btn = e.target.closest('.tab-btn');
    if (!btn) return;
    const idx = +btn.dataset.idx;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.demo-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.querySelector(`.demo-panel[data-idx="${idx}"]`).classList.add('active');
    drawRetailerChart(idx);
  });

  // Draw first chart
  drawRetailerChart(0);
}

function toggleExplainer(btn) {
  const panel = btn.nextElementSibling;
  panel.classList.toggle('visible');
  btn.textContent = panel.classList.contains('visible')
    ? 'Hide explanation' : 'Why this recommendation?';
}

function drawRetailerChart(idx) {
  const retailers = DATA.retailers;
  if (!retailers || !retailers[idx]) return;
  const r = retailers[idx];
  const forecast = r.forecast_30d || [];

  const canvasId = `chart-retailer-${idx}`;
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  if (CHARTS[canvasId]) { CHARTS[canvasId].destroy(); }

  const labels = forecast.map(d => `Day ${d.day}`);
  const invData = forecast.map(d => d.inventory);
  const ordData = forecast.map(d => d.order_qty);

  CHARTS[canvasId] = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'Inventory',
          data: invData,
          borderColor: 'hsl(160,80%,50%)',
          backgroundColor: 'hsl(160,80%,15%,0.2)',
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          borderWidth: 2,
        },
        {
          label: 'Order placed',
          data: ordData,
          borderColor: 'hsl(40,90%,55%)',
          backgroundColor: 'transparent',
          type: 'bar',
          yAxisID: 'y2',
          barThickness: 6,
        },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          labels: { color: 'hsl(220,10%,60%)', font: { size: 11 } }
        },
        tooltip: {
          backgroundColor: 'hsl(220,14%,12%)',
          borderColor: 'hsl(220,14%,22%)',
          borderWidth: 1,
          titleColor: 'hsl(220,10%,95%)',
          bodyColor: 'hsl(220,10%,60%)',
        },
      },
      scales: {
        x: {
          ticks: { color: 'hsl(220,10%,60%)', maxTicksLimit: 8, font:{size:10} },
          grid: { color: 'hsl(220,14%,14%)' },
        },
        y: {
          ticks: { color: 'hsl(220,10%,60%)', font:{size:10} },
          grid: { color: 'hsl(220,14%,14%)' },
          title: { display:true, text:'Inventory (units)', color:'hsl(220,10%,50%)', font:{size:10} },
        },
        y2: {
          position: 'right',
          ticks: { color: 'hsl(40,90%,55%)', font:{size:10} },
          grid: { display: false },
          title: { display:true, text:'Order qty', color:'hsl(40,90%,55%)', font:{size:10} },
        },
      },
    },
  });
}

// ── comparison tables ─────────────────────────────────────────────────────────
function renderComparison() {
  const d = DATA.comparison;
  if (!d) return;

  // Forecaster table
  const ftable = document.getElementById('forecaster-table');
  if (ftable) {
    const sorted = [...d.forecasters].sort((a,b) => (a.rmse||999) - (b.rmse||999));
    ftable.innerHTML = sorted.map(m => {
      const isBest = m.rmse === Math.min(...sorted.map(x=>x.rmse||999));
      return `<tr>
        <td><strong>${m.name}</strong><br><span style="font-size:.75rem;color:var(--text-dim)">${m.type.replace('_',' ')}</span></td>
        <td class="mono${isBest?' best':''}">${(m.rmse||'-').toFixed ? m.rmse.toFixed(3) : '-'}</td>
        <td class="mono${isBest?' best':''}">${(m.mae||'-').toFixed ? m.mae.toFixed(3) : '-'}</td>
        <td class="mono">${(m.smape||'-').toFixed ? m.smape.toFixed(1)+'%' : '-'}</td>
        <td class="mono">${m.train_time_s != null ? m.train_time_s+'s' : '<span style="color:var(--accent)">0 (zero-shot)</span>'}</td>
        <td class="mono">${m.inference_ms != null ? m.inference_ms+'ms' : '-'}</td>
        <td>${m.beats_naive ? '<span class="badge badge-green">Yes</span>' : '<span class="badge badge-red">No</span>'}</td>
      </tr>`;
    }).join('');
  }

  // Policy table
  const ptable = document.getElementById('policy-table');
  if (ptable) {
    const sorted = [...d.policies].sort((a,b) => b.mean_profit - a.mean_profit);
    ptable.innerHTML = sorted.map(m => {
      const isBest = m.mean_profit === Math.max(...sorted.map(x=>x.mean_profit));
      const sl     = m.service_level || 0;
      const slColor = sl >= 90 ? 'var(--success)' : sl >= 80 ? 'var(--warning)' : 'var(--danger)';
      return `<tr>
        <td><strong>${m.name}</strong><br><span style="font-size:.75rem;color:var(--text-dim)">${m.description}</span></td>
        <td class="mono${isBest?' best':''}">${(m.mean_profit||0).toLocaleString('en-US', {maximumFractionDigits:0})}</td>
        <td class="mono" style="color:${slColor}">${sl.toFixed(1)}%</td>
        ${m.store_sl ? `
          <td class="mono" style="font-size:.8rem">
            A: ${m.store_sl.A}% / B: ${m.store_sl.B}% / C: ${m.store_sl.C}%
          </td>` : '<td class="mono" style="color:var(--text-dim)">—</td>'}
      </tr>`;
    }).join('');
  }
}

// ── disruption chart ──────────────────────────────────────────────────────────
function renderDisruptions() {
  const d = DATA.disruptions;
  if (!d) return;

  const btnContainer = document.getElementById('scenario-buttons');
  if (!btnContainer) return;

  btnContainer.innerHTML = d.scenarios.map((s, i) => `
    <button class="scenario-btn${i===0?' active':''}" data-sc="${s.scenario}">
      ${s.label}
    </button>
  `).join('');

  btnContainer.addEventListener('click', e => {
    const btn = e.target.closest('.scenario-btn');
    if (!btn) return;
    document.querySelectorAll('.scenario-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    drawDisruptionChart(btn.dataset.sc);
    renderDisruptionStats(btn.dataset.sc);
  });

  drawDisruptionChart(d.scenarios[0].scenario);
  renderDisruptionStats(d.scenarios[0].scenario);
}

function drawDisruptionChart(scenarioKey) {
  const d = DATA.disruptions;
  const sc = d.scenarios.find(s => s.scenario === scenarioKey);
  if (!sc) return;

  const canvas = document.getElementById('disruption-chart');
  if (!canvas) return;
  if (CHARTS['disruption']) CHARTS['disruption'].destroy();

  const policies = Object.keys(sc.policies);
  const datasets = policies.map((pol, i) => {
    const daily = sc.policies[pol].daily_sl || [];
    const pct   = daily.map(v => v != null ? +(v*100).toFixed(1) : null);
    return {
      label: pol,
      data:  pct,
      borderColor: Object.values(POLICY_COLORS)[i] || 'hsl(220,60%,60%)',
      backgroundColor: 'transparent',
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 2,
    };
  });

  CHARTS['disruption'] = new Chart(canvas, {
    type: 'line',
    data: { labels: Array.from({length:90},(_,i)=>`D${i+1}`), datasets },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: 'hsl(220,10%,60%)', font:{size:11} } },
        tooltip: {
          backgroundColor: 'hsl(220,14%,12%)',
          borderColor: 'hsl(220,14%,22%)',
          borderWidth:1,
          titleColor: 'hsl(220,10%,95%)',
          bodyColor: 'hsl(220,10%,60%)',
        },
      },
      scales: {
        x: { ticks: { color:'hsl(220,10%,50%)', maxTicksLimit:10, font:{size:10} }, grid:{color:'hsl(220,14%,14%)'} },
        y: {
          min: 0, max: 100,
          title: { display:true, text:'Service Level (%)', color:'hsl(220,10%,50%)', font:{size:10} },
          ticks: { color:'hsl(220,10%,50%)', font:{size:10}, callback: v => v+'%' },
          grid:  { color:'hsl(220,14%,14%)' },
        },
      },
    },
  });
}

function renderDisruptionStats(scenarioKey) {
  const d = DATA.disruptions;
  const sc = d.scenarios.find(s => s.scenario === scenarioKey);
  if (!sc) return;

  const el = document.getElementById('disruption-stats');
  if (!el) return;

  const policies = Object.entries(sc.policies);
  const bestProfit = Math.max(...policies.map(([,v]) => v.mean_profit || -Infinity));

  el.innerHTML = policies.map(([name, stats]) => {
    const isBest = stats.mean_profit === bestProfit;
    return `
    <div style="padding:.6rem 0;border-bottom:1px solid var(--border)">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="font-size:.82rem;font-weight:${isBest?700:400};color:${isBest?'var(--accent)':'var(--text-dim)'}">
          ${name}
        </span>
        ${isBest ? '<span class="badge badge-green">Best</span>' : ''}
      </div>
      <div style="display:flex;gap:.75rem;margin-top:.3rem">
        <span style="font-size:.78rem;color:var(--text-dim)">
          Profit: <span class="mono">${stats.mean_profit != null ? stats.mean_profit.toLocaleString('en-US',{maximumFractionDigits:0}) : '—'}</span>
        </span>
        <span style="font-size:.78rem;color:var(--text-dim)">
          SL: <span class="mono">${stats.service_level != null ? stats.service_level+'%' : '—'}</span>
        </span>
      </div>
    </div>`;
  }).join('');
}

// ── explainability showcase ───────────────────────────────────────────────────
function renderExplainability() {
  const d = DATA.explainability;
  if (!d) return;

  const showcase = d.showcase;
  if (!showcase) return;

  const el = document.getElementById('explainability-showcase');
  if (!el) return;

  const top5 = (showcase.top_factors || []).slice(0, 5);
  const cfs  = (showcase.counterfactuals || []).slice(0, 5);
  const maxS = Math.max(...top5.map(f=>f.sensitivity), 0.01);

  el.innerHTML = `
    <div class="card-grid stagger" style="grid-template-columns:1fr 1fr">
      <div class="card">
        <h3 style="margin-bottom:.75rem">Original Decision</h3>
        <div style="font-size:3rem;font-weight:800;font-family:monospace;color:var(--accent)">
          ${showcase.original_decision.toFixed(0)}
        </div>
        <div style="font-size:.78rem;color:var(--text-dim);margin-bottom:1rem">units ordered</div>
        <div style="font-size:.8rem">
          ${Object.entries(showcase.original_state||{}).map(([k,v]) =>
            `<div class="metric-row">
              <span class="metric-name">${k.replace(/_/g,' ')}</span>
              <span class="metric-val">${v}</span>
            </div>`
          ).join('')}
        </div>
      </div>
      <div class="card">
        <h3 style="margin-bottom:.75rem">Top 5 Drivers</h3>
        ${top5.map(f => `
          <div class="bar-row">
            <span class="bar-label">${f.feature.replace(/_/g,' ')}</span>
            <div class="bar-track">
              <div class="bar-fill" style="width:${(f.sensitivity/maxS*100).toFixed(0)}%"></div>
            </div>
            <span class="bar-val">${(f.share*100).toFixed(0)}%</span>
          </div>
        `).join('')}
      </div>
    </div>
    <div class="card" style="margin-top:1.25rem">
      <h3 style="margin-bottom:.75rem">Counterfactuals — What Would Have Changed?</h3>
      <ul class="cf-list">
        ${cfs.map(c => `<li>${c}</li>`).join('')}
      </ul>
    </div>
  `;

  // Draw sensitivity radar / bar chart
  const canvas = document.getElementById('explainability-chart');
  if (canvas && top5.length) {
    if (CHARTS['explainability']) CHARTS['explainability'].destroy();
    CHARTS['explainability'] = new Chart(canvas, {
      type: 'bar',
      data: {
        labels: top5.map(f => f.feature.replace(/_/g,' ')),
        datasets: [{
          label: 'Sensitivity',
          data: top5.map(f => f.sensitivity),
          backgroundColor: top5.map(f =>
            f.direction === 'positive' ? 'hsl(160,80%,35%)' : 'hsl(0,70%,35%)'
          ),
          borderRadius: 4,
        }],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: 'hsl(220,14%,12%)',
            borderColor: 'hsl(220,14%,22%)', borderWidth:1,
            titleColor:'hsl(220,10%,95%)', bodyColor:'hsl(220,10%,60%)',
          },
        },
        scales: {
          x: { ticks:{color:'hsl(220,10%,60%)',font:{size:10}}, grid:{color:'hsl(220,14%,14%)'} },
          y: { ticks:{color:'hsl(220,10%,60%)',font:{size:11}}, grid:{color:'transparent'} },
        },
      },
    });
  }
}

// ── hack detection ────────────────────────────────────────────────────────────
function renderHackDetection() {
  const d = DATA.hack_detection;
  if (!d) return;

  const el = document.getElementById('hack-findings');
  if (!el) return;

  const findings = d.findings || [];
  el.innerHTML = findings.map(f => {
    const slData = f.data && f.data.store_sl;
    const valSL  = f.data && f.data.val_store_sl;
    const testSL = f.data && f.data.test_store_sl;

    let dataViz = '';
    if (slData) {
      dataViz = `
        <div class="sl-bars" style="margin-top:.75rem">
          ${Object.entries(slData).map(([s, v]) => {
            const pct = v;
            const color = pct >= 90 ? 'var(--success)' : pct >= 60 ? 'var(--warning)' : 'var(--danger)';
            return `<div class="sl-bar-wrap">
              <div class="sl-bar-track" title="Store ${s}: ${pct}% SL">
                <div class="sl-bar-fill" style="height:${pct}%;background:${color}"></div>
              </div>
              <div class="sl-bar-label">Store ${s}</div>
              <div class="sl-bar-val" style="color:${color}">${pct.toFixed(0)}%</div>
            </div>`;
          }).join('')}
        </div>
      `;
    }
    if (valSL && testSL) {
      dataViz = `
        <div style="margin-top:.75rem;font-size:.8rem">
          <div style="color:var(--text-dim);margin-bottom:.4rem">Validation vs Test service level:</div>
          ${Object.keys(valSL).map(s => {
            const val  = valSL[s];
            const test = testSL[s];
            const migrated = val >= 80 && test < 80;
            return `<div class="metric-row">
              <span class="metric-name">Store ${s}</span>
              <span class="metric-val">
                Val: <span class="mono">${val.toFixed(0)}%</span>
                &rarr; Test: <span class="mono" style="color:${migrated?'var(--danger)':'var(--success)'}">${test.toFixed(0)}%</span>
                ${migrated ? '<span class="badge badge-red" style="font-size:.65rem;margin-left:.25rem">migrated</span>' : ''}
              </span>
            </div>`;
          }).join('')}
        </div>
      `;
    }

    return `
    <div class="hack-finding severity-${f.severity}">
      <span class="severity-badge severity-${f.severity}">${f.severity === 'none' ? 'PASS' : f.severity.toUpperCase()}</span>
      <h3>${f.title}</h3>
      <p style="margin:.5rem 0;font-size:.88rem">${f.finding}</p>
      ${dataViz}
      ${f.fix ? `
        <div style="margin-top:.75rem;padding:.6rem .75rem;background:hsl(150,60%,6%);border-radius:8px;border:1px solid hsl(150,50%,20%)">
          <span style="font-size:.75rem;color:var(--success);font-weight:600">FIX APPLIED: </span>
          <span style="font-size:.78rem;color:var(--text-dim)">${f.fix} &mdash; ${f.fix_result}</span>
        </div>
      ` : ''}
    </div>`;
  }).join('');

  // Summary
  const sumEl = document.getElementById('hack-summary');
  if (sumEl && d.summary) {
    const s = d.summary;
    sumEl.innerHTML = `
      <div class="metric-row">
        <span class="metric-name">Policies audited</span>
        <span class="metric-val mono">${s.total_audited}</span>
      </div>
      <div class="metric-row">
        <span class="metric-name">Issues detected</span>
        <span class="metric-val mono" style="color:${s.total_flagged > 0 ? 'var(--warning)' : 'var(--success)'}">${s.total_flagged}</span>
      </div>
      <div class="metric-row">
        <span class="metric-name">SL abandonment floor</span>
        <span class="metric-val mono">${(s.thresholds?.sl_abandonment * 100).toFixed(0)}%</span>
      </div>
      <div class="metric-row">
        <span class="metric-name">Generalization gap threshold</span>
        <span class="metric-val mono">${(s.thresholds?.gen_gap * 100).toFixed(0)}%</span>
      </div>
    `;
  }
}

// ── intersection observer (animate-in) ───────────────────────────────────────
function setupScrollAnimations() {
  const obs = new IntersectionObserver((entries) => {
    entries.forEach(e => {
      if (e.isIntersecting) {
        e.target.classList.add('visible');
        obs.unobserve(e.target);
      }
    });
  }, { threshold: 0.1, rootMargin: '0px 0px -40px 0px' });

  document.querySelectorAll('.animate-in').forEach(el => obs.observe(el));
}

// ── expand cards (problem section) ───────────────────────────────────────────
function setupExpandCards() {
  document.querySelectorAll('.expand-card').forEach(card => {
    card.addEventListener('click', () => card.classList.toggle('open'));
  });
}

// ── smooth scroll CTAs ────────────────────────────────────────────────────────
function setupCTAs() {
  document.querySelectorAll('[data-scroll]').forEach(el => {
    el.addEventListener('click', e => {
      e.preventDefault();
      const target = document.querySelector(el.dataset.scroll);
      if (target) target.scrollIntoView({ behavior: 'smooth' });
    });
  });
}

// ── nav highlight ─────────────────────────────────────────────────────────────
function setupNavHighlight() {
  const sections = document.querySelectorAll('section[id]');
  const navLinks = document.querySelectorAll('.nav-links a');
  const obs = new IntersectionObserver((entries) => {
    entries.forEach(e => {
      if (e.isIntersecting) {
        navLinks.forEach(l => l.style.color = '');
        const link = document.querySelector(`.nav-links a[href="#${e.target.id}"]`);
        if (link) link.style.color = 'var(--accent)';
      }
    });
  }, { threshold: 0.4 });
  sections.forEach(s => obs.observe(s));
}

// ── init ──────────────────────────────────────────────────────────────────────
async function init() {
  await loadData();
  renderOverview();
  renderRetailers();
  renderComparison();
  renderDisruptions();
  renderExplainability();
  renderHackDetection();
  setupScrollAnimations();
  setupExpandCards();
  setupCTAs();
  setupNavHighlight();
}

document.addEventListener('DOMContentLoaded', init);
