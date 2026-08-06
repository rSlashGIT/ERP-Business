import { get, post, qs } from "@/lib/api";
import type { ApprovalResult, LineDecision, QueueResponse } from "./types";

export const fetchQueue = (params: Record<string, string | number | undefined>) =>
  get<QueueResponse>(`/api/v1/procurement/recommendations?${qs(params)}`);

export const submitDecisions = (decisions: LineDecision[], actor: string) =>
  post<ApprovalResult>("/api/v1/procurement/recommendations/decide", {
    decisions, actor, create_purchase_orders: true,
  });
