(function () {
  if (window.Chart) return;

  const GRID = 'hsl(220,13%,16%)';
  const COLORS = ['hsl(160,75%,52%)', 'hsl(210,75%,60%)', 'hsl(40,85%,60%)', 'hsl(340,70%,60%)'];

  class SimpleChart {
    static instances = {};
    static _nextId = 1;
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
      return Object.values(SimpleChart.instances).find(chart => chart.canvas === canvas) || null;
    }

    constructor(ctx, config) {
      this.ctx = ctx;
      this.canvas = ctx.canvas;
      this.config = config;
      this.data = config.data || { labels: [], datasets: [] };
      this.id = SimpleChart._nextId++;
      SimpleChart.instances[this.id] = this;
      this.render();
    }

    destroy() {
      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      delete SimpleChart.instances[this.id];
    }

    render() {
      const type = this.config.type;
      if (type === 'bar') return drawBar(this);
      if (type === 'doughnut') return drawDoughnut(this);
      return drawLine(this);
    }
  }

  function setup(chart) {
    const canvas = chart.canvas;
    const ctx = chart.ctx;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor((rect.width || 300) * dpr));
    canvas.height = Math.max(1, Math.floor((rect.height || 160) * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    return { ctx, w: canvas.width / dpr, h: canvas.height / dpr };
  }

  function values(datasets) {
    return datasets.flatMap(ds => (ds.data || []).filter(v => Number.isFinite(Number(v))).map(Number));
  }

  function grid(ctx, x, y, w, h) {
    ctx.strokeStyle = GRID;
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const yy = y + h * i / 4;
      ctx.beginPath();
      ctx.moveTo(x, yy);
      ctx.lineTo(x + w, yy);
      ctx.stroke();
    }
  }

  function legend(ctx, datasets, x, y) {
    ctx.font = '11px Inter, system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    let cursor = x;
    datasets.forEach((ds, i) => {
      ctx.fillStyle = ds.borderColor || ds.backgroundColor || COLORS[i % COLORS.length];
      ctx.fillRect(cursor, y - 4, 10, 8);
      ctx.fillStyle = 'hsl(220,10%,60%)';
      ctx.fillText(ds.label || '', cursor + 14, y);
      cursor += ctx.measureText(ds.label || '').width + 42;
    });
  }

  function drawLine(chart) {
    const { ctx, w, h } = setup(chart);
    const labels = chart.data.labels || [];
    const datasets = chart.data.datasets || [];
    const pad = { l: 34, r: 14, t: datasets.length > 1 ? 28 : 12, b: 24 };
    const plotW = Math.max(1, w - pad.l - pad.r);
    const plotH = Math.max(1, h - pad.t - pad.b);
    const nums = values(datasets);
    const min = Math.min(0, ...nums);
    const max = Math.max(1, ...nums);
    const span = Math.max(1, max - min);
    grid(ctx, pad.l, pad.t, plotW, plotH);
    if (datasets.length > 1) legend(ctx, datasets, pad.l, 12);
    datasets.forEach((ds, di) => {
      ctx.strokeStyle = ds.borderColor || COLORS[di % COLORS.length];
      ctx.lineWidth = ds.borderWidth || 1.5;
      ctx.setLineDash(ds.borderDash || []);
      ctx.beginPath();
      let active = false;
      (ds.data || []).forEach((raw, i) => {
        if (!Number.isFinite(Number(raw))) {
          active = false;
          return;
        }
        const x = pad.l + (labels.length <= 1 ? 0 : i / (labels.length - 1) * plotW);
        const y = pad.t + plotH - (Number(raw) - min) / span * plotH;
        if (!active) {
          ctx.moveTo(x, y);
          active = true;
        } else {
          ctx.lineTo(x, y);
        }
      });
      ctx.stroke();
    });
    ctx.setLineDash([]);
  }

  function drawBar(chart) {
    const { ctx, w, h } = setup(chart);
    const labels = chart.data.labels || [];
    const ds = (chart.data.datasets || [])[0] || { data: [] };
    const data = (ds.data || []).map(v => Number(v) || 0);
    const colors = Array.isArray(ds.backgroundColor) ? ds.backgroundColor : data.map((_, i) => ds.backgroundColor || COLORS[i % COLORS.length]);
    const pad = { l: 34, r: 14, t: 14, b: 42 };
    const plotW = Math.max(1, w - pad.l - pad.r);
    const plotH = Math.max(1, h - pad.t - pad.b);
    const min = Math.min(0, ...data);
    const max = Math.max(1, ...data);
    const span = Math.max(1, max - min);
    grid(ctx, pad.l, pad.t, plotW, plotH);
    const slot = plotW / Math.max(1, data.length);
    const barW = Math.max(6, Math.min(42, slot * 0.6));
    data.forEach((value, i) => {
      const x = pad.l + i * slot + (slot - barW) / 2;
      const zeroY = pad.t + plotH - (0 - min) / span * plotH;
      const y = pad.t + plotH - (value - min) / span * plotH;
      ctx.fillStyle = colors[i];
      ctx.fillRect(x, Math.min(y, zeroY), barW, Math.max(2, Math.abs(zeroY - y)));
    });
    ctx.fillStyle = 'hsl(220,10%,50%)';
    ctx.font = '10px Inter, system-ui, sans-serif';
    ctx.textAlign = 'center';
    labels.forEach((label, i) => ctx.fillText(String(label).slice(0, 14), pad.l + i * slot + slot / 2, h - 18));
  }

  function drawDoughnut(chart) {
    const { ctx, w, h } = setup(chart);
    const ds = (chart.data.datasets || [])[0] || { data: [] };
    const data = (ds.data || []).map(v => Math.max(0, Number(v) || 0));
    const labels = chart.data.labels || [];
    const colors = ds.backgroundColor || COLORS;
    const total = data.reduce((sum, v) => sum + v, 0) || 1;
    const cx = Math.min(w * 0.38, w / 2);
    const cy = h / 2;
    const r = Math.max(24, Math.min(w, h) * 0.32);
    let a = -Math.PI / 2;
    data.forEach((value, i) => {
      const next = a + value / total * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, r, a, next);
      ctx.closePath();
      ctx.fillStyle = colors[i] || COLORS[i % COLORS.length];
      ctx.fill();
      a = next;
    });
    ctx.globalCompositeOperation = 'destination-out';
    ctx.beginPath();
    ctx.arc(cx, cy, r * 0.68, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalCompositeOperation = 'source-over';
    ctx.font = '11px Inter, system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    labels.forEach((label, i) => {
      const y = 28 + i * 22;
      ctx.fillStyle = colors[i] || COLORS[i % COLORS.length];
      ctx.fillRect(w * 0.62, y - 5, 10, 10);
      ctx.fillStyle = 'hsl(220,10%,60%)';
      ctx.fillText(`${label}: ${data[i] || 0}`, w * 0.62 + 16, y);
    });
  }

  window.Chart = SimpleChart;
})();
