"use client";

import Link from "next/link";
import { CalendarClock, ExternalLink } from "lucide-react";
import { useState } from "react";

import { CopyField } from "@/components/copy-field";
import { useSession } from "@/components/providers";
import { Alert, LoadingRows, Modal } from "@/components/ui";
import { query, useApi } from "@/lib/use-api";

import type { CalendlyLinks } from "./documents-shared";

/**
 * "Scheduling links" button for a lead or contact header (or anywhere without a record):
 *   <CalendlyLinksButton recordType="lead" recordId={lead.id} />
 * Links come with the person's name and email filled in. Renders nothing unless
 * Calendly is on and connected for the workspace.
 */
export function CalendlyLinksButton({
  recordType,
  recordId,
  label = "Scheduling links",
  className = "ui-btn ui-btn-sm",
}: {
  recordType?: "lead" | "contact";
  recordId?: string;
  label?: string;
  className?: string;
}) {
  const { has } = useSession();
  const available = has("calendar_sync");
  const status = useApi<{ enabled: boolean; connected: boolean }>(available ? "/calendly/status" : null);
  const [open, setOpen] = useState(false);
  if (!available || !status.data?.enabled || !status.data.connected) return null;
  return (
    <>
      <button type="button" className={className} onClick={() => setOpen(true)}>
        <CalendarClock size={14} /> {label}
      </button>
      {open ? <LinksModal recordType={recordType} recordId={recordId} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

function LinksModal({ recordType, recordId, onClose }: { recordType?: "lead" | "contact"; recordId?: string; onClose: () => void }) {
  const links = useApi<CalendlyLinks>(`/calendly/links${query({ record_type: recordId ? recordType : null, record_id: recordId ?? null })}`);
  const data = links.data;
  return (
    <Modal
      open
      onClose={onClose}
      title="Calendly scheduling links"
      description={data?.person?.email ? `Links fill in ${data.person.name || data.person.email}'s name and email when they book.` : "Share a link so people can book time with you."}
    >
      {links.error ? <Alert>{links.error}</Alert> : null}
      {!data && !links.error ? <LoadingRows rows={4} /> : null}
      {data ? (
        <div className="ui-stack" style={{ gap: 14 }}>
          {data.scope !== "mine" ? (
            <Alert tone="info">
              {data.scope === "organization"
                ? "Your AIVA email doesn't match a Calendly user, so these are your organization's event types."
                : "These are the connected Calendly account's event types."}
            </Alert>
          ) : null}
          {data.items.length ? (
            <ul className="idoc-event-types">
              {data.items.map((item) => (
                <li key={item.uri}>
                  <div className="idoc-event-type-head">
                    <span className="idoc-event-dot" style={{ background: item.color || "var(--primary)" }} aria-hidden />
                    <strong>{item.name}</strong>
                    <span className="ui-muted ui-small">
                      {item.duration ? `${item.duration} min` : ""}
                      {item.owner && data.scope !== "mine" ? ` · ${item.owner}` : ""}
                    </span>
                    <a className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" href={item.scheduling_url} target="_blank" rel="noopener noreferrer" aria-label={`Open ${item.name}`}>
                      <ExternalLink size={14} />
                    </a>
                  </div>
                  <CopyField value={item.share_url || item.scheduling_url} label={`Copy the ${item.name} link`} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              No active event types. Create one in Calendly, then come back.
            </p>
          )}
          {data.scheduling_url ? (
            <p className="ui-small" style={{ margin: 0 }}>
              Your booking page:{" "}
              <a href={data.scheduling_url} target="_blank" rel="noopener noreferrer">
                {data.scheduling_url}
              </a>
            </p>
          ) : null}
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            Bookings show up in <Link href="/meetings">Meetings</Link> and on the person&apos;s record.
          </p>
        </div>
      ) : null}
    </Modal>
  );
}
