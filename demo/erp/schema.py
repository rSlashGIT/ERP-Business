"""SQLite schema for the demo server. Mirrors services/erp-api/app/db/models.py.

The production path is PostgreSQL via SQLAlchemy; this exists because neither
is installable in the dev sandbox (see AGENTS.md). Column names and semantics
match the models exactly so the demo cannot drift from production meaning.
Every tenant-scoped table carries tenant_id, and natural keys are unique
per-tenant, matching the rules in AGENTS.md section 5.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, legal_name TEXT, gstin TEXT,
  state_code TEXT NOT NULL, address TEXT, phone TEXT, email TEXT,
  invoice_prefix TEXT DEFAULT 'INV', currency TEXT DEFAULT 'INR');

CREATE TABLE IF NOT EXISTS locations(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, code TEXT NOT NULL,
  name TEXT NOT NULL, type TEXT DEFAULT 'store', is_active INTEGER DEFAULT 1,
  UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS product_styles(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, style_code TEXT NOT NULL,
  name TEXT NOT NULL, brand TEXT, category TEXT, season TEXT, hsn_code TEXT,
  is_active INTEGER DEFAULT 1, UNIQUE(tenant_id, style_code));

CREATE TABLE IF NOT EXISTS products(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, sku TEXT NOT NULL, name TEXT NOT NULL,
  style_id TEXT, size TEXT, size_seq INTEGER, colour TEXT, barcode TEXT,
  hsn_code TEXT, unit_cost REAL DEFAULT 0, unit_price REAL DEFAULT 0,
  is_active INTEGER DEFAULT 1,
  UNIQUE(tenant_id, sku), UNIQUE(tenant_id, barcode),
  UNIQUE(style_id, size, colour));

CREATE TABLE IF NOT EXISTS inventory_levels(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, product_id TEXT NOT NULL,
  location_id TEXT NOT NULL, on_hand REAL DEFAULT 0, on_order REAL DEFAULT 0,
  reserved REAL DEFAULT 0, backorder REAL DEFAULT 0,
  reorder_point REAL, order_up_to REAL, safety_stock REAL,
  UNIQUE(product_id, location_id));

CREATE TABLE IF NOT EXISTS stock_movements(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
  product_id TEXT NOT NULL, location_id TEXT NOT NULL, movement_type TEXT NOT NULL,
  quantity REAL NOT NULL, occurred_at TEXT NOT NULL,
  reference_type TEXT, reference_id TEXT, idempotency_key TEXT,
  UNIQUE(tenant_id, idempotency_key));

CREATE TABLE IF NOT EXISTS customers(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
  phone TEXT, email TEXT, gstin TEXT, state_code TEXT, address TEXT,
  credit_limit REAL DEFAULT 0, credit_days INTEGER DEFAULT 0,
  is_walkin INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1,
  UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS suppliers(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
  contract_lead_days REAL DEFAULT 7, UNIQUE(tenant_id, code));

CREATE TABLE IF NOT EXISTS sales_invoices(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, invoice_number TEXT NOT NULL,
  customer_id TEXT NOT NULL, location_id TEXT NOT NULL,
  invoice_date TEXT NOT NULL, due_date TEXT, status TEXT DEFAULT 'posted',
  place_of_supply TEXT, is_interstate INTEGER DEFAULT 0,
  subtotal REAL DEFAULT 0, discount_total REAL DEFAULT 0, taxable_total REAL DEFAULT 0,
  cgst_total REAL DEFAULT 0, sgst_total REAL DEFAULT 0, igst_total REAL DEFAULT 0,
  round_off REAL DEFAULT 0, grand_total REAL DEFAULT 0, amount_paid REAL DEFAULT 0,
  notes TEXT, created_by TEXT, created_at TEXT,
  UNIQUE(tenant_id, invoice_number));

CREATE TABLE IF NOT EXISTS sales_invoice_lines(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, invoice_id TEXT NOT NULL,
  product_id TEXT NOT NULL, line_no INTEGER NOT NULL,
  quantity REAL NOT NULL, unit_price REAL NOT NULL, discount_pct REAL DEFAULT 0,
  taxable_value REAL DEFAULT 0, gst_rate REAL DEFAULT 0,
  cgst REAL DEFAULT 0, sgst REAL DEFAULT 0, igst REAL DEFAULT 0,
  line_total REAL DEFAULT 0, hsn_code TEXT,
  UNIQUE(invoice_id, line_no));

CREATE TABLE IF NOT EXISTS payments(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, payment_number TEXT NOT NULL,
  customer_id TEXT NOT NULL, payment_date TEXT NOT NULL, amount REAL NOT NULL,
  method TEXT DEFAULT 'cash', reference TEXT, received_by TEXT,
  UNIQUE(tenant_id, payment_number));

CREATE TABLE IF NOT EXISTS payment_allocations(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, payment_id TEXT NOT NULL,
  invoice_id TEXT NOT NULL, amount REAL NOT NULL,
  UNIQUE(payment_id, invoice_id));

CREATE TABLE IF NOT EXISTS purchase_orders(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, po_number TEXT NOT NULL,
  supplier_id TEXT, location_id TEXT, status TEXT DEFAULT 'approved',
  total_value REAL DEFAULT 0, approved_by TEXT, created_at TEXT,
  UNIQUE(tenant_id, po_number));

CREATE TABLE IF NOT EXISTS purchase_order_lines(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, purchase_order_id TEXT NOT NULL,
  product_id TEXT NOT NULL, line_no INTEGER, ai_recommended_qty REAL,
  ordered_qty REAL NOT NULL, unit_cost REAL, line_value REAL,
  received_qty REAL DEFAULT 0);

-- ── goods receiving ──
-- A GRN is the moment stock and money become real: the shop physically has the
-- garments, owes the supplier, and its cost basis changes. Kept as its own
-- document rather than a flag on the PO because partial deliveries are the
-- norm, not the exception — a supplier sends 40 of 60 and the rest next week.
CREATE TABLE IF NOT EXISTS goods_receipts(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, grn_number TEXT NOT NULL,
  purchase_order_id TEXT, supplier_id TEXT, location_id TEXT NOT NULL,
  received_date TEXT NOT NULL, supplier_invoice TEXT, notes TEXT,
  total_value REAL DEFAULT 0, received_by TEXT, created_at TEXT,
  UNIQUE(tenant_id, grn_number));
CREATE TABLE IF NOT EXISTS goods_receipt_lines(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, goods_receipt_id TEXT NOT NULL,
  purchase_order_line_id TEXT, product_id TEXT NOT NULL, line_no INTEGER NOT NULL,
  accepted_qty REAL NOT NULL DEFAULT 0, rejected_qty REAL NOT NULL DEFAULT 0,
  unit_cost REAL NOT NULL DEFAULT 0, line_value REAL DEFAULT 0, reject_reason TEXT,
  UNIQUE(goods_receipt_id, line_no));
CREATE INDEX IF NOT EXISTS ix_grn_tenant ON goods_receipts(tenant_id, received_date);

-- ── supplier bills / payables ──
CREATE TABLE IF NOT EXISTS supplier_bills(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, bill_number TEXT NOT NULL,
  supplier_id TEXT NOT NULL, location_id TEXT NOT NULL,
  bill_date TEXT NOT NULL, due_date TEXT, status TEXT DEFAULT 'posted',
  goods_receipt_id TEXT, supplier_invoice_number TEXT,
  subtotal REAL DEFAULT 0, tax_total REAL DEFAULT 0,
  round_off REAL DEFAULT 0, grand_total REAL DEFAULT 0, amount_paid REAL DEFAULT 0,
  notes TEXT, created_by TEXT, created_at TEXT,
  UNIQUE(tenant_id, bill_number));

CREATE TABLE IF NOT EXISTS supplier_bill_lines(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, bill_id TEXT NOT NULL,
  product_id TEXT NOT NULL, line_no INTEGER NOT NULL,
  quantity REAL NOT NULL, unit_price REAL NOT NULL,
  taxable_value REAL DEFAULT 0, gst_rate REAL DEFAULT 0,
  cgst REAL DEFAULT 0, sgst REAL DEFAULT 0, igst REAL DEFAULT 0,
  line_total REAL DEFAULT 0, hsn_code TEXT,
  UNIQUE(bill_id, line_no));

CREATE TABLE IF NOT EXISTS supplier_payments(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, payment_number TEXT NOT NULL,
  supplier_id TEXT NOT NULL, payment_date TEXT NOT NULL, amount REAL NOT NULL,
  method TEXT DEFAULT 'bank', reference TEXT, paid_by TEXT,
  UNIQUE(tenant_id, payment_number));

CREATE TABLE IF NOT EXISTS supplier_payment_allocations(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, payment_id TEXT NOT NULL,
  bill_id TEXT NOT NULL, amount REAL NOT NULL,
  UNIQUE(payment_id, bill_id));

CREATE INDEX IF NOT EXISTS ix_sb_tenant_date ON supplier_bills(tenant_id, bill_date);

-- ── credit notes / sales returns ──
-- Apparel comes back. The GST on a return must be reversed at the rate the
-- ORIGINAL line carried, not whatever the slab would be today, so every line
-- points at the invoice line it reverses.
CREATE TABLE IF NOT EXISTS credit_notes(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, cn_number TEXT NOT NULL,
  invoice_id TEXT NOT NULL, customer_id TEXT NOT NULL, location_id TEXT NOT NULL,
  note_date TEXT NOT NULL, reason TEXT,
  taxable_total REAL DEFAULT 0, cgst_total REAL DEFAULT 0, sgst_total REAL DEFAULT 0,
  igst_total REAL DEFAULT 0, round_off REAL DEFAULT 0, grand_total REAL DEFAULT 0,
  refund_mode TEXT DEFAULT 'credit', created_by TEXT, created_at TEXT,
  UNIQUE(tenant_id, cn_number));
CREATE TABLE IF NOT EXISTS credit_note_lines(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, credit_note_id TEXT NOT NULL,
  invoice_line_id TEXT NOT NULL, product_id TEXT NOT NULL, line_no INTEGER NOT NULL,
  quantity REAL NOT NULL, unit_price REAL NOT NULL, discount_pct REAL DEFAULT 0,
  taxable_value REAL DEFAULT 0, gst_rate REAL DEFAULT 0,
  cgst REAL DEFAULT 0, sgst REAL DEFAULT 0, igst REAL DEFAULT 0,
  line_total REAL DEFAULT 0, restock INTEGER DEFAULT 1, condition TEXT DEFAULT 'resaleable',
  UNIQUE(credit_note_id, line_no));
CREATE INDEX IF NOT EXISTS ix_cn_tenant ON credit_notes(tenant_id, note_date);
CREATE INDEX IF NOT EXISTS ix_cnl_invline ON credit_note_lines(invoice_line_id);

CREATE TABLE IF NOT EXISTS recommendations(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, product_id TEXT NOT NULL,
  location_id TEXT NOT NULL, supplier_id TEXT, recommended_qty REAL NOT NULL,
  unit_cost REAL, line_value REAL, urgency TEXT, confidence REAL,
  status TEXT DEFAULT 'pending', final_qty REAL, rationale TEXT DEFAULT '{}',
  created_at TEXT);

CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
  entity_type TEXT, entity_id TEXT, action TEXT, actor TEXT,
  before TEXT, after TEXT, occurred_at TEXT);

-- ── stocktakes ──
CREATE TABLE IF NOT EXISTS stocktakes(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, stocktake_number TEXT NOT NULL,
  location_id TEXT NOT NULL, status TEXT DEFAULT 'draft',
  created_at TEXT NOT NULL, completed_at TEXT, notes TEXT,
  UNIQUE(tenant_id, stocktake_number));
CREATE TABLE IF NOT EXISTS stocktake_lines(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, stocktake_id TEXT NOT NULL,
  product_id TEXT NOT NULL, expected_qty REAL NOT NULL, counted_qty REAL NOT NULL,
  variance_qty REAL NOT NULL, unit_cost REAL NOT NULL,
  UNIQUE(stocktake_id, product_id));

-- ── stock transfers ──
CREATE TABLE IF NOT EXISTS stock_transfers(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, transfer_number TEXT NOT NULL,
  from_location_id TEXT NOT NULL, to_location_id TEXT NOT NULL, status TEXT DEFAULT 'completed',
  created_at TEXT NOT NULL, completed_at TEXT, notes TEXT,
  UNIQUE(tenant_id, transfer_number));
CREATE TABLE IF NOT EXISTS stock_transfer_lines(
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, transfer_id TEXT NOT NULL,
  product_id TEXT NOT NULL, quantity REAL NOT NULL,
  UNIQUE(transfer_id, product_id));

CREATE INDEX IF NOT EXISTS ix_inv_tenant ON inventory_levels(tenant_id, location_id);
CREATE INDEX IF NOT EXISTS ix_si_tenant_date ON sales_invoices(tenant_id, invoice_date);
CREATE INDEX IF NOT EXISTS ix_sil_invoice ON sales_invoice_lines(invoice_id);
CREATE INDEX IF NOT EXISTS ix_prod_tenant ON products(tenant_id, is_active);
CREATE INDEX IF NOT EXISTS ix_reco_tenant ON recommendations(tenant_id, status);
"""
