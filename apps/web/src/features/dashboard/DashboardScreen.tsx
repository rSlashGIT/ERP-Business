import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";

interface Dashboard {
  inventory: { stock_value: number; skus_below_reorder: number; skus_out_of_stock: number };
  procurement: {
    pending_recommendations: number; pending_value: number;
    critical_recommendations: number; open_purchase_orders: number; open_po_value: number;
  };
  last_run: {
    id: string; run_date: string; status: string; policy_version: string | null;
    lines_recommended: number; duration_ms: number | null; error: string | null;
  } | null;
}

const money = (n: number) =>
  new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 })
    .format(n ?? 0);

function Kpi({ label, value, sub, tone }: {
  label: string; value: string | number; sub?: string; tone?: "bad" | "warn";
}) {
  return (
    <div className="rounded-lg bg-white p-4 ring-1 ring-slate-200">
      <div className="text-[10px] font-bold uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-bold tabular tracking-tight ${
        tone === "bad" ? "text-red-700" : tone === "warn" ? "text-amber-700" : "text-slate-900"
      }`}>{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

export default function DashboardScreen() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => get<Dashboard>("/api/v1/dashboard"),
    refetchInterval: 60_000,
  });

  if (isLoading) return <div className="p-8 text-slate-500">Loading…</div>;
  if (error) {
    return (
      <div className="m-6 rounded-lg bg-red-50 p-4 text-sm text-red-700 ring-1 ring-red-200">
        {(error as Error).message}
      </div>
    );
  }
  if (!data) return null;

  const run = data.last_run;
  return (
    <div className="mx-auto max-w-[1400px] p-6">
      <h1 className="mb-4 text-lg font-bold tracking-tight">Dashboard</h1>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Kpi label="Stock value" value={money(data.inventory.stock_value)}
             sub={`${data.inventory.skus_below_reorder ?? 0} below reorder`} />
        <Kpi label="Out of stock" value={data.inventory.skus_out_of_stock}
             tone={data.inventory.skus_out_of_stock > 0 ? "bad" : undefined} sub="SKUs at zero" />
        <Kpi label="Pending approval" value={data.procurement.pending_recommendations}
             sub={`${money(data.procurement.pending_value)} to commit`} />
        <Kpi label="Critical" value={data.procurement.critical_recommendations}
             tone={data.procurement.critical_recommendations > 0 ? "warn" : undefined}
             sub="stockout inside lead time" />
        <Kpi label="Open POs" value={data.procurement.open_purchase_orders}
             sub={money(data.procurement.open_po_value)} />
      </div>

      <div className="mt-5 rounded-lg bg-white p-4 ring-1 ring-slate-200">
        <h2 className="text-sm font-semibold">Last replenishment run</h2>
        {!run ? (
          <p className="mt-1 text-sm text-slate-500">
            No run yet. The scheduler fires nightly at 02:00.
          </p>
        ) : (
          <>
            <dl className="mt-3 grid grid-cols-2 gap-x-8 gap-y-1.5 text-xs md:grid-cols-4">
              {[
                ["Date", run.run_date],
                ["Status", run.status],
                ["Lines", String(run.lines_recommended)],
                ["Duration", run.duration_ms != null ? `${run.duration_ms} ms` : "—"],
                ["Policy", run.policy_version ?? "default (classical s,S)"],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between border-b border-slate-100 pb-1">
                  <dt className="text-slate-500">{k}</dt>
                  <dd className="font-medium text-slate-800">{v}</dd>
                </div>
              ))}
            </dl>
            {run.error && (
              <p className="mt-3 rounded bg-red-50 px-3 py-2 text-xs text-red-700 ring-1 ring-red-200">
                {run.error}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
