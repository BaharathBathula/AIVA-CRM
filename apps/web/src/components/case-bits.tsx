"use client";

import { AlertTriangle, CheckCircle2, Clock } from "lucide-react";

import { Badge, SLA_TONE } from "@/components/ui";
import { duration } from "@/lib/format";
import type { SlaInfo } from "@/lib/platform-types";

export const CASE_STATUS_LABEL: Record<string, string> = {
  new: "New",
  open: "Open",
  pending: "Waiting on customer",
  on_hold: "On hold",
  resolved: "Resolved",
  closed: "Closed",
};

export const CHANNEL_LABEL: Record<string, string> = {
  email: "Email",
  phone: "Phone",
  web: "Web form",
  chat: "Chat",
  portal: "Portal",
  social: "Social",
};

export function SlaBadge({ sla }: { sla: SlaInfo }) {
  if (!sla || sla.state === "none") return <span className="ui-muted ui-small">No SLA</span>;
  const target = sla.target === "first_response" ? "response" : "resolution";
  if (sla.state === "met") {
    return (
      <Badge tone="success">
        <CheckCircle2 size={12} /> SLA met
      </Badge>
    );
  }
  if (sla.state === "missed") {
    return (
      <Badge tone="danger">
        <AlertTriangle size={12} /> SLA missed
      </Badge>
    );
  }
  const minutes = sla.minutes_remaining ?? 0;
  const text = sla.state === "breached" ? `${duration(minutes)} over ${target}` : `${duration(minutes)} to ${target}`;
  return (
    <Badge tone={SLA_TONE[sla.state]}>
      {sla.state === "breached" ? <AlertTriangle size={12} /> : <Clock size={12} />} {text}
    </Badge>
  );
}
