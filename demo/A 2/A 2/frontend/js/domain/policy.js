const DEFAULT_SETTINGS = {
  holdCost: 0.02,
  stockoutPenalty: 2.00,
  preferredModel: 'auto',
  dosFactor: 3,
};

export const MODEL_KEYS = ['lightgbm', 'nhits', 'chronos'];

export function normalizeSettings(settings = {}) {
  const merged = { ...DEFAULT_SETTINGS, ...(settings || {}) };
  return {
    holdCost: clamp(0.01, 1, Number(merged.holdCost) || DEFAULT_SETTINGS.holdCost),
    stockoutPenalty: clamp(0.5, 20, Number(merged.stockoutPenalty) || DEFAULT_SETTINGS.stockoutPenalty),
    preferredModel: ['auto', 'chronos', 'nhits', 'lightgbm', 'ensemble'].includes(merged.preferredModel)
      ? merged.preferredModel
      : DEFAULT_SETTINGS.preferredModel,
    dosFactor: clamp(1, 7, Number(merged.dosFactor) || DEFAULT_SETTINGS.dosFactor),
  };
}

export function selectedForecastSeries(forecast = {}, settings = {}) {
  const s = normalizeSettings(settings);
  if (s.preferredModel === 'ensemble') {
    const series = MODEL_KEYS.map(k => asNumberArray(forecast[k])).filter(v => v.length);
    const len = Math.max(0, ...series.map(v => v.length));
    return Array.from({ length: len }, (_, i) => {
      const vals = series.map(v => v[i]).filter(Number.isFinite);
      return vals.length ? round1(vals.reduce((sum, v) => sum + v, 0) / vals.length) : null;
    });
  }
  const key = s.preferredModel === 'auto' ? 'chronos' : s.preferredModel;
  return asNumberArray(forecast[key] || forecast.chronos || forecast.lightgbm || forecast.nhits);
}

export function selectedForecastLabel(settings = {}) {
  const model = normalizeSettings(settings).preferredModel;
  return {
    auto: 'Chronos forecast',
    chronos: 'Chronos forecast',
    nhits: 'N-HITS forecast',
    lightgbm: 'LightGBM forecast',
    ensemble: 'Ensemble forecast',
  }[model];
}

export function selectedForecastColor(settings = {}, C = {}) {
  const model = normalizeSettings(settings).preferredModel;
  return {
    auto: C.green,
    chronos: C.green,
    nhits: C.blue,
    lightgbm: C.amber,
    ensemble: C.purple,
  }[model] || C.green;
}

export function forecastDemand(forecast = {}, inventory = {}, settings = {}) {
  const selected = selectedForecastSeries(forecast, settings).filter(Number.isFinite);
  if (selected.length) return selected.reduce((sum, v) => sum + v, 0) / selected.length;
  return Number(inventory.avg_daily_demand || forecast.avg_daily_forecast || 0);
}

export function projectedInventorySeries(inventory = {}, recommendedQty = 0, forecast = {}, settings = {}) {
  const selected = selectedForecastSeries(forecast, settings);
  let stock = Number(inventory.total_stock || 0) + Number(recommendedQty || 0);
  return selected.map(demand => {
    stock = Math.max(0, stock - Math.max(0, Number(demand) || 0));
    return Math.round(stock);
  });
}

export function inventoryStatus(daysOfStock, settings = {}) {
  const threshold = normalizeSettings(settings).dosFactor;
  const days = Number(daysOfStock) || 0;
  if (days <= threshold) return 'critical';
  if (days <= threshold + 3) return 'warning';
  if (days <= Math.max(14, threshold + 10)) return 'healthy';
  return 'over';
}

export function adjustedCoverageDays(baseDays, settings = {}) {
  const s = normalizeSettings(settings);
  const baseRatio = DEFAULT_SETTINGS.stockoutPenalty / DEFAULT_SETTINGS.holdCost;
  const ratio = s.stockoutPenalty / s.holdCost;
  const pressure = Math.sqrt(ratio / baseRatio);
  return clamp(4, 18, Number(baseDays || 10) * pressure);
}

export function deriveRecommendation(rec = {}, sku = {}, inventory = {}, forecast = {}, settings = {}) {
  const s = normalizeSettings(settings);
  const demand = Math.max(0.1, forecastDemand(forecast, inventory, s));
  const currentStock = Number(inventory.total_stock ?? rec.current_stock ?? 0);
  const baseQty = preferredModelQty(rec, s);
  const baseCoverage = Number(rec.expected_coverage_days) || Math.max(4, (Number(inventory.max_stock) || demand * 10) / demand);
  const economicsChanged =
    Math.abs(s.holdCost - DEFAULT_SETTINGS.holdCost) > 0.0001 ||
    Math.abs(s.stockoutPenalty - DEFAULT_SETTINGS.stockoutPenalty) > 0.0001;
  const modelChanged = s.preferredModel !== DEFAULT_SETTINGS.preferredModel;

  let qty = Number(baseQty) || 0;
  if (economicsChanged || modelChanged) {
    const targetDays = adjustedCoverageDays(baseCoverage, s);
    const targetStock = demand * targetDays;
    const economicsQty = Math.max(0, targetStock - currentStock);
    const modelWeight = modelChanged && !economicsChanged ? 0.75 : 0.25;
    qty = roundOrder(economicsQty * (1 - modelWeight) + baseQty * modelWeight);
  }

  const unitCost = Number(sku.unit_cost) || costPerUnit(rec);
  const coverageDays = (currentStock + qty) / demand;
  const costPressure = (s.stockoutPenalty / s.holdCost) / (DEFAULT_SETTINGS.stockoutPenalty / DEFAULT_SETTINGS.holdCost);
  const confidence = clamp(0.35, 0.96, Number(rec.confidence || 0.62) + Math.log2(costPressure) * 0.025);
  const urgency = urgencyFromCoverage(coverageDays, s);

  return {
    ...rec,
    current_stock: currentStock,
    recommended_order_qty: qty,
    expected_coverage_days: coverageDays,
    estimated_cost: Math.round(qty * unitCost),
    confidence,
    urgency,
    selected_daily_demand: demand,
    selected_model_label: selectedForecastLabel(s),
  };
}

export function modelScoreMap(actions, recommendedQty, inventory = {}, forecast = {}, settings = {}) {
  const s = normalizeSettings(settings);
  const demand = Math.max(0.1, forecastDemand(forecast, inventory, s));
  const current = Number(inventory.total_stock || 0);
  return actions.map(action => {
    const projectedDays = (current + action) / demand;
    const targetDays = adjustedCoverageDays(10, s);
    const serviceFit = 1 - Math.min(1, Math.abs(projectedDays - targetDays) / Math.max(1, targetDays));
    const policyFit = 1 - Math.min(1, Math.abs(action - recommendedQty) / Math.max(50, recommendedQty || demand));
    return clamp(5, 100, 18 + serviceFit * 24 + policyFit * 58);
  });
}

function preferredModelQty(rec, settings) {
  const mq = rec?.model_quantities || {};
  if (settings.preferredModel === 'lightgbm') return Number(mq.lightgbm ?? rec.recommended_order_qty ?? 0);
  if (settings.preferredModel === 'nhits') return Number(mq.nhits ?? rec.recommended_order_qty ?? 0);
  if (settings.preferredModel === 'chronos') return Number(mq.chronos ?? rec.recommended_order_qty ?? 0);
  if (settings.preferredModel === 'ensemble') {
    const vals = MODEL_KEYS.map(k => Number(mq[k])).filter(Number.isFinite);
    return vals.length ? roundOrder(vals.reduce((sum, v) => sum + v, 0) / vals.length) : Number(rec.recommended_order_qty || 0);
  }
  return Number(rec.recommended_order_qty || mq.chronos || mq.lightgbm || mq.nhits || 0);
}

function asNumberArray(values) {
  return Array.isArray(values) ? values.map(Number).filter(Number.isFinite) : [];
}

function costPerUnit(rec) {
  const qty = Number(rec.recommended_order_qty);
  const cost = Number(rec.estimated_cost);
  return qty > 0 && Number.isFinite(cost) ? cost / qty : 0;
}

function roundOrder(qty) {
  if (!Number.isFinite(qty) || qty <= 0) return 0;
  const step = qty < 100 ? 10 : 50;
  return Math.ceil(qty / step) * step;
}

function urgencyFromCoverage(days, settings) {
  if (days <= settings.dosFactor) return 'critical';
  if (days <= settings.dosFactor + 2) return 'high';
  if (days <= settings.dosFactor + 5) return 'medium';
  return 'low';
}

function round1(value) {
  return Math.round(value * 10) / 10;
}

function clamp(min, max, value) {
  return Math.max(min, Math.min(max, value));
}
