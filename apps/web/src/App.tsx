import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "@/components/Layout";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import DashboardScreen from "@/features/dashboard/DashboardScreen";
import InventoryScreen from "@/features/inventory/InventoryScreen";
import PurchaseOrderApproval from "@/features/procurement/PurchaseOrderApproval";
import { ApiError } from "@/lib/api";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      // Never retry auth/permission failures -- retrying a 401 three times
      // just delays the redirect and spams the audit log.
      retry: (failureCount, err) => {
        if (err instanceof ApiError && [0, 401, 403, 404, 422].includes(err.status)) return false;
        return failureCount < 2;
      },
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary fallbackLabel="The application failed to start">
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<DashboardScreen />} />
              <Route path="procurement" element={<PurchaseOrderApproval />} />
              <Route path="inventory" element={<InventoryScreen />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ErrorBoundary>
    </QueryClientProvider>
  );
}
