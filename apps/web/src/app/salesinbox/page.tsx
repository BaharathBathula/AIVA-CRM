"use client";

import Link from "next/link";
import { Building2, Handshake, Inbox, Mail, Search, Target } from "lucide-react";
import { useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Alert, Badge, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { dateTime, relativeTime } from "@/lib/format";
import { useApi, useDebounced } from "@/lib/use-api";

interface InboxItem {
  id: string;
  subject: string;
  snippet: string | null;
  last_message_at: string | null;
  participants: string[];
  message_count: number;
  unread: number;
  record: { type: string; label: string | null; href: string } | null;
}

type Column = "deals" | "contacts" | "leads" | "others";

const COLUMNS: { key: Column; label: string; icon: React.ReactNode; hint: string }[] = [
  { key: "deals", label: "Deals", icon: <Handshake size={16} />, hint: "Threads about an open or won deal" },
  { key: "contacts", label: "Contacts", icon: <Building2 size={16} />, hint: "Customers and their companies" },
  { key: "leads", label: "Leads", icon: <Target size={16} />, hint: "People you're still qualifying" },
  { key: "others", label: "Others", icon: <Inbox size={16} />, hint: "Not linked to anything in AIVA" },
];

interface ThreadMessage {
  id: string;
  direction: string;
  subject: string;
  from_address: string;
  from_name: string | null;
  body_text: string | null;
  snippet: string | null;
  occurred_at: string;
}

export default function SalesInboxPage() {
  return (
    <AppShell permission="records.read" what="SalesInbox">
      <SalesInbox />
    </AppShell>
  );
}

function SalesInbox() {
  const [search, setSearch] = useState("");
  const debounced = useDebounced(search);
  const inbox = useApi<{ columns: Record<Column, InboxItem[]>; counts: Record<Column, number>; unread: Record<Column, number> }>(
    `/salesinbox${debounced.trim() ? `?search=${encodeURIComponent(debounced.trim())}` : ""}`
  );
  const [open, setOpen] = useState<InboxItem | null>(null);

  return (
    <>
      <PageHeader
        title="SalesInbox"
        description="Your email sorted by who it's with, so customer and deal conversations come first."
        actions={
          <Link className="ui-btn" href="/email">
            <Mail size={15} /> Full inbox
          </Link>
        }
      />
      <div className="ui-toolbar">
        <div className="ui-search">
          <Search size={15} />
          <input className="ui-input sm" placeholder="Search subject or text" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search email" />
        </div>
      </div>
      {inbox.error ? <Alert>{inbox.error}</Alert> : null}
      {inbox.loading && !inbox.data ? (
        <div className="ui-card">
          <LoadingRows />
        </div>
      ) : (
        <div className="inbox-columns">
          {COLUMNS.map((column) => {
            const items = inbox.data?.columns[column.key] ?? [];
            return (
              <section key={column.key} className="inbox-column" aria-label={column.label}>
                <div className="inbox-column-head" title={column.hint}>
                  {column.icon}
                  <h2>{column.label}</h2>
                  <span className="ui-spacer" />
                  {inbox.data?.unread[column.key] ? <Badge tone="danger">{inbox.data.unread[column.key]} new</Badge> : null}
                  <span className="ui-muted ui-small">{items.length}</span>
                </div>
                <div className="inbox-column-list">
                  {items.length ? (
                    items.map((item) => (
                      <button key={item.id} type="button" className={`inbox-card${item.unread ? " unread" : ""}`} onClick={() => setOpen(item)}>
                        <span className="subject">{item.subject}</span>
                        {item.snippet ? <span className="inbox-snippet">{item.snippet}</span> : null}
                        <span className="foot">
                          {item.record?.label ? <span className="ui-truncate">{item.record.label}</span> : <span className="ui-truncate">{item.participants[0] ?? ""}</span>}
                          <span className="ui-spacer" />
                          {item.message_count > 1 ? <span>{item.message_count}</span> : null}
                          <span>{relativeTime(item.last_message_at)}</span>
                        </span>
                      </button>
                    ))
                  ) : (
                    <p className="ui-muted ui-small" style={{ padding: "12px 10px", margin: 0 }}>
                      {column.hint}. Nothing here yet.
                    </p>
                  )}
                </div>
              </section>
            );
          })}
        </div>
      )}
      <ThreadModal item={open} onClose={() => setOpen(null)} />
    </>
  );
}

function ThreadModal({ item, onClose }: { item: InboxItem | null; onClose: () => void }) {
  const messages = useApi<ThreadMessage[]>(item ? `/email/threads/${item.id}/messages` : null);
  return (
    <Modal
      open={Boolean(item)}
      size="lg"
      title={item?.subject ?? ""}
      description={item?.participants.join(", ")}
      onClose={onClose}
      footer={
        <>
          {item?.record ? (
            <Link className="ui-btn" href={item.record.href} style={{ marginRight: "auto" }}>
              Open {item.record.label}
            </Link>
          ) : null}
          <Link className="ui-btn ui-btn-primary" href="/email">
            Reply in Email
          </Link>
        </>
      }
    >
      {messages.loading && !messages.data ? (
        <LoadingRows rows={4} />
      ) : messages.data?.length ? (
        <div className="ui-thread">
          {messages.data.map((m) => (
            <div key={m.id} className="ui-message">
              <div className="bubble">
                <div className="meta">
                  <strong>{m.from_name || m.from_address}</strong>
                  <span>{m.direction === "outbound" ? "Sent" : "Received"}</span>
                  <span>{dateTime(m.occurred_at)}</span>
                </div>
                <div className="text">{m.body_text || m.snippet || ""}</div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="ui-muted">{item?.snippet || "No messages in this thread yet."}</p>
      )}
    </Modal>
  );
}
