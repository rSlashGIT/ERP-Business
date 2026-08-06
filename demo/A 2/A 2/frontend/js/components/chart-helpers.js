export const C = {
  green:  'hsl(160,75%,52%)',
  blue:   'hsl(210,75%,60%)',
  amber:  'hsl(40,85%,60%)',
  purple: 'hsl(280,65%,65%)',
  red:    'hsl(340,70%,60%)',
  greenA: 'hsla(160,75%,52%,0.15)',
  blueA:  'hsla(210,75%,60%,0.15)',
  amberA: 'hsla(40,85%,60%,0.15)',
};

const GRID = 'hsl(220,13%,16%)';

class FallbackChart {
  static _nextId = 1;
  static instances = {};
  static defaults = {
    color: 'hsl(220,10%,60%)',
    borderColor: 'hsl(220,13%,22%)',
    backgroundColor: 'transparent',
    font: { family: "'Inter', system-ui, sans-serif", size: 11 },
    plugins: {
      legend: { labels: { boxWidth: 10, usePointStyle: true } },
      tooltip: {
        backgroundColor: 'hsl(220,13%,13%)',
        borderColor: 'hsl(220,13%,22%)',
        borderWidth: 1,
        titleColor: 'hsl(220,10%,96%)',
        bodyColor: 'hsl(220,10%,70%)',
        padding: 10,
        cornerRadius: 6,
      },
    },
  };

  static getChart(canvas) {
    return Object.values(FallbackChart.instances).find(chart => chart.canvas === canvas) || null;
  }

  constructor(ctx, config) {
    this.ctx = ctx;
    this.canvas = ctx.canvas;
    this.config = config;
    this.data = config.data || { labels: [], datasets: [] };
    this.id = FallbackChart._nextId++;
    FallbackChart.instances[this.id] = this;
    this._render();
  }

  destroy() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    delete FallbackChart.instances[this.id];
  }

  _render() {
    const type = this.config.type;
    if (type === 'bar') return drawFallbackBar(this);
    if (type === 'doughnut') return drawFallbackDoughnut(this);
    return drawFallbackLine(this);
  }
}

if (typeof globalThis !== 'undefined' && typeof globalThis.Chart === 'undefined') {
  globalThis.Chart = FallbackChart;
}

function chartRuntime() {
  return (typeof globalThis !== 'undefined' && globalThis.Chart) ? globalThis.Chart : FallbackChart;
}

export function applyDefaults() {
  const d = chartRuntime().defaults;
  d.color = 'hsl(220,10%,60%)';
  d.borderColor = 'hsl(220,13%,22%)';
  d.font.family = "'Inter', system-ui, sans-serif";
  d.font.size = 11;
  d.responsive = true;
  d.maintainAspectRatio = false;
  // PERFORMANCE: disable animations globally to keep navigation snappy
  if (d.animation !== undefined) d.animation = false;
  if (d.animations) {
    Object.keys(d.animations).forEach(k => { d.animations[k] = false; });
  }
  d.transitions = d.transitions || {};
  d.transitions.active = { animation: { duration: 0 } };
  d.transitions.resize = { animation: { duration: 0 } };
  if (d.elements?.line) d.elements.line.borderCapStyle = 'round';
  if (d.elements?.point) d.elements.point.hoverRadius = 5;
  d.plugins.legend.labels.boxWidth = 10;
  d.plugins.legend.labels.usePointStyle = true;
  d.plugins.legend.labels.padding = 14;
  d.plugins.tooltip.backgroundColor = 'hsl(220,13%,13%)';
  d.plugins.tooltip.borderColor = 'hsl(220,13%,22%)';
  d.plugins.tooltip.borderWidth = 1;
  d.plugins.tooltip.titleColor = 'hsl(220,10%,96%)';
  d.plugins.tooltip.bodyColor = 'hsl(220,10%,70%)';
  d.plugins.tooltip.padding = 10;
  d.plugins.tooltip.cornerRadius = 6;
}

function _destroy(canvas) {
  const existing = chartRuntime().getChart(canvas);
  if (existing) existing.destroy();
}

function _scaleOpts() {
  return {
    grid: { color: GRID },
    ticks: { maxTicksLimit: 7, color: 'hsl(220,10%,50%)' },
    border: { color: GRID },
  };
}

function _fitDatasets(labels, datasets) {
  const targetLength = labels.length;
  return datasets.map(ds => {
    const data = Array.isArray(ds.data) ? ds.data.slice(0, targetLength) : [];
    while (data.length < targetLength) data.push(null);
    return { ...ds, data };
  });
}

export function lineChart(canvas, labels, datasets, extra = {}) {
  _destroy(canvas);
  const fitted = _fitDatasets(labels, datasets);
  canvas.dataset.chartReady = 'pending';
  canvas.dataset.chartLabels = String(labels.length);
  canvas.dataset.chartDatasets = String(fitted.length);
  const chart = new (chartRuntime())(canvas.getContext('2d'), {
    type: 'line',
    data: { labels, datasets: fitted },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      clip: 8,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { ..._scaleOpts(), ticks: { ..._scaleOpts().ticks, maxRotation: 0 } },
        y: { ..._scaleOpts(), beginAtZero: false },
      },
      elements: { line: { capBezierPoints: true }, point: { hitRadius: 10 } },
      plugins: { legend: { display: fitted.length > 1 } },
      animation: false,
      ...extra,
    },
  });
  canvas.dataset.chartReady = 'true';
  return chart;
}

export function barChart(canvas, labels, datasets, extra = {}) {
  _destroy(canvas);
  const fitted = _fitDatasets(labels, datasets);
  canvas.dataset.chartReady = 'pending';
  canvas.dataset.chartLabels = String(labels.length);
  canvas.dataset.chartDatasets = String(fitted.length);
  const chart = new (chartRuntime())(canvas.getContext('2d'), {
    type: 'bar',
    data: { labels, datasets: fitted },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      borderRadius: 5,
      scales: {
        x: { grid: { color: GRID }, ticks: { color: 'hsl(220,10%,50%)' } },
        y: { grid: { color: GRID }, ticks: { color: 'hsl(220,10%,50%)' }, beginAtZero: true },
      },
      plugins: { legend: { display: fitted.length > 1 } },
      animation: false,
      ...extra,
    },
  });
  canvas.dataset.chartReady = 'true';
  return chart;
}

export function doughnutChart(canvas, labels, data, colors, extra = {}) {
  _destroy(canvas);
  canvas.dataset.chartReady = 'pending';
  canvas.dataset.chartLabels = String(labels.length);
  canvas.dataset.chartDatasets = '1';
  const chart = new (chartRuntime())(canvas.getContext('2d'), {
    type: 'doughnut',
    data: { labels, datasets: [{ data, backgroundColor: colors, borderWidth: 0, hoverOffset: 4 }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '68%',
      plugins: { legend: { position: 'right', labels: { padding: 16, font: { size: 12 } } } },
      animation: false,
      ...extra,
    },
  });
  canvas.dataset.chartReady = 'true';
  return chart;
}

export function sparkline(canvas, data, color = C.green) {
  _destroy(canvas);
  canvas.dataset.chartReady = 'pending';
  canvas.dataset.chartLabels = String(data.length);
  canvas.dataset.chartDatasets = '1';
  const chart = new (chartRuntime())(canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels: data.map((_, i) => i),
      datasets: [{
        data,
        borderColor: color,
        borderWidth: 1.5,
        pointRadius: 0,
        fill: true,
        backgroundColor: color.includes('hsla') ? color : color.replace('hsl(', 'hsla(').replace(')', ',0.12)'),
        tension: 0.3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: { x: { display: false }, y: { display: false } },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      animation: false,
    },
  });
  canvas.dataset.chartReady = 'true';
  return chart;
}

function prepareCanvas(chart) {
  const canvas = chart.canvas;
  const ctx = chart.ctx;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.floor((rect.width || canvas.clientWidth || 300) * dpr));
  const height = Math.max(1, Math.floor((rect.height || canvas.clientHeight || 160) * dpr));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return { ctx, w: width / dpr, h: height / dpr };
}

function finiteValues(datasets) {
  return datasets.flatMap(ds => (ds.data || []).filter(v => Number.isFinite(Number(v))).map(Number));
}

function drawGrid(ctx, x, y, w, h) {
  ctx.strokeStyle = GRID;
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const yy = y + (h / 4) * i;
    ctx.beginPath();
    ctx.moveTo(x, yy);
    ctx.lineTo(x + w, yy);
    ctx.stroke();
  }
}

function drawLegend(ctx, datasets, x, y) {
  ctx.font = '11px Inter, system-ui, sans-serif';
  ctx.textBaseline = 'middle';
  let cursor = x;
  datasets.forEach(ds => {
    ctx.fillStyle = ds.borderColor || ds.backgroundColor || C.blue;
    ctx.fillRect(cursor, y - 4, 10, 8);
    ctx.fillStyle = 'hsl(220,10%,60%)';
    ctx.fillText(ds.label || '', cursor + 14, y);
    cursor += ctx.measureText(ds.label || '').width + 42;
  });
}

function drawFallbackLine(chart) {
  const { ctx, w, h } = prepareCanvas(chart);
  const labels = chart.data.labels || [];
  const datasets = chart.data.datasets || [];
  const pad = { l: 34, r: 14, t: datasets.length > 1 ? 28 : 12, b: 24 };
  const plotW = Math.max(1, w - pad.l - pad.r);
  const plotH = Math.max(1, h - pad.t - pad.b);
  const values = finiteValues(datasets);
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 1);
  const span = Math.max(1, max - min);
  drawGrid(ctx, pad.l, pad.t, plotW, plotH);
  if (datasets.length > 1) drawLegend(ctx, datasets, pad.l, 12);

  datasets.forEach(ds => {
    ctx.strokeStyle = ds.borderColor || C.blue;
    ctx.lineWidth = ds.borderWidth || 1.5;
    if (ds.borderDash) ctx.setLineDash(ds.borderDash);
    else ctx.setLineDash([]);
    ctx.beginPath();
    let started = false;
    (ds.data || []).forEach((raw, i) => {
      if (!Number.isFinite(Number(raw))) {
        started = false;
        return;
      }
      const x = pad.l + (labels.length <= 1 ? 0 : (i / (labels.length - 1)) * plotW);
      const y = pad.t + plotH - ((Number(raw) - min) / span) * plotH;
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
  });
  ctx.setLineDash([]);
}

function drawFallbackBar(chart) {
  const { ctx, w, h } = prepareCanvas(chart);
  const labels = chart.data.labels || [];
  const dataset = (chart.data.datasets || [])[0] || { data: [] };
  const data = (dataset.data || []).map(v => Number(v) || 0);
  const colors = Array.isArray(dataset.backgroundColor) ? dataset.backgroundColor : data.map(() => dataset.backgroundColor || C.blue);
  const pad = { l: 34, r: 14, t: 14, b: 42 };
  const plotW = Math.max(1, w - pad.l - pad.r);
  const plotH = Math.max(1, h - pad.t - pad.b);
  const minValue = Math.min(0, ...data);
  const maxValue = Math.max(1, ...data);
  const span = Math.max(1, maxValue - minValue);
  drawGrid(ctx, pad.l, pad.t, plotW, plotH);
  const slot = plotW / Math.max(1, data.length);
  const barW = Math.max(6, Math.min(42, slot * 0.6));
  data.forEach((value, i) => {
    const x = pad.l + i * slot + (slot - barW) / 2;
    const zeroY = pad.t + plotH - ((0 - minValue) / span) * plotH;
    const y = pad.t + plotH - ((value - minValue) / span) * plotH;
    ctx.fillStyle = colors[i] || C.blue;
    ctx.fillRect(x, Math.min(y, zeroY), barW, Math.max(2, Math.abs(zeroY - y)));
  });
  ctx.fillStyle = 'hsl(220,10%,50%)';
  ctx.font = '10px Inter, system-ui, sans-serif';
  ctx.textAlign = 'center';
  labels.forEach((label, i) => {
    const x = pad.l + i * slot + slot / 2;
    ctx.fillText(String(label).slice(0, 14), x, h - 18);
  });
}

function drawFallbackDoughnut(chart) {
  const { ctx, w, h } = prepareCanvas(chart);
  const dataset = (chart.data.datasets || [])[0] || { data: [] };
  const data = (dataset.data || []).map(v => Math.max(0, Number(v) || 0));
  const labels = chart.data.labels || [];
  const colors = dataset.backgroundColor || [C.green, C.amber, C.red];
  const total = data.reduce((sum, v) => sum + v, 0) || 1;
  const cx = Math.min(w * 0.38, w / 2);
  const cy = h / 2;
  const radius = Math.max(24, Math.min(w, h) * 0.32);
  let angle = -Math.PI / 2;
  data.forEach((value, i) => {
    const next = angle + (value / total) * Math.PI * 2;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, radius, angle, next);
    ctx.closePath();
    ctx.fillStyle = colors[i] || C.blue;
    ctx.fill();
    angle = next;
  });
  ctx.globalCompositeOperation = 'destination-out';
  ctx.beginPath();
  ctx.arc(cx, cy, radius * 0.68, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalCompositeOperation = 'source-over';
  ctx.font = '11px Inter, system-ui, sans-serif';
  ctx.textBaseline = 'middle';
  labels.forEach((label, i) => {
    const y = 28 + i * 22;
    ctx.fillStyle = colors[i] || C.blue;
    ctx.fillRect(w * 0.62, y - 5, 10, 10);
    ctx.fillStyle = 'hsl(220,10%,60%)';
    ctx.fillText(`${label}: ${data[i] || 0}`, w * 0.62 + 16, y);
  });
}
