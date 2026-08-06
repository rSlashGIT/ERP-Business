import { NavLink, Outlet } from "react-router-dom";
import { ErrorBoundary } from "./ErrorBoundary";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/procurement", label: "Procurement" },
  { to: "/inventory", label: "Inventory" },
];

export function Layout() {
  return (
    <div className="min-h-screen">
      <header className="flex items-center gap-6 bg-brand px-6 py-3 text-white">
        <div>
          <div className="text-[15px] font-bold tracking-tight">ERP · SmartStock</div>
          <div className="text-[11px] text-blue-200">AI-assisted replenishment</div>
        </div>
        <nav className="flex gap-1">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                `rounded-md px-3 py-1.5 text-[13px] transition ${
                  isActive ? "bg-white text-brand font-semibold" : "text-blue-100 hover:bg-white/10"
                }`
              }
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main>
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
