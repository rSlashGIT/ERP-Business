import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { get, qs } from "@/lib/api";

interface InventoryRow {
  product_id: string; sku: string; name: string; location_code: string;
  on_hand: number; on_order: number; reserved: number; backorder: number;
  available: number; inventory_position: number;
  reorder_point: number | null; order_up_to: number | null; safety_stock: number | null;
  unit_cost: number; stock_value: number; below_reorder: boolean;
}
interface InventoryResponse { total: number; limit: number; offset: number; items: InventoryRow[] }

const num = (n: number) => new Intl.NumberFormat().format(Math.round(n ?? 0));
const money = (n: number) =>
  new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 })
    .format(n ?? 0);

const PAGE = 100;

export default function InventoryScreen() {
  const [search, setSearch] = useState("");
  const [belowReorder, setBelowReorder] = useState(false);
  const [location, setLocation] = useState("");
  const [offset, setOffset] = useState(0);

  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: ["inventory", search, belowReorder, location, offset],
    queryFn: () =>
      get<InventoryResponse>(
        `/api/v1/inventory?${qs({
          search, below_reorder: belowReorder, location_code: location,
          limit: PAGE, offset,
        })}`,
      ),
    placeholderData: keepPreviousData,
  });

  if (error) {
    return (
      <div className="m-6 rounded-lg bg-red-50 p-4 text-sm text-red-700 ring-1 ring-red-200">
        {(error as Error).message}
      </div>
    );
  }

  const rows = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="mx-auto max-w-[1500px] p-6">
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-lg font-bold tracking-tight">Inventory</h1>
          <p className="text-sm text-slate-500">
            {total.toLocaleString()} rows{isFetching && " · refreshing"}
          </p>
        </div>
        <div className="ml-auto flex flex-wrap gap-2">
          <input
            value={search}
            onChange={(e) => { setSearch(e.target.value); setOffset(0); }}
            placeholder="SKU or product name…"
            className="rounded-md border border-slate-200 px-3 py-1.5 text-xs"
          />
          <input
            value={location}
            onChange={(e) => { setLocation(e.target.value); setOffset(0); }}
            placeholder="Location code"
            className="w-32 rounded-md border border-slate-200 px-3 py-1.5 text-xs"
          />
          <button
            onClick={() => { setBelowReorder((b) => !b); setOffset(0); }}
            className={`rounded-md px-3 py-1.5 text-xs font-medium ring-1 ${
              belowReorder ? "bg-slate-900 text-white ring-slate-900" : "bg-white text-slate-600 ring-slate-200"
            }`}
          >
            Below reorder only
          </button>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg bg-white ring-1 ring-slate-200">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-3 py-2 text-left">Item</th>
              <th className="px-3 py-2 text-left">Node</th>
              <th className="px-3 py-2 text-right">On hand</th>
              <th className="px-3 py-2 text-right">On order</th>
              <th className="px-3 py-2 text-right">Position</th>
              <th className="px-3 py-2 text-right">Reorder pt</th>
              <th className="px-3 py-2 text-right">Safety</th>
              <th className="px-3 py-2 text-right">Value</th>
              <th className="px-3 py-2 text-center">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading && (
              <tr><td colSpan={9} className="p-8 text-center text-slate-500">Loading…</td></tr>
            )}
            {!isLoading && rows.length === 0 && (
              <tr><td colSpan={9} className="p-8 text-center text-slate-500">No rows match.</td></tr>
            )}
            {rows.map((r) => (
              <tr key={`${r.product_id}-${r.location_code}`} className="hover:bg-brand-soft">
                <td className="px-3 py-2">
                  <div className="font-medium text-slate-900">{r.name}</div>
                  <div className="font-mono text-[11px] text-slate-400">{r.sku}</div>
                </td>
                <td className="px-3 py-2 text-slate-600">{r.location_code}</td>
                <td className={`px-3 py-2 text-right tabular ${r.on_hand <= 0 ? "font-semibold text-red-700" : ""}`}>
                  {num(r.on_hand)}
                </td>
                <td className="px-3 py-2 text-right tabular text-slate-600">{num(r.on_order)}</td>
                <td className="px-3 py-2 text-right tabular font-medium">{num(r.inventory_position)}</td>
                <td className="px-3 py-2 text-right tabular text-slate-600">
                  {r.reorder_point != null ? num(r.reorder_point) : "—"}
                </td>
                <td className="px-3 py-2 text-right tabular text-slate-600">
                  {r.safety_stock != null ? num(r.safety_stock) : "—"}
                </td>
                <td className="px-3 py-2 text-right tabular">{money(r.stock_value)}</td>
                <td className="px-3 py-2 text-center">
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ring-1 ${
                    r.on_hand <= 0 ? "bg-red-50 text-red-700 ring-red-200"
                      : r.below_reorder ? "bg-amber-50 text-amber-700 ring-amber-200"
                      : "bg-emerald-50 text-emerald-700 ring-emerald-200"
                  }`}>
                    {r.on_hand <= 0 ? "out" : r.below_reorder ? "low" : "ok"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {total > PAGE && (
        <div className="mt-3 flex items-center gap-3 text-xs">
          <button
            disabled={offset === 0}
            onClick={() => setOffset((o) => Math.max(0, o - PAGE))}
            className="rounded border border-slate-200 px-3 py-1.5 disabled:opacity-40"
          >Previous</button>
          <span className="text-slate-500">
            {offset + 1}–{Math.min(offset + PAGE, total)} of {total.toLocaleString()}
          </span>
          <button
            disabled={offset + PAGE >= total}
            onClick={() => setOffset((o) => o + PAGE)}
            className="rounded border border-slate-200 px-3 py-1.5 disabled:opacity-40"
          >Next</button>
        </div>
      )}
    </div>
  );
}
