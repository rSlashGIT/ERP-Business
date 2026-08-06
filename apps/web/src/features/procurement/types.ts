export type Urgency = "critical" | "high" | "medium" | "low" | "none";
export type RecoStatus = "pending" | "approved" | "modified" | "rejected" | "expired";
export type Decision = "approve" | "reject" | "modify";

export interface Rationale {
  reorder_point: number;
  order_up_to: number;
  safety_stock: number;
  cycle_stock: number;
  inventory_position: number;
  demand_over_leadtime: number;
  sigma_demand_over_leadtime: number;
  lead_time_mean_days: number;
  lead_time_std_days: number;
  lead_time_source: "empirical" | "shrunk" | "contract" | "default";
  implied_service_level: number;
  days_of_cover_before: number;
  days_of_cover_after: number;
  projected_stockout_day: number | null;
  segment: string;
  binding_constraint: string | null;
  explanation: string;
}

export interface Recommendation {
  id: string;
  sku: string;
  product_name: string;
  location_code: string;
  location_name: string;
  supplier_id: string | null;
  supplier_name: string | null;
  recommended_qty: number;
  unconstrained_qty: number;
  unit_cost: number;
  line_value: number;
  urgency: Urgency;
  confidence: number;
  status: RecoStatus;
  rationale: Rationale;
  warnings: string[];
}

export interface QueueResponse {
  run_id: string | null;
  total: number;
  limit: number;
  offset: number;
  summary: Record<string, { count: number; value: number }>;
  items: Recommendation[];
}

export interface LineDecision {
  recommendation_id: string;
  action: Decision;
  final_qty?: number;
  note?: string;
}

export interface ApprovalResult {
  approved: number;
  rejected: number;
  modified: number;
  purchase_orders_created: string[];
  errors: { id: string; error: string }[];
}
