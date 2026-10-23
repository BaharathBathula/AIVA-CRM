/** Shared types for the marketing screens. */

export interface CampaignStats {
  recipients?: number;
  sent?: number;
  failed?: number;
  queued?: number;
  opened?: number;
  unsubscribed?: number;
  outbox?: number;
  open_rate?: number;
}

export interface CampaignSummary {
  id: string;
  name: string;
  subject: string;
  status: "draft" | "scheduled" | "sending" | "sent" | "failed";
  audience: { object_type: string; filters: unknown[]; abm_tier?: string | null };
  scheduled_at: string | null;
  completed_at: string | null;
  stats: CampaignStats;
  created_at: string;
  updated_at: string;
}

export const CAMPAIGN_TONE: Record<string, "neutral" | "info" | "warning" | "success" | "danger"> = {
  draft: "neutral",
  scheduled: "info",
  sending: "warning",
  sent: "success",
  failed: "danger",
};

export interface FormFieldDef {
  key: string;
  label: string;
  type: "text" | "email" | "tel" | "textarea" | "select" | "checkbox" | "number" | "date";
  required: boolean;
  maps_to: string;
  options: string[];
  placeholder?: string;
  help?: string;
  step: number;
}

export interface WebFormDef {
  id: string;
  name: string;
  slug: string;
  target: "lead" | "case";
  fields: FormFieldDef[];
  settings: Record<string, string>;
  is_active: boolean;
  submission_count: number;
  last_submission_at: string | null;
  url: string;
  embed_code: string;
  updated_at: string;
}

export interface JourneySummary {
  id: string;
  name: string;
  description: string | null;
  object_type: "lead" | "contact";
  trigger: "created" | "manual";
  conditions: { field: string; operator: string; value: string | null }[];
  steps: { type: string; params: Record<string, unknown> }[];
  status: "draft" | "active" | "paused";
  enrollments: { active: number; completed: number; exited: number; failed: number; total: number };
  updated_at: string;
}
