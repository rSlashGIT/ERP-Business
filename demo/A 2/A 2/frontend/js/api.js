const _cache = {};
const BASE = './data/';
const BACKEND_BASE = (
  window.SMARTSTOCK_API_BASE ||
  new URLSearchParams(window.location.search).get('api') ||
  ''
).replace(/\/$/, '');

async function _load(file) {
  if (_cache[file]) return _cache[file];
  const candidates = BACKEND_BASE
    ? [`${BACKEND_BASE}/${file}`, `${BACKEND_BASE}/${file.replace(/\.json$/, '')}`, BASE + file]
    : [BASE + file];

  let lastError = null;
  for (const url of candidates) {
    try {
      const r = await fetch(url);
      if (!r.ok) {
        lastError = new Error(`${url}: HTTP ${r.status}`);
        continue;
      }
      _cache[file] = await r.json();
      return _cache[file];
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError || new Error(`${file}: unavailable`);
}

export const API = {
  skus:            () => _load('skus.json'),
  inventory:       () => _load('inventory_today.json'),
  forecasts:       () => _load('forecasts.json'),
  recommendations: () => _load('recommendations.json'),
  explanations:    () => _load('explanations.json'),
  historySeed:     () => _load('history_seed.json'),
  alerts:          () => _load('alerts.json'),
  disruptions:     () => _load('disruptions.json'),
  comparison:      () => _load('comparison.json'),
  tolerance:       () => _load('tolerance_accuracy.json'),

  async all() {
    const [skus, inventory, forecasts, recommendations, explanations, historySeed, alerts] =
      await Promise.all([
        this.skus(), this.inventory(), this.forecasts(),
        this.recommendations(), this.explanations(),
        this.historySeed(), this.alerts(),
      ]);
    return { skus, inventory, forecasts, recommendations, explanations, historySeed, alerts };
  },

  // skus.json is an array: find by id
  async sku(id) {
    return (await this.skus()).find(s => s.id === id) || null;
  },

  // inventory_today.json is keyed by sku_id
  async inv(id) {
    const inv = await this.inventory();
    return inv[id] || null;
  },

  // forecasts.json is keyed by sku_id
  async forecast(id) {
    const fc = await this.forecasts();
    return fc[id] || null;
  },

  // explanations.json is keyed by sku_id
  async explanation(id) {
    const ex = await this.explanations();
    return ex[id] || null;
  },

  // recommendations.json is an array: find by sku_id
  async recommendation(id) {
    return (await this.recommendations()).find(r => r.sku_id === id) || null;
  },
};
