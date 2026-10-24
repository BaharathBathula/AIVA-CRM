"use client";

import Link from "next/link";
import { Pause, Play, Plus, RefreshCcw, Send, Trash2, Webhook } from "lucide-react";
import { useMemo, useState } from "react";

import { CopyField } from "@/components/copy-field";
import { useToast } from "@/components/providers";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, type Tone } from "@/components/ui";
import { dateTime, relativeTime } from "@/lib/format";
import { api, errorMessage, query, useApi } from "@/lib/use-api";

/* Shapes from apps/api/app/api/routes/int_leads_automation.py */

export type HookProvider = "zapier" | "make" | "n8n" | "custom";

export type WebhookEvent = { key: string; object: string; group: string; label: string; description: string };

export type WebhookEvents = {
  items: WebhookEvent[];
  any: string;
  providers: { key: HookProvider; name: string }[];
  can_manage: boolean;
};

export type WebhookSubscription = {
  id: string;
  provider: HookProvider;
  provider_name: string;
  event: string;
  event_label: string;
  target_url: string;
  target_host: string | null;
  label: string | null;
  source: "manual" | "rest_hook";
  is_active: boolean;
  status: "active" | "paused";
  created_by: string | null;
  via_api_key: boolean;
  last_delivery_at: string | null;
  last_status: number | null;
  consecutive_failures: number;
  created_at: string;
};

export type WebhookDelivery = {
  id: string;
  subscription_id: string;
  provider: HookProvider | null;
  provider_name: string | null;
  target_host: string | null;
  subscription_label: string | null;
  event: string;
  event_label: string;
  status: "pending" | "delivered" | "failed";
  attempts: number;
  response_status: number | null;
  error: string | null;
  next_attempt_at: string | null;
  delivered_at: string | null;
  created_at: string;
  test: boolean;
  record_label: string | null;
  payload?: Record<string, unknown>;
};

const DELIVERY_TONE: Record<WebhookDelivery["status"], Tone> = { delivered: "success", failed: "danger", pending: "warning" };
const DELIVERY_LABEL: Record<WebhookDelivery["status"], string> = { delivered: "Delivered", failed: "Failed", pending: "Retrying" };
export const PROVIDER_LABEL: Record<HookProvider, string> = { zapier: "Zapier", make: "Make", n8n: "n8n", custom: "Other" };

export const URL_HINT: Record<HookProvider, string> = {
  zapier: "The Catch Hook URL from “Webhooks by Zapier” (https://hooks.zapier.com/hooks/catch/…).",
  make: "The address of a Custom webhook in Make (https://hook.<region>.make.com/…).",
  n8n: "The Production URL of an n8n Webhook node (…/webhook/<path>). Activate the workflow first.",
  custom: "Any https URL that accepts JSON POST requests.",
};

/** Workspace's event catalog (and whether this person may manage webhooks). */
export function useWebhookEvents() {
  return useApi<WebhookEvents>("/integration-webhooks/events");
}

function groupEvents(items: WebhookEvent[]) {
  const groups: { name: string; events: WebhookEvent[] }[] = [];
  for (const event of items) {
    const group = groups.find((g) => g.name === event.group);
    if (group) group.events.push(event);
    else groups.push({ name: event.group, events: [event] });
  }
  return groups;
}

// ---------------------------------------------------------------- Secret shown once

export function SecretOnce({ secret, onClose, title = "Signing secret" }: { secret: string | null; onClose: () => void; title?: string }) {
  return (
    <Modal
      open={Boolean(secret)}
      title={title}
      description="Copy it now. For your security it won't be shown again."
      onClose={onClose}
      footer={
        <button type="button" className="ui-btn ui-btn-primary" onClick={onClose}>
          Done
        </button>
      }
    >
      <div className="ui-stack">
        {secret ? <CopyField value={secret} label="Copy the signing secret" /> : null}
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          AIVA signs every request with it. The receiver can check the <code>X-AIVA-Signature</code> header to be sure the event came from AIVA. It&apos;s optional: Zapier, Make and n8n work without checking it.
        </p>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- Add / edit

function AddWebhookModal({
  open,
  provider,
  events,
  onClose,
  onCreated,
}: {
  open: boolean;
  provider?: HookProvider;
  events: WebhookEvents | null;
  onClose: () => void;
  onCreated: (secret: string) => void;
}) {
  const [chosenProvider, setChosenProvider] = useState<HookProvider>(provider ?? "zapier");
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");
  const [picked, setPicked] = useState<string[]>(["lead.created"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const groups = useMemo(() => groupEvents(events?.items ?? []), [events]);
  const every = picked.includes("*");
  const target = provider ?? chosenProvider;

  function toggle(key: string, on: boolean) {
    setPicked((current) => (on ? [...current.filter((k) => k !== "*"), key] : current.filter((k) => k !== key)));
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ secret: string }>("/integration-webhooks/subscriptions", { provider: target, events: picked, target_url: url.trim(), label: label.trim() || null });
      setUrl("");
      setLabel("");
      setPicked(["lead.created"]);
      onCreated(result.secret);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      size="lg"
      title={`Add a webhook${provider ? ` for ${PROVIDER_LABEL[provider]}` : ""}`}
      description="AIVA posts each chosen event to this URL as JSON, within about a minute."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !url.trim() || !picked.length} onClick={() => void save()}>
            {busy ? <span className="ui-spinner" /> : null} Add webhook
          </button>
        </>
      }
    >
      <div className="ui-stack">
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-form-grid">
          {!provider ? (
            <Field label="Sends to">
              <select className="ui-select" value={chosenProvider} onChange={(event) => setChosenProvider(event.target.value as HookProvider)}>
                {(events?.providers ?? []).map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.name}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
          <Field label="Name" hint="Optional, to tell webhooks apart." className={provider ? "span-2" : undefined}>
            <input className="ui-input" value={label} onChange={(event) => setLabel(event.target.value)} placeholder="New leads to Google Sheets" maxLength={200} />
          </Field>
          <Field label="Webhook URL" required hint={URL_HINT[target]} className="span-2">
            <input className="ui-input ui-mono" type="url" inputMode="url" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://" autoComplete="off" />
          </Field>
        </div>
        <fieldset className="ila-events">
          <legend>Events</legend>
          <label className="ui-check ila-every">
            <input type="checkbox" checked={every} onChange={(event) => setPicked(event.target.checked ? ["*"] : ["lead.created"])} />
            <span>
              <strong>Every event</strong> <span className="ui-muted ui-small">One subscription that receives all of them; use the event field to tell them apart.</span>
            </span>
          </label>
          <div className="ila-event-groups">
            {groups.map((group) => (
              <div key={group.name} className="ila-event-group">
                <div className="ila-event-group-name">{group.name}</div>
                {group.events.map((event) => (
                  <label key={event.key} className="ui-check" title={event.description}>
                    <input type="checkbox" disabled={every} checked={every || picked.includes(event.key)} onChange={(e) => toggle(event.key, e.target.checked)} />
                    <span>{event.label}</span>
                  </label>
                ))}
              </div>
            ))}
          </div>
        </fieldset>
      </div>
    </Modal>
  );
}

function EditWebhookModal({ row, onClose, onSaved }: { row: WebhookSubscription | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [url, setUrl] = useState(row?.target_url ?? "");
  const [label, setLabel] = useState(row?.label ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!row) return null;
  async function save() {
    if (!row) return;
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/integration-webhooks/subscriptions/${row.id}`, { target_url: url.trim(), label: label.trim() });
      toast("Webhook saved.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      open
      title="Edit webhook"
      description={`${row.provider_name} · ${row.event_label}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !url.trim()} onClick={() => void save()}>
            {busy ? <span className="ui-spinner" /> : null} Save
          </button>
        </>
      }
    >
      <div className="ui-stack">
        {error ? <Alert>{error}</Alert> : null}
        <Field label="Name">
          <input className="ui-input" value={label} onChange={(event) => setLabel(event.target.value)} maxLength={200} />
        </Field>
        <Field label="Webhook URL" required hint={URL_HINT[row.provider]}>
          <input className="ui-input ui-mono" type="url" value={url} onChange={(event) => setUrl(event.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- Subscriptions

type SubscriptionList = { items: WebhookSubscription[]; providers_on: Record<string, boolean> };

export function SubscriptionsCard({
  provider,
  title = "Webhooks",
  enabled = true,
  onChanged,
}: {
  provider?: HookProvider;
  title?: string;
  /** False while the integration is switched off (adding is blocked, rows show as waiting). */
  enabled?: boolean;
  onChanged?: () => void;
}) {
  const toast = useToast();
  const events = useWebhookEvents();
  const [eventFilter, setEventFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [providerFilter, setProviderFilter] = useState("");
  const list = useApi<SubscriptionList>(
    `/integration-webhooks/subscriptions${query({ provider: provider ?? providerFilter, event: eventFilter, status: statusFilter })}`,
  );
  const [adding, setAdding] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [editing, setEditing] = useState<WebhookSubscription | null>(null);
  const [removing, setRemoving] = useState<WebhookSubscription | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const canManage = events.data?.can_manage ?? false;

  async function refresh() {
    await list.reload();
    onChanged?.();
  }

  async function act(row: WebhookSubscription, action: "test" | "pause" | "resume" | "rotate") {
    setBusy(`${row.id}:${action}`);
    try {
      if (action === "test") {
        const result = await api.post<{ ok: boolean; message: string }>(`/integration-webhooks/subscriptions/${row.id}/test`);
        toast(result.message, result.ok ? undefined : "danger");
      } else if (action === "rotate") {
        const result = await api.post<{ secret: string }>(`/integration-webhooks/subscriptions/${row.id}/rotate-secret`);
        setSecret(result.secret);
      } else {
        await api.patch(`/integration-webhooks/subscriptions/${row.id}`, { is_active: action === "resume" });
        toast(action === "resume" ? "Webhook resumed." : "Webhook paused.");
      }
      await refresh();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (!removing) return;
    setBusy(`${removing.id}:remove`);
    try {
      await api.del(`/integration-webhooks/subscriptions/${removing.id}`);
      toast("Webhook removed.");
      setRemoving(null);
      await refresh();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(null);
    }
  }

  const rows = list.data?.items ?? [];
  const off = (row: WebhookSubscription) => row.provider !== "custom" && list.data?.providers_on?.[row.provider] === false;

  return (
    <Card
      flush
      title={title}
      icon={<Webhook size={17} />}
      actions={
        canManage ? (
          <button type="button" className="ui-btn ui-btn-sm ui-btn-primary" disabled={!enabled} onClick={() => setAdding(true)} title={!enabled ? "Turn the integration on first" : undefined}>
            <Plus size={14} /> Add webhook
          </button>
        ) : null
      }
    >
      {!provider ? (
        <div className="ila-filters">
          <select className="ui-select sm" aria-label="Tool" value={providerFilter} onChange={(event) => setProviderFilter(event.target.value)}>
            <option value="">All tools</option>
            {(events.data?.providers ?? []).map((p) => (
              <option key={p.key} value={p.key}>
                {p.name}
              </option>
            ))}
          </select>
          <select className="ui-select sm" aria-label="Event" value={eventFilter} onChange={(event) => setEventFilter(event.target.value)}>
            <option value="">All events</option>
            <option value="*">Every event (catch-all)</option>
            {(events.data?.items ?? []).map((e) => (
              <option key={e.key} value={e.key}>
                {e.label}
              </option>
            ))}
          </select>
          <select className="ui-select sm" aria-label="Status" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">Active and paused</option>
            <option value="active">Active</option>
            <option value="paused">Paused</option>
          </select>
        </div>
      ) : null}
      {list.error ? (
        <div style={{ padding: 16 }}>
          <Alert>{list.error}</Alert>
        </div>
      ) : !list.data ? (
        <LoadingRows rows={3} />
      ) : rows.length ? (
        <div className="ui-table-wrap">
          <table className="ui-table ila-table">
            <thead>
              <tr>
                <th>Webhook</th>
                <th>Event</th>
                <th>Last delivery</th>
                <th>Status</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td className="cell-title">
                    <span className="ila-provider-chip">{row.provider_name}</span> {row.label ?? row.target_host}
                    <div className="ui-muted ui-small ui-mono ui-truncate ila-url" title={row.target_url}>
                      {row.target_url}
                    </div>
                    <div className="ui-muted ui-small">
                      {row.via_api_key || row.source === "rest_hook" ? "Subscribed through the API" : row.created_by ? `Added by ${row.created_by}` : "Added"} · {relativeTime(row.created_at)}
                    </div>
                  </td>
                  <td>{row.event === "*" ? <Badge tone="info">Every event</Badge> : row.event_label}</td>
                  <td className="ui-small">
                    {row.last_delivery_at ? (
                      <>
                        {relativeTime(row.last_delivery_at)}
                        <div className="ui-muted">{row.last_status ? `HTTP ${row.last_status}` : "No response"}</div>
                      </>
                    ) : (
                      <span className="ui-muted">Nothing sent yet</span>
                    )}
                  </td>
                  <td>
                    {off(row) ? (
                      <Badge tone="neutral">Waiting (integration off)</Badge>
                    ) : row.is_active ? (
                      <Badge tone={row.consecutive_failures ? "warning" : "success"} dot>
                        {row.consecutive_failures ? `Failing (${row.consecutive_failures})` : "Active"}
                      </Badge>
                    ) : (
                      <Badge tone="neutral" dot>
                        Paused
                      </Badge>
                    )}
                  </td>
                  <td>
                    {canManage ? (
                      <div className="ila-row-actions">
                        <button type="button" className="ui-btn ui-btn-sm" disabled={Boolean(busy) || !row.is_active || off(row)} onClick={() => void act(row, "test")} title="Send a sample event now">
                          {busy === `${row.id}:test` ? <span className="ui-spinner" /> : <Send size={13} />} Test
                        </button>
                        <button
                          type="button"
                          className="ui-btn ui-btn-sm ui-btn-ghost ui-btn-icon"
                          disabled={Boolean(busy)}
                          onClick={() => void act(row, row.is_active ? "pause" : "resume")}
                          aria-label={row.is_active ? "Pause" : "Resume"}
                          title={row.is_active ? "Pause" : "Resume"}
                        >
                          {row.is_active ? <Pause size={14} /> : <Play size={14} />}
                        </button>
                        <details className="ila-more">
                          <summary className="ui-btn ui-btn-sm ui-btn-ghost" aria-label="More actions">
                            More
                          </summary>
                          <div className="ila-menu" role="menu">
                            <button type="button" role="menuitem" onClick={() => setEditing(row)}>
                              Edit URL or name
                            </button>
                            <button type="button" role="menuitem" onClick={() => void act(row, "rotate")}>
                              <RefreshCcw size={13} /> New signing secret
                            </button>
                            <button type="button" role="menuitem" className="danger" onClick={() => setRemoving(row)}>
                              <Trash2 size={13} /> Remove
                            </button>
                          </div>
                        </details>
                      </div>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState
          icon={<Webhook size={20} />}
          title="No webhooks yet"
          message={canManage ? "Add a webhook to send AIVA events to your automations." : "An admin can add webhooks that send AIVA events to your automations."}
        />
      )}
      <AddWebhookModal
        open={adding}
        provider={provider}
        events={events.data}
        onClose={() => setAdding(false)}
        onCreated={(value) => {
          setAdding(false);
          setSecret(value);
          toast("Webhook added.");
          void refresh();
        }}
      />
      <SecretOnce secret={secret} onClose={() => setSecret(null)} />
      {editing ? (
        <EditWebhookModal
          key={editing.id}
          row={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void refresh();
          }}
        />
      ) : null}
      <ConfirmDialog
        open={Boolean(removing)}
        title="Remove this webhook?"
        message={removing ? `AIVA stops sending ${removing.event_label.toLowerCase()} events to ${removing.target_host}. Anything still queued for it is cancelled.` : ""}
        confirmLabel="Remove"
        busy={Boolean(busy)}
        onConfirm={() => void remove()}
        onClose={() => setRemoving(null)}
      />
    </Card>
  );
}

// ---------------------------------------------------------------- Deliveries

type DeliveryList = { items: WebhookDelivery[]; next_before: string | null };

export function DeliveriesCard({ provider, limit = 50, compact, title = "Deliveries" }: { provider?: HookProvider; limit?: number; compact?: boolean; title?: string }) {
  const toast = useToast();
  const events = useWebhookEvents();
  const [status, setStatus] = useState("");
  const [event, setEvent] = useState("");
  const [providerFilter, setProviderFilter] = useState("");
  const [extra, setExtra] = useState<WebhookDelivery[]>([]);
  const [cursor, setCursor] = useState<string | null | undefined>(undefined);
  const path = `/integration-webhooks/deliveries${query({ provider: provider ?? providerFilter, status, event, limit })}`;
  const list = useApi<DeliveryList>(path);
  const [open, setOpen] = useState<WebhookDelivery | null>(null);
  const [busy, setBusy] = useState(false);
  const rows = [...(list.data?.items ?? []), ...extra];
  const next = cursor === undefined ? list.data?.next_before : cursor;

  function reset(update: () => void) {
    setExtra([]);
    setCursor(undefined);
    update();
  }

  async function more() {
    if (!next) return;
    setBusy(true);
    try {
      const page = await api.get<DeliveryList>(`/integration-webhooks/deliveries${query({ provider: provider ?? providerFilter, status, event, limit, before: next })}`);
      setExtra((current) => [...current, ...page.items]);
      setCursor(page.next_before);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function show(row: WebhookDelivery) {
    try {
      setOpen(await api.get<WebhookDelivery>(`/integration-webhooks/deliveries/${row.id}`));
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function redeliver(row: WebhookDelivery) {
    setBusy(true);
    try {
      const result = await api.post<{ ok: boolean; message: string; delivery: WebhookDelivery }>(`/integration-webhooks/deliveries/${row.id}/redeliver`);
      toast(result.message, result.ok ? undefined : "danger");
      setOpen(null);
      reset(() => void list.reload());
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      flush
      title={title}
      actions={
        <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" onClick={() => reset(() => void list.reload())} aria-label="Refresh deliveries">
          <RefreshCcw size={14} /> Refresh
        </button>
      }
    >
      {!compact ? (
        <div className="ila-filters">
          {!provider ? (
            <select className="ui-select sm" aria-label="Tool" value={providerFilter} onChange={(e) => reset(() => setProviderFilter(e.target.value))}>
              <option value="">All tools</option>
              {(events.data?.providers ?? []).map((p) => (
                <option key={p.key} value={p.key}>
                  {p.name}
                </option>
              ))}
            </select>
          ) : null}
          <select className="ui-select sm" aria-label="Status" value={status} onChange={(e) => reset(() => setStatus(e.target.value))}>
            <option value="">Any status</option>
            <option value="delivered">Delivered</option>
            <option value="pending">Retrying</option>
            <option value="failed">Failed</option>
          </select>
          <select className="ui-select sm" aria-label="Event" value={event} onChange={(e) => reset(() => setEvent(e.target.value))}>
            <option value="">All events</option>
            {(events.data?.items ?? []).map((e) => (
              <option key={e.key} value={e.key}>
                {e.label}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      {list.error ? (
        <div style={{ padding: 16 }}>
          <Alert>{list.error}</Alert>
        </div>
      ) : !list.data ? (
        <LoadingRows rows={3} />
      ) : rows.length ? (
        <>
          <div className="ui-table-wrap">
            <table className="ui-table ila-table">
              <thead>
                <tr>
                  <th>Event</th>
                  <th>Sent to</th>
                  <th>Status</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className="ila-click" onClick={() => void show(row)} tabIndex={0} onKeyDown={(e) => (e.key === "Enter" ? void show(row) : undefined)}>
                    <td className="cell-title">
                      {row.event_label}
                      {row.test ? (
                        <>
                          {" "}
                          <Badge tone="info">Test</Badge>
                        </>
                      ) : null}
                      {row.record_label ? <div className="ui-muted ui-small ui-truncate">{row.record_label}</div> : null}
                    </td>
                    <td className="ui-small">
                      {row.provider_name ? <span className="ila-provider-chip">{row.provider_name}</span> : null} {row.subscription_label ?? row.target_host}
                    </td>
                    <td>
                      <Badge tone={DELIVERY_TONE[row.status]} dot>
                        {DELIVERY_LABEL[row.status]}
                      </Badge>
                      <div className="ui-muted ui-small">
                        {row.response_status ? `HTTP ${row.response_status}` : row.error && row.status !== "delivered" ? "No response" : ""}
                        {row.attempts > 1 ? ` · ${row.attempts} tries` : ""}
                      </div>
                    </td>
                    <td className="ui-small ui-muted ui-nowrap">{relativeTime(row.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {next && !compact ? (
            <div style={{ padding: 12, textAlign: "center" }}>
              <button type="button" className="ui-btn ui-btn-sm" disabled={busy} onClick={() => void more()}>
                {busy ? <span className="ui-spinner" /> : null} Load more
              </button>
            </div>
          ) : null}
        </>
      ) : (
        <EmptyState title="No deliveries" message="Events sent to your webhooks appear here, with the receiver's response." />
      )}
      {compact && rows.length ? (
        <div className="ila-card-foot">
          <Link href="/integrations/webhooks?tab=deliveries">All deliveries</Link>
        </div>
      ) : null}
      <Modal
        open={Boolean(open)}
        size="lg"
        title={open ? open.event_label : ""}
        description={open ? `${open.provider_name ?? ""} · ${open.subscription_label ?? open.target_host ?? ""} · ${dateTime(open.created_at)}` : undefined}
        onClose={() => setOpen(null)}
        footer={
          open ? (
            <>
              <button type="button" className="ui-btn" onClick={() => setOpen(null)}>
                Close
              </button>
              <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void redeliver(open)}>
                {busy ? <span className="ui-spinner" /> : <Send size={14} />} Send again
              </button>
            </>
          ) : null
        }
      >
        {open ? (
          <div className="ui-stack">
            <div className="ila-kv">
              <div>
                <span>Status</span>
                <Badge tone={DELIVERY_TONE[open.status]} dot>
                  {DELIVERY_LABEL[open.status]}
                </Badge>
              </div>
              <div>
                <span>Attempts</span>
                {open.attempts}
              </div>
              <div>
                <span>Response</span>
                {open.response_status ? `HTTP ${open.response_status}` : "—"}
              </div>
              <div>
                <span>{open.status === "delivered" ? "Delivered" : "Next try"}</span>
                {open.status === "delivered" ? dateTime(open.delivered_at) : open.next_attempt_at ? dateTime(open.next_attempt_at) : "—"}
              </div>
            </div>
            {open.error ? <Alert tone={open.status === "pending" ? "warning" : "danger"}>{open.error}</Alert> : null}
            <Field label="Body sent">
              <pre className="code-block ila-payload">{JSON.stringify(open.payload ?? {}, null, 2)}</pre>
            </Field>
          </div>
        ) : null}
      </Modal>
    </Card>
  );
}

// ---------------------------------------------------------------- Signature help

const NODE_SNIPPET = `import crypto from "node:crypto";

// header: the X-AIVA-Signature value, body: the raw request body (string)
export function fromAiva(header, body, secret) {
  const parts = Object.fromEntries(header.split(",").map((p) => p.split("=")));
  if (Math.abs(Date.now() / 1000 - Number(parts.t)) > 300) return false;
  const expected = crypto.createHmac("sha256", secret).update(\`\${parts.t}.\${body}\`).digest("hex");
  return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(parts.v1 ?? ""));
}`;

const PYTHON_SNIPPET = `import hashlib, hmac, time

def from_aiva(header: str, body: bytes, secret: str) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    if abs(time.time() - int(parts["t"])) > 300:
        return False
    expected = hmac.new(secret.encode(), f"{parts['t']}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts.get("v1", ""))`;

export function SignatureCard() {
  const [lang, setLang] = useState<"node" | "python">("node");
  return (
    <Card title="Check that events came from AIVA">
      <div className="ui-stack" style={{ gap: 12 }}>
        <p className="ui-small" style={{ margin: 0, color: "var(--text-2)" }}>
          Every request carries <code>X-AIVA-Signature: t=&lt;time&gt;,v1=&lt;signature&gt;</code>: an HMAC-SHA256 of <code>&lt;time&gt;.&lt;raw body&gt;</code> made with the webhook&apos;s signing secret.
          Also sent: <code>X-AIVA-Event</code> and <code>X-AIVA-Delivery</code> (unique per delivery; a retry keeps the same id, so you can ignore repeats).
        </p>
        <div className="ila-seg" role="tablist" aria-label="Language">
          <button type="button" role="tab" aria-selected={lang === "node"} className={lang === "node" ? "on" : ""} onClick={() => setLang("node")}>
            Node.js
          </button>
          <button type="button" role="tab" aria-selected={lang === "python"} className={lang === "python" ? "on" : ""} onClick={() => setLang("python")}>
            Python
          </button>
        </div>
        <CopyField value={lang === "node" ? NODE_SNIPPET : PYTHON_SNIPPET} label="Copy the code" multiline />
      </div>
    </Card>
  );
}
