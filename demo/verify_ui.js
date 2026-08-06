/*
 * Render every screen and every modal, then read what came out.
 *
 *     node demo/verify_ui.js [port]          (or: make verify-ui)
 *
 * WHY
 * ---
 * The Price Advisor shipped with its modal arguments in the wrong order:
 * openM(title, subtitle, body, footer) was called as openM(title, body), so a
 * screenful of raw HTML rendered as literal text in the subtitle and the body
 * read "undefined". Every API test passed. Every Python check passed. The bug
 * was only visible to a human looking at the screen.
 *
 * So this drives the REAL functions out of erp.html against the REAL server,
 * through a DOM small enough to write by hand (there is no npm here), and then
 * greps the output for the fingerprints of that class of bug:
 *
 *     undefined / NaN / [object Object]      a value that never arrived
 *     raw markup inside a text-only slot      arguments in the wrong order
 *     empty required region                   a render that silently did nothing
 */
const fs = require('fs');
const path = require('path');
const PORT = process.argv[2] || '8500';
const BASE = `http://127.0.0.1:${PORT}`;

let pass = 0, fail = 0;
const failures = [];
function check(ok, label, detail = '') {
  if (ok) { pass++; console.log(`  PASS  ${label}${detail ? '   ' + detail : ''}`); }
  else { fail++; failures.push(`${label} ${detail}`); console.log(`  FAIL  ${label}   ${detail}`); }
}

/* ── the smallest DOM that can run this app ── */
class El {
  constructor(id = '') {
    this.id = id; this._html = ''; this._text = '';
    this.style = {}; this.dataset = {}; this.className = ''; this.value = '';
    this.children = []; this.classList = {
      _s: new Set(),
      add: (c) => this.classList._s.add(c),
      remove: (c) => this.classList._s.delete(c),
      toggle: (c, on) => on ? this.classList._s.add(c) : this.classList._s.delete(c),
      contains: (c) => this.classList._s.has(c),
    };
  }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  set textContent(v) { this._text = String(v); }
  get textContent() { return this._text; }
  appendChild(c) { this.children.push(c); return c; }
  remove() {}
  querySelectorAll() { return []; }
  addEventListener() {}
  click() {}
  focus() {}
}

const els = new Map();
function el(id) { if (!els.has(id)) els.set(id, new El(id)); return els.get(id); }

// Selected-for-labels product ids. `printLabels()` reads ticked `.lblPick`
// checkboxes from the document, so the shim serves them from here.
let PICKED = [];
global.document = {
  getElementById: el,
  querySelectorAll: (sel) => (String(sel).includes('lblPick')
    ? PICKED.map((v) => ({ checked: true, value: v }))
    : []),
  querySelector: () => new El(),
  createElement: () => new El(),
  addEventListener: () => {},
  body: new El('body'),
};
global.window = global;
global.localStorage = {
  _m: {}, getItem(k) { return this._m[k] ?? null; },
  setItem(k, v) { this._m[k] = String(v); }, removeItem(k) { delete this._m[k]; },
};
global.location = { origin: BASE, href: BASE + '/' };
global.alert = () => {};
global.print = () => {};
global.setTimeout = (f) => { try { f(); } catch (e) {} return 0; };
global.confirm = () => true;

/* fetch: real HTTP to the real server, carrying the tenant header */
let TENANT_HDR = 'kurta-house';
global.fetch = async (url, opts = {}) => {
  const u = url.startsWith('http') ? url : BASE + url;
  const headers = Object.assign({ 'X-Tenant': TENANT_HDR }, opts.headers || {});
  const res = await nodeFetch(u, { method: opts.method || 'GET', headers, body: opts.body });
  return res;
};

function nodeFetch(url, { method = 'GET', headers = {}, body } = {}) {
  const http = require('http');
  const u = new URL(url);
  // Without Content-Length, Node sends the body chunked and http.server reads
  // nothing — the import then failed with "Need a header row", blaming the
  // sheet for a transport bug. Browsers always set it; this must too.
  if (body) headers = Object.assign({}, headers,
    { 'Content-Length': Buffer.byteLength(body) });
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: u.hostname, port: u.port, path: u.pathname + u.search, method, headers,
    }, (res) => {
      let data = '';
      res.on('data', (c) => data += c);
      res.on('end', () => resolve({
        ok: res.statusCode < 400, status: res.statusCode, statusText: String(res.statusCode),
        json: async () => JSON.parse(data || '{}'),
        text: async () => data,
      }));
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

/* ── load the app's own script out of erp.html ── */
const html = fs.readFileSync(path.join(__dirname, '..', 'apps', 'console', 'erp.html'), 'utf8');
const js = html.slice(html.lastIndexOf('<script>') + 8, html.lastIndexOf('</script>'));
// boot() runs on load and would race the checks; the harness calls it itself.
const src = js.replace(/\nboot\(\);\s*$/, '\n');
// Indirect eval so the app's declarations land in GLOBAL scope. A direct
// eval() gets its own scope under strict mode and nothing leaks out, which
// looks exactly like "boot is not defined".
// `const RENDER` / `let IMP` are lexical declarations: even under indirect
// eval they land in the global LEXICAL environment, which module scope cannot
// see. Copy the ones the harness drives onto globalThis explicitly.
(0, eval)(src + `
;Object.assign(globalThis, {RENDER, IMP, SAMPLE, skeleton, emptyState, errorCard, trendChart, fmt});`);

/* ── the fingerprints of a broken render ── */
const BAD = [
  [/\bundefined\b/, 'contains "undefined"'],
  [/\bNaN\b/, 'contains "NaN"'],
  [/\[object Object\]/, 'contains "[object Object]"'],
  [/>\s*null\s*</, 'rendered a null value'],
  [/₹\s*null|null\s*(?:pcs|%)/, 'rendered a null value'],
  [/Infinity/, 'contains "Infinity"'],
];

function scan(label, markup, { minLength = 60 } = {}) {
  const s = String(markup || '');
  if (s.length < minLength) { check(false, label, `rendered only ${s.length} chars`); return; }
  for (const [re, why] of BAD) {
    if (re.test(s)) {
      const i = s.search(re);
      check(false, label, `${why}: …${s.slice(Math.max(0, i - 55), i + 45).replace(/\s+/g, ' ')}…`);
      return;
    }
  }
  check(true, label, `${(s.length / 1024).toFixed(1)} KB`);
}

/* A text-only slot that has been handed markup is the exact bug that shipped. */
function scanTextSlot(label, text) {
  const s = String(text || '');
  if (/<\/?[a-z][\s\S]*>/i.test(s)) {
    check(false, label, `raw markup in a text-only slot: "${s.slice(0, 70)}…"`);
  } else if (/undefined|NaN|\[object/.test(s)) {
    check(false, label, `bad value: "${s.slice(0, 70)}"`);
  } else {
    check(true, label, s ? `"${s.slice(0, 52)}"` : '(empty, allowed)');
  }
}

(async () => {
  console.log(`\ndriving the real UI against ${BASE}\n`);

  for (const tenant of ['kurta-house', 'denim-depot']) {
    TENANT_HDR = tenant;
    console.log(`\n=== ${tenant} ===`);
    await switchTenant(tenant);   // the real path a user takes

    const screens = ['dashboard', 'billing', 'invoices', 'customers', 'receivables',
                     'inventory', 'styles', 'purchase', 'prices', 'reports', 'import',
                     'receiving', 'returns', 'payables', 'stocktakes', 'transfers', 'tax'];
    for (const s of screens) {
      try {
        await (RENDER[s] || (() => {}))();
        scan(`${s} renders`, el('p-' + s).innerHTML);
      } catch (e) {
        check(false, `${s} renders`, `threw: ${e.message}`);
      }
    }

    // ── modals: the part with no API test behind it ──
    try {
      const inv = (await (await fetch('/api/v1/sales/invoices')).json()).items[0];
      if (inv) {
        await showInvoice(inv.id);
        scanTextSlot('invoice modal subtitle', el('ms').textContent);
        scan('invoice modal body', el('mb').innerHTML);
      }
    } catch (e) { check(false, 'invoice modal', `threw: ${e.message}`); }

    try {
      const p = await (await fetch('/api/v1/prices')).json();
      for (const d of p.items.slice(0, 3)) {
        await priceDetail(d.style_id);
        scanTextSlot(`price modal subtitle · ${d.style_name}`, el('ms').textContent);
        scan(`price modal body · ${d.style_name}`, el('mb').innerHTML, { minLength: 500 });
        scan(`price modal footer · ${d.style_name}`, el('mf').innerHTML, { minLength: 40 });
      }
    } catch (e) { check(false, 'price modal', `threw: ${e.message}`); }

    // ── receiving: the PO modal and the SVG chart ──
    try {
      const open = (await (await fetch('/api/v1/receiving/open-pos')).json()).items
                     .filter(p => !p.fully_received);
      check(open.length > 0, 'a purchase order is awaiting delivery', `${open.length} open`);
      if (open.length) {
        await openReceive(open[0].id);
        scanTextSlot('receive modal subtitle', el('ms').textContent);
        scan('receive modal body', el('mb').innerHTML, { minLength: 400 });
        scan('receive modal footer', el('mf').innerHTML, { minLength: 40 });
      }
    } catch (e) { check(false, 'receive modal', `threw: ${e.message}`); }

    // ── returns: the credit-note modal off a real invoice ──
    try {
      const invs = (await (await fetch('/api/v1/sales/invoices?status=posted')).json()).items;
      let opened = false;
      for (const inv of invs.slice(0, 6)) {
        const r = await fetch('/api/v1/returns/returnable/' + inv.id);
        if (!r.ok) continue;
        const d = await r.json();
        if (!d.any_returnable) continue;
        await openReturn(inv.id);
        scanTextSlot('return modal subtitle', el('ms').textContent);
        scan('return modal body', el('mb').innerHTML, { minLength: 400 });
        scan('return modal footer', el('mf').innerHTML, { minLength: 40 });
        opened = true;
        break;
      }
      check(opened, 'a posted invoice can be returned against');
    } catch (e) { check(false, 'return modal', `threw: ${e.message}`); }

    // ── the dashboard chart must be a real chart, not an empty box ──
    try {
      const dash = el('p-dashboard').innerHTML;
      check(/<svg class="chart"/.test(dash), 'dashboard renders an SVG chart');
      const bars = (dash.match(/class="bar-r"/g) || []).length;
      check(bars > 0, 'chart has bars', `${bars} days plotted`);
      check(/<title>/.test(dash), 'chart bars carry a readable value on hover');
    } catch (e) { check(false, 'dashboard chart', `threw: ${e.message}`); }

    // ── empty and error states have to exist, not just happen to be unused ──
    try {
      check(typeof skeleton === 'function' && skeleton(2).includes('class="sk"'),
            'loading skeleton renders');
      check(typeof emptyState === 'function' && emptyState('t', 'b').includes('empty'),
            'empty state renders');
      check(typeof errorCard === 'function' && errorCard('t', 'd', 'x()').includes('Try again'),
            'error state offers a retry');
    } catch (e) { check(false, 'ui states', `threw: ${e.message}`); }

    // ── tax screen + label modal ──
    try {
      const dash = el('p-tax').innerHTML;
      check(/GSTR-1/.test(dash) && /GSTR-3B/.test(dash),
            'tax screen shows both returns');
      check(/working papers, not a filing/i.test(dash),
            'tax screen states plainly it is not a filing');
    } catch (e) { check(false, 'tax screen content', `threw: ${e.message}`); }

    try {
      await renderInventory();
      const invHtml = el('p-inventory').innerHTML;
      check(/class="lblPick"/.test(invHtml), 'inventory rows are selectable for labels');
      check(/id="lblBtn"/.test(invHtml), 'inventory has a Print labels button');
      // Drive the modal the way the button does, with a real product id.
      const someInv = (await (await fetch('/api/v1/inventory')).json()).items;
      // Drive the REAL printLabels(). It reads ticked checkboxes out of the
      // DOM, so the shim is taught to return two — rather than adding a
      // test-only hook to production code, which would mean the thing under
      // test is not the thing that ships.
      PICKED = someInv.slice(0, 2).map((r) => r.product_id);
      printLabels();
      scanTextSlot('label modal subtitle', el('ms').textContent);
      scan('label modal body', el('mb').innerHTML, { minLength: 200 });
      scan('label modal footer', el('mf').innerHTML, { minLength: 40 });
      check(/l7159|l7160|l7651/.test(el('mb').innerHTML),
            'label modal offers Avery sheet sizes');
      PICKED = [];
    } catch (e) { check(false, 'label modal', `threw: ${e.message}`); }

    // ── stocktake + transfer modals ──
    try {
      await openStocktake();
      scanTextSlot('stocktake modal subtitle', el('ms').textContent);
      scan('stocktake modal body', el('mb').innerHTML, { minLength: 400 });
      scan('stocktake modal footer', el('mf').innerHTML, { minLength: 40 });
    } catch (e) { check(false, 'stocktake modal', `threw: ${e.message}`); }

    try {
      await openTransfer();
      scanTextSlot('transfer modal subtitle', el('ms').textContent);
      scan('transfer modal body', el('mb').innerHTML, { minLength: 400 });
      scan('transfer modal footer', el('mf').innerHTML, { minLength: 40 });
      check(/id="trFrom"/.test(el('mb').innerHTML) && /id="trTo"/.test(el('mb').innerHTML),
            'transfer modal offers a source and a destination');
    } catch (e) { check(false, 'transfer modal', `threw: ${e.message}`); }

    try {
      newCustomer();
      scan('new-customer modal', el('mb').innerHTML);
    } catch (e) { check(false, 'new-customer modal', `threw: ${e.message}`); }

    try {
      const rec = await (await fetch('/api/v1/sales/receivables')).json();
      if (rec.by_customer && rec.by_customer.length) {
        await collect(rec.by_customer[0].customer_name, rec.by_customer[0].total);
        scan('collect-payment modal', el('mb').innerHTML);
      }
    } catch (e) { check(false, 'collect-payment modal', `threw: ${e.message}`); }

    // Import preview drives the analyse endpoint through the real render path.
    try {
      el('impText').value = SAMPLE;
      await impAnalyse();
      scan('import preview', el('impOut').innerHTML, { minLength: 400 });
    } catch (e) { check(false, 'import preview', `threw: ${e.message}`); }

    // Reports: each tab separately, they are the easiest thing to leave empty.
    for (const kind of ['sales_by_style', 'size_curve', 'gst_summary', 'dead_stock']) {
      try {
        const rows = (await (await fetch('/api/v1/reports?kind=' + kind)).json()).rows;
        check(Array.isArray(rows), `report ${kind} returns rows`, `${(rows||[]).length} rows`);
      } catch (e) { check(false, `report ${kind}`, `threw: ${e.message}`); }
    }
  }

  console.log('\n' + '='.repeat(70));
  console.log(`${pass}/${pass + fail} UI checks passed`);
  if (fail) {
    console.log('\nFAILURES:');
    failures.forEach((f) => console.log('  ' + f));
    console.log('\nUI IS BROKEN');
    process.exit(1);
  }
  console.log('EVERY SCREEN AND MODAL RENDERS CLEAN');
})().catch((e) => { console.error('harness error:', e); process.exit(1); });
