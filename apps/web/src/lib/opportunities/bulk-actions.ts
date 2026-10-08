import type {
  ForecastCategory,
  OpportunityPriority,
} from "@/types/opportunity";

export type OpportunityBulkActionType =
  | "change_owner"
  | "change_stage"
  | "change_priority"
  | "change_forecast"
  | "delete";

export interface OpportunityBulkActionRequest {
  opportunityIds: string[];
  action: OpportunityBulkActionType;
  value?: string;
}

export interface OpportunityBulkActionResult {
  successfulIds: string[];
  failedIds: string[];
  errors: Record<string, string>;
}

export const BULK_PRIORITY_OPTIONS:
  OpportunityPriority[] = [
    "low",
    "medium",
    "high",
    "critical",
  ];

export const BULK_FORECAST_OPTIONS:
  ForecastCategory[] = [
    "pipeline",
    "best_case",
    "commit",
    "closed",
    "omitted",
  ];
