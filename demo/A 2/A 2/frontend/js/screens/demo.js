import { API } from '../api.js';
import { applyDefaults, lineChart, barChart, C } from '../components/chart-helpers.js?v=accuracy4';
import { AppState } from '../state.js';
import { adjustedCoverageDays, deriveRecommendation, modelScoreMap, selectedForecastColor, selectedForecastLabel, selectedForecastSeries } from '../domain/policy.js?v=accuracy4';

const ACTIONS = [0, 50, 150, 300, 500, 750, 1000, 1500];

let _data = null;
let _syncing = false;
let _state = {
  skuId: null,
  inventory: 0,
  demand: 60,
  lead: 4,
  shock: 0,
  scenario: 0,
};

export async function render(container) {
  applyDefaults();
  document.body.classList.add('steam-demo-mode');
  const [skus, inventory, forecasts, recommendations, explanations, comparison, disruptions, alerts] =
    await Promise.all([
      API.skus(), API.inventory(), API.forecasts(), API.recommendations(),
      API.explanations(), API.comparison(), API.disruptions(), API.alerts(),
    ]);

  _data = { skus, inventory, forecasts, recommendations, explanations, comparison, disruptions, alerts };
  const first = recommendations[0] || skus[0];
  _state.skuId = first.sku_id || first.id;
  _hydrateFromSku(_state.skuId);
  const forecastWinner = (comparison.forecasters || [])
    .filter(m => m.name !== 'Naive Baseline' && m.rmse)
    .reduce((best, m) => !best || m.rmse < best.rmse ? m : best, null);
  const scenarioCount = disruptions.scenarios?.length || 0;

  container.innerHTML = `
    <div class="steam-demo animate-in">
      <canvas id="demo-particles" aria-hidden="true"></canvas>
      <header class="premium-demo-dock">
        <a class="dock-brand" href="#/demo" aria-label="SmartStock showcase">
          <span>SS</span>
          <div>
            <strong>SmartStock</strong>
            <small>AI retail studio</small>
          </div>
        </a>
        <nav class="dock-links" aria-label="Project navigation">
          <a class="active" href="#/demo"><i data-lucide="sparkles"></i><span>Showcase</span></a>
          <a href="#/dashboard"><i data-lucide="command"></i><span>Command</span></a>
          <a href="#/inventory"><i data-lucide="package-check"></i><span>Stock Ops</span></a>
          <a href="#/orders"><i data-lucide="shopping-bag"></i><span>Orders</span></a>
          <a href="#/forecasts"><i data-lucide="activity"></i><span>Demand AI</span></a>
          <a href="#/stress"><i data-lucide="shield-alert"></i><span>Risk Lab</span></a>
        </nav>
        <div class="dock-status"><span></span>Demo-ready</div>
      </header>
      <section class="steam-hero">
        <div class="steam-hero-main">
          <div class="steam-section-label">Premium project showcase</div>
          <h1>SmartStock Inventory Decision Engine</h1>
          <p id="demo-phase-copy">
            Forecast demand, choose an order quantity, compare the classical rule, and explain the decision through one interactive retail AI prototype.
          </p>
          <div class="steam-tags">
            <span>Chronos + LightGBM</span>
            <span>Replenishment Policy</span>
            <span>Explainable AI</span>
            <span>Scenario Stress Lab</span>
          </div>
          <div class="engine-stage-strip" id="demo-phase-links">
            <button class="active" data-phase="think">
              <i data-lucide="radar"></i>
              <span>Signals</span>
              <small>Stock, demand, supplier delay</small>
            </button>
            <button data-phase="forecast">
              <i data-lucide="waves"></i>
              <span>Demand AI</span>
              <small>Forecast next movement</small>
            </button>
            <button data-phase="decide">
              <i data-lucide="route"></i>
              <span>Order Policy</span>
              <small>Pick a practical quantity</small>
            </button>
            <button data-phase="explain">
              <i data-lucide="scan-search"></i>
              <span>Explain Why</span>
              <small>Show reason and baseline</small>
            </button>
          </div>
          <div class="steam-actions">
            <button class="steam-primary-btn" data-jump="#decision-cockpit">
              <i data-lucide="play" style="width:15px;height:15px"></i>
              Launch Console
            </button>
            <a class="steam-secondary-btn" href="#/stress">
              <i data-lucide="flame" style="width:15px;height:15px"></i>
              Open Stress Lab
            </a>
          </div>
        </div>
        <div class="steam-hero-side">
          <div class="steam-preview-card">
            <div class="steam-preview-top">
              <span>Next replenishment</span>
              <strong id="demo-order">0</strong>
            </div>
            <div class="steam-preview-line" id="demo-order-help">-</div>
            <div class="steam-preview-grid">
              <div><span>Service</span><strong id="demo-service">0%</strong></div>
              <div><span>Classical</span><strong id="demo-classical">0</strong></div>
              <div><span>SKUs</span><strong>${skus.length}</strong></div>
            </div>
          </div>
          <div class="steam-thumb-row">
            <div>Demand</div><div>Policy</div><div>Narrative</div>
          </div>
        </div>
      </section>

      <div class="steam-store-shell">
        <aside class="steam-discovery">
          <div class="steam-side-title">Choose retail item</div>
          <select class="select" id="demo-sku-select">
            ${skus.map(s => `<option value="${s.id}" ${s.id === _state.skuId ? 'selected' : ''}>${s.name}</option>`).join('')}
          </select>
          <div class="steam-side-title subtle">PROJECT PROOF POINTS · all items</div>
          <div class="steam-mini-stats">
            ${metricCard('389', 'test days', 'Global test window (M5 FOODS_3_090)', 'calendar-range')}
            ${metricCard(forecastWinner?.rmse?.toFixed?.(2) || '—', 'RMSE', 'Chronos-Bolt on FOODS_3_090 (foundation model RMSE)', 'activity')}
            ${metricCard(String(scenarioCount || 6), 'scenarios', 'Disruption simulator (applies to all SKUs)', 'shield-alert')}
          </div>
        </aside>

        <main class="steam-content">
          <section class="steam-console" id="decision-cockpit">
            <div class="steam-console-head">
              <div>
                <div class="steam-section-label">Decision console</div>
                <h2 id="demo-sku-name">-</h2>
                <div class="demo-subline" id="demo-sku-meta">-</div>
              </div>
              <button class="steam-primary-btn compact" id="demo-pressure-btn">
                <i data-lucide="zap" style="width:14px;height:14px"></i>
                Simulate Spike
              </button>
            </div>

            <div class="steam-controls">
              ${sliderControl('inventory', 'Available stock', 0, 2000, _state.inventory, 'units')}
              ${sliderControl('demand', 'Daily demand', 1, 200, _state.demand, 'units/day')}
              ${sliderControl('lead', 'Supplier lead time', 1, 21, _state.lead, 'days')}
              ${sliderControl('shock', 'Demand shock', 0, 200, _state.shock, '%')}
            </div>

            <div class="steam-decision-strip">
              <div><span>Recommended order</span><strong id="demo-order-secondary">0</strong></div>
              <div><span>Projected service level</span><strong id="demo-service-secondary">0%</strong><small id="demo-risk">-</small></div>
              <div><span>Classical baseline</span><strong id="demo-classical-secondary">0</strong></div>
              <div><span>Main reason</span><strong id="demo-main-reason">Healthy stock</strong></div>
            </div>

            <div class="demo-action-map" id="demo-action-map"></div>
          </section>

          <section class="steam-shelf">
            <div class="steam-shelf-title">Model evidence</div>
            <div class="steam-module-grid">
              <article class="steam-module-card large">
                <div class="card-title">Demand signal vs forecast</div>
                <div class="chart-wrap" style="height:210px"><canvas id="demo-forecast-chart"></canvas></div>
              </article>
              <article class="steam-module-card">
                <div class="card-title">Policy score map</div>
                <div class="chart-wrap" style="height:210px"><canvas id="demo-action-chart"></canvas></div>
              </article>
              <article class="steam-module-card">
                <div class="card-title">Decision drivers</div>
                <div class="demo-factor-list" id="demo-factors"></div>
              </article>
            </div>
          </section>

          <section class="steam-shelf">
            <div class="steam-shelf-title">Explainability and resilience</div>
            <div class="steam-bottom-grid">
              <div class="steam-module-card">
                <div class="card-title">Counterfactuals</div>
                <div class="demo-counterfactuals" id="demo-counterfactuals"></div>
              </div>
              <div class="steam-module-card">
                <div class="card-title">Forecast models</div>
                <div style="font-size:11px;color:var(--text-tertiary);margin-bottom:8px">
                  RMSE improvement over naive baseline (repeat-last). Operational
                  accuracy: 84.1% — see Demand AI tab.
                </div>
                <div id="demo-models"></div>
              </div>
              <div class="steam-module-card stress">
                <div class="card-title">Policy profit under selected scenario</div>
                <div class="demo-scenario-grid" id="demo-scenarios"></div>
                <div class="chart-wrap" style="height:220px"><canvas id="demo-stress-chart"></canvas></div>
                <div class="demo-stress-verdict" id="demo-stress-verdict"></div>
              </div>
            </div>
          </section>
        </main>
      </div>

      <section class="steam-guide">
        ${scriptStep('01', 'Pick a product', 'Load stock, forecast, and explanation data for that SKU.')}
        ${scriptStep('02', 'Move the sliders', 'Increase demand or shock and watch the order quantity react.')}
        ${scriptStep('03', 'Read the evidence', 'Forecasts, factors, stress tests, and baselines explain the system.')}
        ${scriptStep('04', 'Tell the story', 'SmartStock is an inventory decision-support prototype, not a static dashboard.')}
        <div class="demo-note">
          <div class="demo-note-title">Research honesty</div>
          <p>Forecasting is the strongest AI layer. Ordering is compared honestly against tuned classical policies.</p>
        </div>
      </section>
    </div>`;

  window.lucide?.createIcons?.();
  _bind();
  _updateAll();
  container._cleanup = _initParticleField();
}

function _hydrateFromSku(skuId) {
  const inv = _data.inventory[skuId] || {};
  const fc = _data.forecasts[skuId] || {};
  const sku = (_data.skus || []).find(s => s.id === skuId) || {};
  const categoryLeadDays = { FOODS: 3, HOBBIES: 5, HOUSEHOLD: 4 };
  const categoryPrefix = (sku.category || '').split('_')[0];
  _state.inventory = Number(inv.total_stock || inv.warehouse || 400);
  _state.demand = Number(inv.avg_daily_demand || fc.avg_daily_forecast || 60);
  _state.lead = Number(inv.lead_time_days) || categoryLeadDays[categoryPrefix] || 3;
  _state.shock = 0;
  console.log('[demo] Hydrating SKU', skuId, {
    stock: inv.total_stock,
    demand: inv.avg_daily_demand,
    lead: inv.lead_time_days,
    state: { ..._state },
  });
}

function _bind() {
  document.querySelectorAll('[data-jump]').forEach(btn => {
    btn.addEventListener('click', () => document.querySelector(btn.dataset.jump)?.scrollIntoView({ behavior: 'auto' }));
  });

  document.getElementById('demo-sku-select')?.addEventListener('change', e => {
    _state.skuId = e.target.value;
    _hydrateFromSku(_state.skuId);
    _syncSliders();
    _updateAll();
  });

  ['inventory', 'demand', 'lead', 'shock'].forEach(id => {
    document.getElementById(`demo-${id}`)?.addEventListener('input', e => {
      if (_syncing) return; // synthetic event from _syncSliders — skip recursive update
      _state[id] = Number(e.target.value);
      _updateAll();
    });
  });

  document.getElementById('demo-pressure-btn')?.addEventListener('click', () => {
    const btn = document.getElementById('demo-pressure-btn');
    const spikeValue = _backendSpikePct();
    const isCurrentlyCalm = _state.shock < 5;

    if (isCurrentlyCalm) {
      _state.shock = spikeValue || 90;
      btn.innerHTML = '<i data-lucide="rotate-ccw" style="width:14px;height:14px"></i> Reset to Calm';
    } else {
      _state.shock = 0;
      btn.innerHTML = '<i data-lucide="zap" style="width:14px;height:14px"></i> Simulate Spike';
    }
    window.lucide?.createIcons?.();

    _syncSliders();
    _updateAll();

    // Visual feedback: pulse affected fields so the user sees the change
    ['demo-order', 'demo-service', 'demo-risk', 'demo-main-reason'].forEach(id => {
      const el = document.getElementById(id);
      if (!el) return;
      el.style.transition = 'background-color 200ms ease-out, color 200ms ease-out';
      const origBg = el.style.backgroundColor;
      el.style.backgroundColor = isCurrentlyCalm ? 'hsla(40,80%,55%,0.18)' : 'hsla(160,75%,52%,0.18)';
      setTimeout(() => { el.style.backgroundColor = origBg; }, 600);
    });

    const shockSlider = document.getElementById('demo-shock');
    if (shockSlider) {
      shockSlider.style.transition = 'transform 220ms ease-out';
      shockSlider.style.transform = 'scaleY(1.4)';
      setTimeout(() => { shockSlider.style.transform = 'scaleY(1)'; }, 260);
    }
  });

  document.getElementById('demo-phase-links')?.addEventListener('click', e => {
    const btn = e.target.closest('[data-phase]');
    if (!btn) return;
    const copy = {
      think: 'Signals layer: the system reads stock, demand velocity, supplier delay, and shock pressure before making a recommendation.',
      forecast: 'Demand AI layer: saved Chronos, N-HITS, and LightGBM outputs create the short-term demand expectation.',
      decide: 'Order Policy layer: the engine maps the current situation to one of eight practical replenishment quantities.',
      explain: 'Explain Why layer: the prototype exposes service level, residual risk, decision drivers, and the classical baseline.',
    };
    document.querySelectorAll('#demo-phase-links button').forEach(b => b.classList.toggle('active', b === btn));
    setText('demo-phase-copy', copy[btn.dataset.phase] || copy.think);
  });

  document.getElementById('demo-scenarios')?.addEventListener('click', e => {
    const card = e.target.closest('[data-scenario]');
    if (!card) return;
    _state.scenario = Number(card.dataset.scenario);
    _renderStress();
  });
}

function _syncSliders() {
  _syncing = true;
  ['inventory', 'demand', 'lead', 'shock'].forEach(id => {
    const input = document.getElementById(`demo-${id}`);
    if (!input) return;
    input.value = _state[id];
    // Browser silently clamps input.value to [min, max]. If clamping occurred,
    // pull state back to the clamped value so the visible label, thumb, and
    // downstream calculations all agree.
    const clamped = Number(input.value);
    if (Number.isFinite(clamped) && Math.abs(clamped - _state[id]) > 0.01) {
      _state[id] = clamped;
    }
    // Force a visual update — some browsers don't repaint the slider thumb
    // until an event fires. The _syncing guard above prevents the input
    // handler from re-running _updateAll.
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  _syncing = false;
}

function _updateAll() {
  _renderSku();
  _renderSliderValues();
  _renderDecision();
  _renderExplanation();
  _renderModels();
  _renderCharts();
  _renderStress();
  window.lucide?.createIcons?.();
}

function _renderSku() {
  const sku = _data.skus.find(s => s.id === _state.skuId) || {};
  const inv = _data.inventory[_state.skuId] || {};
  const name = document.getElementById('demo-sku-name');
  const meta = document.getElementById('demo-sku-meta');
  if (name) name.textContent = sku.name || _state.skuId;
  if (meta) {
    meta.textContent =
      `${sku.id || ''} | ${sku.category || 'Retail'} | ${sku.supplier || 'Supplier'} | current stock ${inv.total_stock || _state.inventory} units`;
  }
}

function _renderSliderValues() {
  const labels = {
    inventory: `${_state.inventory.toFixed(0)} units`,
    demand: `${_state.demand.toFixed(0)} units/day`,
    lead: `${_state.lead.toFixed(0)} days`,
    shock: `+${_state.shock.toFixed(0)}%`,
  };
  Object.entries(labels).forEach(([k, v]) => {
    const el = document.getElementById(`demo-${k}-value`);
    if (el) el.textContent = v;
  });
}

function _decision() {
  const inv = _data?.inventory?.[_state.skuId] || {};
  const rec = (_data?.recommendations || []).find(r => r.sku_id === _state.skuId);
  const sku = (_data?.skus || []).find(s => s.id === _state.skuId);
  const fc = _data?.forecasts?.[_state.skuId] || {};
  const settings = AppState.getSettings();
  const liveRec = deriveRecommendation(rec || {}, sku || {}, inv, fc, settings);
  const robustPolicy = (_data?.comparison?.policies || []).find(p => /per-node/i.test(p.name || ''));
  const adjustedDemand = _state.demand * (1 + _state.shock / 100);
  const targetCoverageDays = adjustedCoverageDays(Number(liveRec.expected_coverage_days || rec?.expected_coverage_days || 10), settings);
  const safetyDays = Math.max(2, targetCoverageDays - _state.lead);
  const targetStock = adjustedDemand * targetCoverageDays;
  const rawOrder = Math.max(0, targetStock - _state.inventory);
  const isDatasetSnapshot =
    rec &&
    Math.abs(_state.inventory - Number(inv.total_stock || 0)) < 0.5 &&
    Math.abs(_state.demand - Number(inv.avg_daily_demand || 0)) < 0.5 &&
    _state.shock === 0;
  const recommended = isDatasetSnapshot ? liveRec.recommended_order_qty : roundOrder(rawOrder);

  const classicalTrigger = adjustedDemand * 3;
  const classicalTarget = adjustedDemand * 7;
  const classical = _state.inventory < classicalTrigger ? roundOrder(classicalTarget - _state.inventory) : 0;

  const projectedCoverage = (_state.inventory + recommended) / Math.max(1, adjustedDemand);
  const baseService = Number(robustPolicy?.service_level || 93.2);
  const service = clamp(45, 99.5, baseService - Math.max(0, targetCoverageDays - projectedCoverage) * 3.8 - _state.shock * 0.12);
  const stockoutRisk = clamp(0, 100, 100 - service);
  const actionIdx = ACTIONS.indexOf(nearestAction(recommended));

  return { adjustedDemand, targetStock, recommended, classical, projectedCoverage, service, stockoutRisk, actionIdx, safetyDays };
}

function _renderDecision() {
  const d = _decision();
  setText('demo-order', d.recommended.toLocaleString('en-IN'));
  setText('demo-classical', d.classical.toLocaleString('en-IN'));
  setText('demo-service', `${d.service.toFixed(1)}%`);
  setText('demo-order-secondary', d.recommended.toLocaleString('en-IN'));
  setText('demo-classical-secondary', d.classical.toLocaleString('en-IN'));
  setText('demo-service-secondary', `${d.service.toFixed(1)}%`);
  setText('demo-risk', `${d.stockoutRisk.toFixed(1)}% residual stockout risk`);
  setText('demo-order-help', `${Math.round(d.targetStock).toLocaleString('en-IN')} target units after safety buffer`);
  setText('demo-main-reason', _state.shock > 20 ? 'Demand shock' : _state.inventory < d.targetStock ? 'Low stock' : 'Healthy stock');

  const map = document.getElementById('demo-action-map');
  if (!map) return;
  const activeAction = nearestAction(d.recommended);
  map.innerHTML = ACTIONS.map((a, i) => `
    <div class="demo-action-chip ${a === activeAction ? 'active' : ''}">
      <span>A${i}</span>
      <strong>${a}</strong>
    </div>
  `).join('');
}

function _renderExplanation() {
  const ex = _data.explanations[_state.skuId] || {};
  const factors = ex.top_factors || fallbackFactors();
  const list = document.getElementById('demo-factors');
  if (list) {
    list.innerHTML = factors.slice(0, 5).map(f => `
      <div class="demo-factor-row">
        <div class="demo-factor-head">
          <span>${labelize(f.label || f.feature)}</span>
          <strong>${Math.round((f.weight || 0.15) * 100)}%</strong>
        </div>
        <div class="progress-bar"><div class="progress-fill" style="width:${Math.max(6, (f.weight || 0.15) * 100)}%"></div></div>
      </div>
    `).join('');
  }

  const d = _decision();
  const cfs = ex.counterfactuals || [];
  const cf = document.getElementById('demo-counterfactuals');
  if (cf) {
    cf.innerHTML = `
      <div class="demo-section-title">Counterfactuals</div>
      ${(cfs.length ? cfs.slice(0, 4) : [
        { change: 'demand +20%', new_qty: nearestAction(d.recommended * 1.2), delta: '+20%' },
        { change: 'lead time -1 day', new_qty: nearestAction(d.recommended * 0.85), delta: '-15%' },
        { change: 'holding cost 2x', new_qty: nearestAction(d.recommended * 0.7), delta: '-30%' },
      ]).map(row => {
        const qty = Number(row.new_qty) || 0;
        const deltaStr = String(row.delta || '');
        const deltaIsZero = /^[+-]?0$/.test(deltaStr);
        const label = qty > 0
          ? `${qty} units`
          : (deltaIsZero ? 'no order' : (row.delta || '—'));
        return `
          <div class="demo-cf-row">
            <span>${row.change}</span>
            <strong>${label}</strong>
          </div>
        `;
      }).join('')}
    `;
  }
}

function _renderModels() {
  const models = (_data.comparison.forecasters || []).filter(m => m.name !== 'Naive Baseline');
  const wrap = document.getElementById('demo-models');
  if (!wrap) return;
  wrap.innerHTML = models.map(m => {
    const naiveRmse = 27.55;
    const score = Number(m.accuracy_pct ?? (m.rmse ? Math.max(0, Math.min(100, ((naiveRmse - m.rmse) / naiveRmse) * 100)) : 0));
    const best = m.name === 'Chronos-Bolt';
    return `
      <div class="demo-model-row ${best ? 'best' : ''}">
        <div>
          <strong>${m.name}</strong>
          <span>${modelTypeLabel(m.type)}</span>
        </div>
        <div class="demo-model-score">
          <span>+${score.toFixed(1)}% vs naive · RMSE ${m.rmse?.toFixed?.(2) || m.rmse}</span>
          <div class="progress-bar"><div class="progress-fill ${best ? 'success' : ''}" style="width:${Math.min(100, score * 5)}%"></div></div>
        </div>
      </div>
    `;
  }).join('');
}

function _renderCharts() {
  const fc = _data.forecasts[_state.skuId] || {};
  const inv = _data.inventory[_state.skuId] || {};
  const rawActual = (fc.actual_recent_30 || []).slice(-18);
  const settings = AppState.getSettings();
  const selected = selectedForecastSeries(fc, settings);

  const originalDemand = Number(inv.avg_daily_demand || fc.avg_daily_forecast || _state.demand || 60);
  const effectiveDemand = _state.demand * (1 + _state.shock / 100);
  const demandRatio = originalDemand > 0 ? effectiveDemand / originalDemand : 1;
  const scale = v => Number.isFinite(v) ? Math.round(v * demandRatio * 10) / 10 : v;
  // Scale actual history alongside forecasts so the chart stays coherent when slider moves
  const actual = rawActual.map(scale);
  const scaledSelected = selected.map(scale);
  const scaledLightgbm = (fc.lightgbm || []).map(scale);

  // Cap y-axis to demand scale — prevent the inventory reference line from squishing
  // all demand curves to the bottom when inventory >> daily demand
  const demandValues = [...actual, ...scaledSelected, ...scaledLightgbm].filter(v => Number.isFinite(v) && v >= 0);
  const demandMax = demandValues.length ? Math.max(...demandValues) : effectiveDemand;
  // Only show inventory reference line if it's within 4x demand range; otherwise it just distorts
  const showInventoryLine = _state.inventory <= demandMax * 4;
  const yMax = showInventoryLine
    ? Math.ceil(Math.max(demandMax, _state.inventory) * 1.15)
    : Math.ceil(demandMax * 1.3);

  const forecastLen = scaledSelected.length;
  const totalLen = actual.length + forecastLen;
  const inventoryLine = showInventoryLine ? Array(totalLen).fill(_state.inventory) : null;

  const labels = [...actual.map((_, i) => `D-${actual.length - i}`), ...scaledSelected.map((_, i) => `+${i + 1}`)];
  const pad = Array(actual.length).fill(null);
  const canvas = document.getElementById('demo-forecast-chart');
  if (canvas) {
    const datasets = [
      { label: 'Actual', data: [...actual, ...Array(forecastLen).fill(null)], borderColor: 'hsl(220,10%,58%)', pointRadius: 0, borderWidth: 1.5, tension: 0.25 },
      { label: selectedForecastLabel(settings), data: [...pad, ...scaledSelected], borderColor: selectedForecastColor(settings, C), pointRadius: 2, borderWidth: 2.4, tension: 0.25, fill: { target: 'origin', above: 'hsla(160,75%,52%,0.08)' } },
      { label: 'LightGBM forecast', data: [...pad, ...scaledLightgbm], borderColor: C.amber, pointRadius: 0, borderDash: [4, 4], borderWidth: 1.2, tension: 0.25 },
    ];
    if (inventoryLine) {
      datasets.push({ label: 'Current stock', data: inventoryLine, borderColor: C.blue, pointRadius: 0, borderDash: [6, 3], borderWidth: 1, tension: 0 });
    }
    lineChart(canvas, labels, datasets, { scales: { y: { beginAtZero: true, max: yMax } } });
  }

  const d = _decision();
  const activeAction = nearestAction(d.recommended);
  // Pass live slider values so the action chart responds to inventory/demand changes
  const liveInv = { ..._data.inventory[_state.skuId], total_stock: _state.inventory, avg_daily_demand: _state.demand };
  const scores = modelScoreMap(ACTIONS, activeAction, liveInv, fc, settings);
  const actionCanvas = document.getElementById('demo-action-chart');
  if (actionCanvas) {
    barChart(actionCanvas, ACTIONS.map((_, i) => `A${i}`), [{
      label: 'Policy score',
      data: scores,
      backgroundColor: ACTIONS.map(a => a === activeAction ? C.green : 'hsl(220,13%,28%)'),
      borderRadius: 4,
    }], { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, max: 100 } } });
  }
}

function _renderStress() {
  const scenarios = _data.disruptions.scenarios || [];
  const grid = document.getElementById('demo-scenarios');
  if (grid) {
    grid.innerHTML = scenarios.map((s, i) => `
      <button class="demo-scenario-btn ${i === _state.scenario ? 'active' : ''}" data-scenario="${i}">
        <span>${s.label || s.scenario}</span>
      </button>
    `).join('');
  }

  const selected = scenarios[_state.scenario];
  if (!selected) return;
  const policies = selected.policies || {};
  const allNames = Object.keys(policies);

  // Exclude "Default (s,S)" from the bar chart — it's a baseline outlier at -43k
  // that would collapse the y-axis and make the tuned-policy comparison unreadable.
  // It stays in the verdict text so the comparison story is not lost.
  const chartNames = allNames.filter(n => !n.includes('Default'));

  const canvas = document.getElementById('demo-stress-chart');
  if (canvas) {
    barChart(canvas, chartNames, [{
      label: 'Mean profit',
      data: chartNames.map(n => policies[n].mean_profit || 0),
      backgroundColor: chartNames.map(n => n.includes('Grid') ? C.amber : n.includes('CMA') ? C.green : C.blue),
      borderRadius: 5,
    }], { plugins: { legend: { display: false } } });
  }
  // Best across ALL policies (including Default) for the verdict
  const best = allNames.reduce((b, n) => (policies[n].mean_profit || -Infinity) > (policies[b]?.mean_profit || -Infinity) ? n : b, allNames[0]);
  const defaultProfit = policies['Default (s,S)']?.mean_profit;
  const verdict = document.getElementById('demo-stress-verdict');
  if (verdict) {
    const defaultNote = defaultProfit != null
      ? ` &nbsp;·&nbsp; Default (s,S): ${defaultProfit.toLocaleString('en-IN')}`
      : '';
    verdict.innerHTML = `
      <i data-lucide="award" style="width:15px;height:15px"></i>
      In this scenario, <strong>${best}</strong> is strongest on mean profit with service level
      <strong>${(policies[best]?.service_level || 0).toFixed(1)}%</strong>${defaultNote}
    `;
  }
}

function flowNode(title, sub, icon) {
  return `
    <div class="demo-flow-node">
      <i data-lucide="${icon}" style="width:18px;height:18px"></i>
      <strong>${title}</strong>
      <span>${sub}</span>
    </div>
  `;
}

function flowConnector() {
  return '<div class="demo-flow-line"></div>';
}

function metricCard(value, label, note, icon) {
  return `
    <div class="demo-metric-card">
      <i data-lucide="${icon}" style="width:18px;height:18px"></i>
      <strong>${value}</strong>
      <span>${label}</span>
      <small>${note}</small>
    </div>
  `;
}

function scriptStep(num, title, text) {
  return `
    <div class="demo-script-step">
      <span>${num}</span>
      <div>
        <strong>${title}</strong>
        <p>${text}</p>
      </div>
    </div>
  `;
}

function sliderControl(id, label, min, max, value, unit) {
  return `
    <label class="demo-slider-card">
      <span>${label}</span>
      <strong id="demo-${id}-value">${value} ${unit}</strong>
      <input id="demo-${id}" class="slider" type="range" min="${min}" max="${max}" value="${value}">
    </label>
  `;
}

function _backendSpikePct() {
  const alert = (_data.alerts || []).find(a => a.type === 'demand_spike');
  if (Number.isFinite(Number(alert?.spike_pct))) return Math.max(0, Math.round(Number(alert.spike_pct)));

  if (/[+-]\d+/.test(alert?.message || '')) {
    const fromAlert = Number((alert?.message || '').match(/([+-]\d+(?:\.\d+)?)%/)?.[1]);
    if (Number.isFinite(fromAlert)) return Math.max(0, Math.round(fromAlert));
  }

  const scenario = (_data.disruptions?.scenarios || []).find(s => s.scenario === 'demand_spike');
  const multiplier = Number(scenario?.effects?.demand_multiplier);
  if (Number.isFinite(multiplier) && multiplier > 1) {
    return Math.round((multiplier - 1) * 100);
  }
  return 0;
}

function nearestAction(qty) {
  return ACTIONS.reduce((best, a) => Math.abs(a - qty) < Math.abs(best - qty) ? a : best, ACTIONS[0]);
}

function roundOrder(qty) {
  if (qty <= 0) return 0;
  const step = qty < 100 ? 10 : 50;
  return Math.ceil(qty / step) * step;
}

function modelTypeLabel(type) {
  return {
    gradient_boosting: 'gradient boosting',
    neural: 'neural network',
    foundation_model: 'foundation model',
  }[type] || type || 'model';
}

function clamp(min, max, value) {
  return Math.max(min, Math.min(max, value));
}

function labelize(s = '') {
  return s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function unitHash(i, salt = 0) {
  const value = Math.sin((i + 1) * 12.9898 + salt * 78.233) * 43758.5453;
  return value - Math.floor(value);
}

function fallbackFactors() {
  return [
    { label: 'Current inventory level', weight: 0.32 },
    { label: 'Demand forecast', weight: 0.28 },
    { label: 'Supplier lead time', weight: 0.18 },
    { label: 'Demand shock', weight: 0.14 },
    { label: 'Safety stock buffer', weight: 0.08 },
  ];
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function _initParticleField() {
  const canvas = document.getElementById('demo-particles');
  const frame = document.querySelector('.steam-demo') || document.querySelector('.demo-restored');
  if (!canvas || !frame) return () => {};

  const ctx = canvas.getContext('2d');
  const mouse = { x: 0, y: 0, tx: 0, ty: 0 };
  const particles = Array.from({ length: 260 }, (_, i) => ({
    x: unitHash(i, 11),
    y: unitHash(i, 23),
    baseX: unitHash(i, 37),
    baseY: unitHash(i, 41),
    r: 0.55 + unitHash(i, 53) * 1.65,
    vx: (unitHash(i, 67) - 0.5) * 0.00065,
    vy: (unitHash(i, 79) - 0.5) * 0.00065,
    a: 0.14 + unitHash(i, 83) * 0.52,
    hue: i % 4,
  }));

  // Cache frame bounds — avoids getBoundingClientRect on every mouse move
  let _frameRect = { left: 0, top: 0, width: 1, height: 1 };
  let _w = 1, _h = 1;

  // Pre-rendered dot grid — built once on resize, drawn as single drawImage per frame
  const _dotCanvas = document.createElement('canvas');
  const _dotCtx = _dotCanvas.getContext('2d');
  function _buildDotGrid(w, h) {
    _dotCanvas.width = w; _dotCanvas.height = h;
    _dotCtx.clearRect(0, 0, w, h);
    _dotCtx.fillStyle = 'rgba(255,255,255,0.055)';
    const step = 42;
    for (let y = 10; y < h; y += step) {
      for (let x = 10; x < w; x += step) {
        const jitter = Math.sin(x * 12.9898 + y * 78.233) * 43758.5453;
        const frac = jitter - Math.floor(jitter);
        if (frac < 0.42) _dotCtx.fillRect(x + frac * 8, y + frac * 8, 1, 1);
      }
    }
  }

  function resize() {
    _frameRect = frame.getBoundingClientRect();
    _w = _frameRect.width;
    _h = _frameRect.height;
    canvas.width = Math.max(1, Math.floor(_w * devicePixelRatio));
    canvas.height = Math.max(1, Math.floor(_h * devicePixelRatio));
    canvas.style.width = `${_w}px`;
    canvas.style.height = `${_h}px`;
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    _buildDotGrid(Math.ceil(_w), Math.ceil(_h));
  }

  // passive-safe: only reads event coords against cached rect, no DOM writes
  function move(e) {
    mouse.tx = (e.clientX - _frameRect.left) / Math.max(1, _frameRect.width) - 0.5;
    mouse.ty = (e.clientY - _frameRect.top) / Math.max(1, _frameRect.height) - 0.5;
  }

  let _rafId;
  let _lastTick = 0;
  function tick(ts) {
    // Throttle to ~30fps — halves GPU work, background animation is imperceptible at 30fps
    if (ts - _lastTick < 32) { _rafId = requestAnimationFrame(tick); return; }
    _lastTick = ts;
    const w = _w;
    const h = _h;
    mouse.x += (mouse.tx - mouse.x) * 0.06;
    mouse.y += (mouse.ty - mouse.y) * 0.06;
    ctx.clearRect(0, 0, w, h);

    const mx = (mouse.x + 0.5) * w;
    const my = (mouse.y + 0.5) * h;

    drawTexture(ctx, w, h, mouse, mx, my);

    for (const p of particles) {
      p.x += p.vx + mouse.x * 0.00058;
      p.y += p.vy + mouse.y * 0.00058;
      if (p.x < -0.02) p.x = 1.02;
      if (p.x > 1.02) p.x = -0.02;
      if (p.y < -0.02) p.y = 1.02;
      if (p.y > 1.02) p.y = -0.02;
      const px = p.x * w;
      const py = p.y * h;
      const dx = px - mx;
      const dy = py - my;
      const dist = Math.max(1, Math.hypot(dx, dy));
      const pull = Math.max(0, 1 - dist / 280);
      const orbit = Math.max(0, 1 - dist / 180);
      const rx = (dx / dist) * pull * 36 + (-dy / dist) * orbit * 12;
      const ry = (dy / dist) * pull * 36 + (dx / dist) * orbit * 12;
      const colors = [
        `rgba(255,255,255,${p.a})`,
        `rgba(124,148,255,${p.a})`,
        `rgba(255,116,76,${p.a})`,
        `rgba(69,235,187,${p.a})`,
      ];
      ctx.beginPath();
      ctx.fillStyle = colors[p.hue];
      ctx.arc(px + rx, py + ry, p.r, 0, Math.PI * 2);
      ctx.fill();
    }

    drawLinks(ctx, particles, w, h, mouse);

    // Apply CSS spotlight vars here in RAF, not in the pointermove handler
    frame.style.setProperty('--mx', `${50 + mouse.x * 20}%`);
    frame.style.setProperty('--my', `${50 + mouse.y * 20}%`);

    _rafId = requestAnimationFrame(tick);
  }

  resize();
  frame.addEventListener('pointermove', move, { passive: true });
  window.addEventListener('resize', resize);
  _rafId = requestAnimationFrame(tick);

  // Returned cleanup cancels the RAF loop and removes listeners when navigating away
  return () => {
    cancelAnimationFrame(_rafId);
    window.removeEventListener('resize', resize);
    frame.removeEventListener('pointermove', move);
  };
}

function drawTexture(ctx, w, h, mouse, mx, my) {
  ctx.save();

  const glow = ctx.createRadialGradient(mx, my, 0, mx, my, Math.max(w, h) * 0.38);
  glow.addColorStop(0, 'rgba(120,142,255,0.12)');
  glow.addColorStop(0.34, 'rgba(255,112,83,0.04)');
  glow.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, w, h);

  ctx.lineWidth = 1;
  ctx.strokeStyle = 'rgba(255,255,255,0.07)';
  const cell = 28;
  const ox = mouse.x * 38;
  const oy = mouse.y * 38;
  // Batch all vertical grid lines into one path — single stroke() instead of ~40
  ctx.beginPath();
  for (let x = -cell; x < w + cell; x += cell) {
    ctx.moveTo(x + ox, 0);
    ctx.lineTo(x - ox * 0.5, h);
  }
  ctx.stroke();
  // Batch all horizontal grid lines into one path
  ctx.beginPath();
  for (let y = -cell; y < h + cell; y += cell) {
    ctx.moveTo(0, y + oy);
    ctx.lineTo(w, y - oy * 0.5);
  }
  ctx.stroke();

  ctx.strokeStyle = 'rgba(255,255,255,0.035)';
  // Batch diagonal lines into one path
  ctx.beginPath();
  for (let x = -60; x < w + 120; x += 56) {
    ctx.moveTo(x - oy, 0);
    ctx.lineTo(x + h * 0.42 - oy, h);
  }
  ctx.stroke();

  for (let band = 0; band < 8; band++) {
    ctx.beginPath();
    ctx.strokeStyle = band % 3 === 0
      ? 'rgba(126,148,255,0.18)'
      : band % 3 === 1
        ? 'rgba(255,112,83,0.14)'
        : 'rgba(69,235,187,0.10)';
    ctx.lineWidth = band % 4 === 0 ? 1.4 : 0.9;
    const yBase = h * (0.06 + band * 0.065);
    for (let x = -30; x <= w + 30; x += 12) {
      const y = yBase
        + Math.sin((x + mouse.x * 240) * 0.012 + band) * 18
        + Math.sin((x - mouse.y * 170) * 0.026 + band * 1.7) * 6
        + Math.sin((x + band * 31) * 0.061) * 2.5;
      if (x === -30) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  const cx = w * (0.5 + mouse.x * 0.08);
  const cy = h * (0.45 + mouse.y * 0.08);
  for (let r = 110; r < Math.max(w, h); r += 110) {
    ctx.beginPath();
    ctx.strokeStyle = `rgba(255,255,255,${0.105 - Math.min(0.075, r / 10000)})`;
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();
  }

  // Dot grid pre-rendered once on resize — single drawImage instead of ~448 fillRect calls
  ctx.drawImage(_dotCanvas, mouse.x * 10, mouse.y * 10);
  ctx.restore();
}

function drawLinks(ctx, particles, w, h, mouse) {
  ctx.save();
  // 60 particles = 1770 pair checks/frame vs 5995 at 110 — same visual result
  const sample = particles.slice(0, 60);
  for (let i = 0; i < sample.length; i++) {
    const a = sample[i];
    const ax = a.x * w + mouse.x * 14;
    const ay = a.y * h + mouse.y * 14;
    for (let j = i + 1; j < sample.length; j++) {
      const b = sample[j];
      const bx = b.x * w + mouse.x * -10;
      const by = b.y * h + mouse.y * -10;
      const d = Math.hypot(ax - bx, ay - by);
      if (d > 88) continue;
      ctx.beginPath();
      ctx.strokeStyle = `rgba(255,255,255,${0.10 * (1 - d / 88)})`;
      ctx.lineWidth = 1;
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.stroke();
    }
  }
  ctx.restore();
}
