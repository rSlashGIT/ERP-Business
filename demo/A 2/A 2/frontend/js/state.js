const KEY = 'smartstock_v2';

const DEFAULTS = {
  decisions: [],
  settings: {
    holdCost: 0.02,
    stockoutPenalty: 2.00,
    preferredModel: 'auto',
    dosFactor: 3,
  },
};

const _bus = {};

function _emit(event, data) {
  (_bus[event] || []).forEach(fn => fn(data));
}

function _load() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { decisions: [], settings: { ...DEFAULTS.settings } };
    const saved = JSON.parse(raw);
    return {
      decisions: Array.isArray(saved.decisions) ? saved.decisions : [],
      settings: { ...DEFAULTS.settings, ...(saved.settings || {}) },
    };
  } catch {
    return { decisions: [], settings: { ...DEFAULTS.settings } };
  }
}

function _save(state) {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch {}
}

let _state = _load();

export const AppState = {
  getSettings() { return { ..._state.settings }; },

  updateSettings(patch) {
    _state.settings = { ..._state.settings, ...patch };
    _save(_state);
    _emit('settings', _state.settings);
  },

  getDecisions() { return _state.decisions; },

  recordDecision(d) {
    _state.decisions = [d, ..._state.decisions].slice(0, 500);
    _save(_state);
    _emit('decisions', _state.decisions);
  },

  pendingCount(recommendations) {
    if (!recommendations) return 0;
    const decided = new Set(_state.decisions.map(d => d.sku_id));
    return recommendations.filter(r => !decided.has(r.sku_id)).length;
  },

  isDecided(skuId) {
    return _state.decisions.some(d => d.sku_id === skuId);
  },

  getDecision(skuId) {
    return _state.decisions.find(d => d.sku_id === skuId) || null;
  },

  on(event, fn) {
    if (!_bus[event]) _bus[event] = [];
    _bus[event].push(fn);
    return () => { _bus[event] = (_bus[event] || []).filter(f => f !== fn); };
  },

  reset() {
    _state = { decisions: [], settings: { ...DEFAULTS.settings } };
    _save(_state);
    _emit('reset', null);
    _emit('decisions', []);
    _emit('settings', _state.settings);
  },
};
