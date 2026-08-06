/**
 * Procurement / Purchase Order Approval.
 *
 * The screen where a human decides whether the AI was right. Design rules it
 * follows, each learned from procurement software that people refuse to use:
 *
 *  1. NOTHING IS AUTO-APPROVED. Every line requires an explicit act.
 *  2. THE MODEL MUST SHOW ITS WORKING. Reorder point, safety stock, lead-time
 *     distribution and the binding constraint are one click away on every row.
 *     A quantity with no derivation gets rejected wholesale the second time.
 *  3. OVERRIDES ARE FIRST-CLASS, NOT AN ESCAPE HATCH. Editing a quantity is
 *     one field. The delta versus the AI is shown immediately, and a reason is
 *     REQUIRED past a threshold — that reason is the training signal.
 *  4. RISK BEFORE VALUE. Sorted by urgency first: a critical ₹200 line that
 *     stops a production run outranks a routine ₹80,000 one.
 *  5. LOW CONFIDENCE IS VISIBLE. The model saying "I am 24% sure" is more
 *     useful than a confident wrong number, so it is rendered, not hidden.
 *  6. BULK ACTIONS ARE SCOPED. "Approve all" only ever applies to the current
 *     filtered selection, and always states the count and value first.
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { fetchQueue, submitDecisions } from "./api";
import type {
  ApprovalResult, Decision, LineDecision, QueueResponse, Recommendation, Urgency,
} from "./types";

const URGENCY_RANK: Record<Urgency, number> = {
  critical: 0, high: 1, medium: 2, low: 3, none: 4,
};

const URGENCY_STYLE: Record<Urgency, string> = {
  critical: "bg-red-50 text-red-700 ring-red-200",
  high: "bg-amber-50 text-amber-700 ring-amber-200",
  medium: "bg-blue-50 text-blue-700 ring-blue-200",
  low: "bg-slate-50 text-slate-600 ring-slate-200",
  none: "bg-slate-50 text-slate-400 ring-slate-200",
};

/** Overrides beyond this fraction require a written reason. */
const REASON_REQUIRED_DELTA = 0.2;

const money = (n: number) =>
  new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(n);
const num = (n: number) => new Intl.NumberFormat().format(Math.round(n));

interface RowState {
  decision: Decision | null;
  qty: number;
  note: string;
}

export default function PurchaseOrderApproval({ actor = "buyer@erp" }: { actor?: string }) {
  const [data, setData] = useState<QueueResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<Record<string, RowState>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [urgencyFilter, setUrgencyFilter] = useState<Urgency | "all">("all");
  const [search, setSearch] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<ApprovalResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchQueue({ status: "pending", limit: 500 });
      setData(res);
      setRows(
        Object.fromEntries(
          res.items.map((i) => [i.id, { decision: null, qty: i.recommended_qty, note: "" }])
        )
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load queue");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const visible = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    return data.items
      .filter((i) => urgencyFilter === "all" || i.urgency === urgencyFilter)
      .filter((i) =>
        !q ||
        i.sku.toLowerCase().includes(q) ||
        i.product_name.toLowerCase().includes(q) ||
        (i.supplier_name ?? "").toLowerCase().includes(q)
      )
      .sort((a, b) =>
        URGENCY_RANK[a.urgency] - URGENCY_RANK[b.urgency] || b.line_value - a.line_value
      );
  }, [data, urgencyFilter, search]);

  const setRow = (id: string, patch: Partial<RowState>) =>
    setRows((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));

  const deltaOf = (item: Recommendation): number => {
    const r = rows[item.id];
    if (!r || !item.recommended_qty) return 0;
    return (r.qty - item.recommended_qty) / item.recommended_qty;
  };

  /** A row needs a reason if the quantity moved materially from the AI's. */
  const needsReason = (item: Recommendation): boolean => {
    const r = rows[item.id];
    if (!r || r.decision !== "modify") return false;
    return Math.abs(deltaOf(item)) > REASON_REQUIRED_DELTA && r.note.trim().length === 0;
  };

  const staged = useMemo(
    () => visible.filter((i) => rows[i.id]?.decision !== null && rows[i.id] !== undefined),
    [visible, rows]
  );
  const blocked = useMemo(() => staged.filter(needsReason), [staged, rows]);

  const stagedValue = staged.reduce((sum, i) => {
    const r = rows[i.id];
    return sum + (r.decision === "reject" ? 0 : r.qty * i.unit_cost);
  }, 0);

  const bulk = (decision: Decision) =>
    setRows((prev) => {
      const next = { ...prev };
      for (const i of visible) next[i.id] = { ...next[i.id], decision };
      return next;
    });

  const onSubmit = async () => {
    if (staged.length === 0 || blocked.length > 0) return;
    setSubmitting(true);
    setError(null);
    try {
      const decisions: LineDecision[] = staged.map((i) => {
        const r = rows[i.id];
        return {
          recommendation_id: i.id,
          action: r.decision as Decision,
          final_qty: r.decision === "modify" ? r.qty : undefined,
          note: r.note || undefined,
        };
      });
      const res = await submitDecisions(decisions, actor);
      setResult(res);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "submission failed");
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <div className="p-8 text-slate-500">Loading approval queue…</div>;

  if (error && !data) {
    return (
      <div className="p-8">
        <div className="rounded-lg bg-red-50 p-4 text-red-700 ring-1 ring-red-200">
          <p className="font-semibold">Could not load recommendations</p>
          <p className="mt-1 text-sm">{error}</p>
          <button onClick={() => void load()} className="mt-3 rounded bg-red-600 px-3 py-1.5 text-sm text-white">
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!data || data.items.length === 0) {
    return (
      <div className="p-12 text-center">
        <h2 className="text-lg font-semibold text-slate-800">Nothing waiting for approval</h2>
        <p className="mt-1 text-sm text-slate-500">
          The last replenishment run produced no recommendations. The next run is scheduled for 02:00.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1500px] p-6">
      <header className="mb-5 flex flex-wrap items-end gap-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-900">Purchase Order Approval</h1>
          <p className="text-sm text-slate-500">
            Run <span className="font-mono">{data.run_id?.slice(0, 8)}</span> · {data.total} lines awaiting a decision
          </p>
        </div>
        <div className="ml-auto flex flex-wrap gap-2">
          {(["all", "critical", "high", "medium", "low"] as const).map((u) => {
            const s = u === "all" ? null : data.summary[u];
            return (
              <button
                key={u}
                onClick={() => setUrgencyFilter(u as Urgency | "all")}
                className={`rounded-md px-3 py-1.5 text-xs font-medium ring-1 ${
                  urgencyFilter === u ? "bg-slate-900 text-white ring-slate-900" : "bg-white text-slate-600 ring-slate-200"
                }`}
              >
                {u}
                {s ? ` (${s.count})` : ""}
              </button>
            );
          })}
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="SKU, product or supplier…"
            className="rounded-md border border-slate-200 px-3 py-1.5 text-xs"
          />
        </div>
      </header>

      {result && (
        <div className="mb-4 rounded-lg bg-emerald-50 p-4 text-sm text-emerald-800 ring-1 ring-emerald-200">
          <p className="font-semibold">
            {result.approved} approved · {result.modified} modified · {result.rejected} rejected
          </p>
          {result.purchase_orders_created.length > 0 && (
            <p className="mt-1">Purchase orders created: {result.purchase_orders_created.join(", ")}</p>
          )}
          {result.errors.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-red-700">
              {result.errors.map((e) => <li key={e.id}>{e.id.slice(0, 8)}: {e.error}</li>)}
            </ul>
          )}
        </div>
      )}

      {error && <div className="mb-4 rounded bg-red-50 p-3 text-sm text-red-700 ring-1 ring-red-200">{error}</div>}

      <div className="mb-3 flex items-center gap-2 text-xs">
        <span className="text-slate-500">Bulk action on {visible.length} filtered lines:</span>
        <button onClick={() => bulk("approve")} className="rounded bg-emerald-600 px-2.5 py-1 font-medium text-white">Approve all</button>
        <button onClick={() => bulk("reject")} className="rounded bg-slate-200 px-2.5 py-1 font-medium text-slate-700">Reject all</button>
        <button onClick={() => setRows((p) => Object.fromEntries(Object.entries(p).map(([k, v]) => [k, { ...v, decision: null }])))}
                className="rounded px-2.5 py-1 font-medium text-slate-500 underline">Clear</button>
      </div>

      <div className="overflow-hidden rounded-lg ring-1 ring-slate-200">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-3 py-2 text-left">Urgency</th>
              <th className="px-3 py-2 text-left">Item</th>
              <th className="px-3 py-2 text-left">Supplier / Node</th>
              <th className="px-3 py-2 text-right">Position</th>
              <th className="px-3 py-2 text-right">AI qty</th>
              <th className="px-3 py-2 text-right">Order qty</th>
              <th className="px-3 py-2 text-right">Value</th>
              <th className="px-3 py-2 text-center">Confidence</th>
              <th className="px-3 py-2 text-center">Decision</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 bg-white">
            {visible.map((item) => {
              const r = rows[item.id];
              const delta = deltaOf(item);
              const open = expanded === item.id;
              const missingReason = needsReason(item);
              return (
                <React.Fragment key={item.id}>
                  <tr className={r?.decision ? "bg-slate-50/60" : undefined}>
                    <td className="px-3 py-2">
                      <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ring-1 ${URGENCY_STYLE[item.urgency]}`}>
                        {item.urgency}
                      </span>
                      {item.rationale?.projected_stockout_day != null && item.urgency === "critical" && (
                        <div className="mt-0.5 text-[10px] text-red-600">
                          out in {item.rationale.projected_stockout_day}d
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <div className="font-medium text-slate-900">{item.product_name}</div>
                      <div className="font-mono text-[11px] text-slate-400">{item.sku}</div>
                    </td>
                    <td className="px-3 py-2 text-slate-600">
                      <div>{item.supplier_name ?? <span className="text-red-600">no supplier</span>}</div>
                      <div className="text-[11px] text-slate-400">{item.location_code}</div>
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-600">
                      {num(item.rationale?.inventory_position ?? 0)}
                      <div className="text-[11px] text-slate-400">
                        ROP {num(item.rationale?.reorder_point ?? 0)}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums font-medium">{num(item.recommended_qty)}</td>
                    <td className="px-3 py-2 text-right">
                      <input
                        type="number"
                        min={0}
                        value={r?.qty ?? 0}
                        onChange={(e) => {
                          const v = Math.max(0, Number(e.target.value) || 0);
                          setRow(item.id, {
                            qty: v,
                            decision: v === item.recommended_qty ? r?.decision ?? null : "modify",
                          });
                        }}
                        className={`w-24 rounded border px-2 py-1 text-right tabular-nums ${
                          missingReason ? "border-red-400 bg-red-50" : "border-slate-200"
                        }`}
                      />
                      {Math.abs(delta) > 0.001 && (
                        <div className={`text-[11px] ${delta > 0 ? "text-blue-600" : "text-amber-600"}`}>
                          {delta > 0 ? "+" : ""}{(delta * 100).toFixed(0)}% vs AI
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-700">
                      {money((r?.qty ?? 0) * item.unit_cost)}
                    </td>
                    <td className="px-3 py-2">
                      <div className="mx-auto h-1.5 w-16 rounded-full bg-slate-100">
                        <div
                          className={`h-1.5 rounded-full ${
                            item.confidence > 0.6 ? "bg-emerald-500"
                              : item.confidence > 0.35 ? "bg-amber-500" : "bg-red-500"
                          }`}
                          style={{ width: `${Math.max(4, item.confidence * 100)}%` }}
                        />
                      </div>
                      <div className="mt-0.5 text-center text-[10px] text-slate-400">
                        {(item.confidence * 100).toFixed(0)}%
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex justify-center gap-1">
                        {(["approve", "modify", "reject"] as Decision[]).map((d) => (
                          <button
                            key={d}
                            onClick={() => setRow(item.id, { decision: r?.decision === d ? null : d })}
                            className={`rounded px-2 py-1 text-[11px] font-medium ${
                              r?.decision === d
                                ? d === "approve" ? "bg-emerald-600 text-white"
                                  : d === "reject" ? "bg-red-600 text-white" : "bg-blue-600 text-white"
                                : "bg-slate-100 text-slate-600"
                            }`}
                          >
                            {d === "approve" ? "✓" : d === "reject" ? "✕" : "±"}
                          </button>
                        ))}
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <button
                        onClick={() => setExpanded(open ? null : item.id)}
                        className="text-[11px] text-slate-500 underline"
                      >
                        {open ? "hide" : "why?"}
                      </button>
                    </td>
                  </tr>

                  {open && item.rationale && (
                    <tr className="bg-slate-50">
                      <td colSpan={10} className="px-6 py-4">
                        <p className="mb-3 text-sm text-slate-700">{item.rationale.explanation}</p>
                        <div className="grid grid-cols-2 gap-x-8 gap-y-1.5 text-xs md:grid-cols-4">
                          {([
                            ["Reorder point (s)", num(item.rationale.reorder_point)],
                            ["Order-up-to (S)", num(item.rationale.order_up_to)],
                            ["Safety stock", num(item.rationale.safety_stock)],
                            ["Cycle stock", num(item.rationale.cycle_stock)],
                            ["Lead time", `${item.rationale.lead_time_mean_days.toFixed(1)} ± ${item.rationale.lead_time_std_days.toFixed(1)} d`],
                            ["Lead-time source", item.rationale.lead_time_source],
                            ["σ demand over LT", num(item.rationale.sigma_demand_over_leadtime)],
                            ["Implied service", `${(item.rationale.implied_service_level * 100).toFixed(1)}%`],
                            ["Cover before", `${item.rationale.days_of_cover_before.toFixed(1)} d`],
                            ["Cover after", `${item.rationale.days_of_cover_after.toFixed(1)} d`],
                            ["Segment", item.rationale.segment],
                            ["Unconstrained qty", num(item.unconstrained_qty)],
                          ] as [string, string][]).map(([k, v]) => (
                            <div key={k} className="flex justify-between border-b border-slate-200/70 pb-1">
                              <span className="text-slate-500">{k}</span>
                              <span className="font-medium tabular-nums text-slate-800">{v}</span>
                            </div>
                          ))}
                        </div>
                        {item.rationale.binding_constraint && (
                          <p className="mt-3 rounded bg-amber-50 px-3 py-1.5 text-xs text-amber-800 ring-1 ring-amber-200">
                            Quantity was adjusted from {num(item.unconstrained_qty)} by constraint:{" "}
                            <b>{item.rationale.binding_constraint}</b>
                          </p>
                        )}
                        {item.warnings.length > 0 && (
                          <ul className="mt-2 list-disc pl-5 text-xs text-red-700">
                            {item.warnings.map((w, i) => <li key={i}>{w}</li>)}
                          </ul>
                        )}
                        <div className="mt-3">
                          <label className="text-[11px] font-medium text-slate-600">
                            Override reason {missingReason && <span className="text-red-600">— required for a change this large</span>}
                          </label>
                          <input
                            value={r?.note ?? ""}
                            onChange={(e) => setRow(item.id, { note: e.target.value })}
                            placeholder="e.g. supplier confirmed a promotion next month"
                            className={`mt-1 w-full rounded border px-3 py-1.5 text-xs ${
                              missingReason ? "border-red-400" : "border-slate-200"
                            }`}
                          />
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      <footer className="sticky bottom-0 mt-4 flex items-center gap-4 rounded-lg bg-white/95 p-4 shadow-lg ring-1 ring-slate-200 backdrop-blur">
        <div className="text-sm">
          <span className="font-semibold text-slate-900">{staged.length}</span>
          <span className="text-slate-500"> lines staged · </span>
          <span className="font-semibold text-slate-900">{money(stagedValue)}</span>
          <span className="text-slate-500"> committed value</span>
        </div>
        {blocked.length > 0 && (
          <span className="rounded bg-red-50 px-2 py-1 text-xs text-red-700 ring-1 ring-red-200">
            {blocked.length} override{blocked.length > 1 ? "s" : ""} need a reason
          </span>
        )}
        <button
          onClick={() => void onSubmit()}
          disabled={submitting || staged.length === 0 || blocked.length > 0}
          className="ml-auto rounded-md bg-slate-900 px-5 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {submitting ? "Creating purchase orders…" : `Commit ${staged.length} decisions`}
        </button>
      </footer>
    </div>
  );
}
