"use client";

import { ExternalLink, MessageSquare } from "lucide-react";
import type { ReactNode } from "react";

import { Badge, Field, type Tone } from "@/components/ui";
import { relativeTime } from "@/lib/format";

import { IntegrationTile } from "../common";

/* Shapes returned by /helpdesk/... (apps/api/app/api/routes/int_support.py). */

export type DeskKey = "zendesk" | "freshdesk" | "intercom" | "help_scout" | "jira_service_management";

export type DeskOptions = {
  mailboxes?: { id: string; name: string | null; email?: string | null }[];
  service_desks?: { id: string; name: string | null; project_key?: string | null }[];
  service_desk_id?: string | null;
  request_types?: { id: string; name: string | null }[];
  request_type_id?: string | null;
};

export type Desk = {
  key: DeskKey;
  name: string;
  kind: "ticket" | "conversation";
  color: string;
  monogram: string;
  enabled: boolean;
  connected: boolean;
  ready: boolean;
  status: string | null;
  label: string | null;
  options: DeskOptions;
  defaults: { notify_customer: boolean; mailbox_id: string | null; request_type_id: string | null };
  can_notify: boolean;
  has_priority: boolean;
};

export type TicketStatus = "new" | "open" | "pending" | "on_hold" | "solved" | "closed";

export type TicketItem = {
  id?: string;
  provider: DeskKey;
  provider_name: string;
  kind: "ticket" | "conversation" | string;
  external_id: string;
  number: string;
  subject: string;
  status: TicketStatus;
  status_label: string;
  priority: "low" | "normal" | "high" | "urgent" | null;
  requester_email: string | null;
  requester_name?: string | null;
  url: string | null;
  comment_count: number | null;
  created_at?: string | null;
  updated_at: string | null;
  is_open: boolean;
  case_id?: string | null;
};

export type CaseLink = TicketItem & {
  link_id: string;
  origin: "escalation" | "linked" | "ticket";
  linked_at: string;
  last_synced_at: string | null;
  error: string | null;
};

export const TICKET_TONE: Record<TicketStatus, Tone> = {
  new: "primary",
  open: "warning",
  pending: "info",
  on_hold: "neutral",
  solved: "success",
  closed: "neutral",
};

export const PRIORITY_LABEL: Record<string, string> = { low: "Low", normal: "Normal", high: "High", urgent: "Urgent" };

export function TicketStatusBadge({ item }: { item: Pick<TicketItem, "status" | "status_label"> }) {
  return (
    <Badge tone={TICKET_TONE[item.status] ?? "neutral"} dot>
      {item.status_label || item.status.replace(/_/g, " ")}
    </Badge>
  );
}

export function DeskMark({ desk, size = 22 }: { desk: Pick<Desk, "color" | "monogram" | "name">; size?: number }) {
  return <IntegrationTile item={desk} size={size} />;
}

/** One ticket or conversation in a list. */
export function TicketRow({ item, desk, actions, meta }: { item: TicketItem; desk?: Desk; actions?: ReactNode; meta?: ReactNode }) {
  const who = item.requester_name || item.requester_email;
  return (
    <li className="isup-row">
      {desk ? <DeskMark desk={desk} /> : null}
      <div className="mini-main">
        {item.url ? (
          <a className="mini-title" href={item.url} target="_blank" rel="noopener noreferrer" title={`Open in ${item.provider_name}`}>
            {item.subject || "(no subject)"}
          </a>
        ) : (
          <span className="mini-title">{item.subject || "(no subject)"}</span>
        )}
        <span className="mini-sub">
          {[`${item.provider_name} ${item.number}`, who, item.updated_at ? `updated ${relativeTime(item.updated_at)}` : null].filter(Boolean).join(" · ")}
          {meta}
        </span>
      </div>
      <div className="isup-row-side">
        {item.priority === "urgent" || item.priority === "high" ? <Badge tone={item.priority === "urgent" ? "danger" : "warning"}>{PRIORITY_LABEL[item.priority]}</Badge> : null}
        <TicketStatusBadge item={item} />
        {typeof item.comment_count === "number" ? (
          <span className="ui-muted ui-small isup-count" title="Messages on the ticket">
            <MessageSquare size={12} aria-hidden /> {item.comment_count}
          </span>
        ) : null}
        {item.url ? (
          <a className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" href={item.url} target="_blank" rel="noopener noreferrer" aria-label={`Open ${item.number} in ${item.provider_name}`}>
            <ExternalLink size={14} />
          </a>
        ) : null}
        {actions}
      </div>
    </li>
  );
}

export type DeskFieldsValue = {
  priority: string;
  mailbox_id: string;
  request_type_id: string;
  notify_customer: boolean;
};

export function deskDefaults(desk: Desk | undefined, priority = "normal"): DeskFieldsValue {
  return {
    priority,
    mailbox_id: desk?.defaults.mailbox_id ?? desk?.options.mailboxes?.[0]?.id ?? "",
    request_type_id: desk?.defaults.request_type_id ?? desk?.options.request_type_id ?? desk?.options.request_types?.[0]?.id ?? "",
    notify_customer: Boolean(desk?.defaults.notify_customer),
  };
}

/** Priority, Help Scout mailbox, Jira request type and "email the customer", as the desk needs. */
export function DeskFields({ desk, value, onChange }: { desk: Desk; value: DeskFieldsValue; onChange: (value: DeskFieldsValue) => void }) {
  const mailboxes = desk.options.mailboxes ?? [];
  const types = desk.options.request_types ?? [];
  return (
    <>
      {desk.has_priority ? (
        <Field label="Priority">
          <select className="ui-select" value={value.priority} onChange={(event) => onChange({ ...value, priority: event.target.value })}>
            {Object.entries(PRIORITY_LABEL).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </Field>
      ) : null}
      {desk.key === "help_scout" ? (
        <Field label="Mailbox" required>
          <select className="ui-select" value={value.mailbox_id} onChange={(event) => onChange({ ...value, mailbox_id: event.target.value })}>
            <option value="">Choose a mailbox</option>
            {mailboxes.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name ?? m.email ?? m.id}
              </option>
            ))}
          </select>
        </Field>
      ) : null}
      {desk.key === "jira_service_management" ? (
        <Field label="Request type" required>
          <select className="ui-select" value={value.request_type_id} onChange={(event) => onChange({ ...value, request_type_id: event.target.value })}>
            {types.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name ?? t.id}
              </option>
            ))}
          </select>
        </Field>
      ) : null}
      {desk.can_notify ? (
        <label className="ui-check span-2">
          <input type="checkbox" checked={value.notify_customer} onChange={(event) => onChange({ ...value, notify_customer: event.target.checked })} />
          <span>Make the first message public (the customer gets it by email)</span>
        </label>
      ) : null}
    </>
  );
}

export function deskPayload(desk: Desk, value: DeskFieldsValue) {
  return {
    priority: desk.has_priority ? value.priority : null,
    mailbox_id: desk.key === "help_scout" ? value.mailbox_id || null : null,
    request_type_id: desk.key === "jira_service_management" ? value.request_type_id || null : null,
    notify_customer: desk.can_notify ? value.notify_customer : null,
  };
}

export function noun(desk: Pick<Desk, "kind"> | undefined) {
  return desk?.kind === "conversation" ? "conversation" : "ticket";
}
