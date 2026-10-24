"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, Ban, Link2, MessageSquarePlus, RefreshCw, Search, Settings2, ShieldCheck, Unlink } from "lucide-react";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import {
  ChannelChip,
  Composer,
  MessageList,
  prettyNumber,
  useStickToBottom,
  type ChannelKey,
  type ChannelMessage,
  type ChannelOption,
  type OptOut,
  type RecordRef,
  type SendResult,
} from "@/components/integrations/record/messaging-kit";
import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { RecordSearch, type FoundRecord } from "@/components/record-search";
import { Alert, Badge, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, query, useApi, useDebounced } from "@/lib/use-api";

type Conversation = {
  number: string;
  pretty: string;
  last_at: string;
  unread: number;
  total: number;
  channels: ChannelKey[];
  last: ChannelMessage | null;
  record: RecordRef | null;
  opted_out: boolean;
};

type ConversationList = { items: Conversation[]; more: boolean; unread_total: number };

type Thread = {
  number: string;
  pretty: string;
  record: RecordRef | null;
  opted_out: OptOut;
  whatsapp_window_until: string | null;
  messages: ChannelMessage[];
  more: boolean;
  channels: ChannelOption[];
};

type Filter = "all" | "unread" | "sms" | "whatsapp";

const POLL_MS = 15_000;

export default function MessagesPage() {
  return (
    <FeaturePage feature="messaging" permission="records.read" what="messages">
      <Suspense fallback={null}>
        <Messages />
      </Suspense>
    </FeaturePage>
  );
}

/** Refetch while the tab is visible. */
function usePolling(callback: () => void, active: boolean) {
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") callback();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [callback, active]);
}

function Messages() {
  const params = useSearchParams();
  const router = useRouter();
  const { can } = useSession();
  const [term, setTerm] = useState("");
  const search = useDebounced(term, 300);
  const [filter, setFilter] = useState<Filter>("all");
  const [active, setActive] = useState<string | null>(params.get("number"));
  const [pendingRecord, setPendingRecord] = useState<{ type: string; id: string } | null>(null);
  const [composing, setComposing] = useState(false);

  const listPath = `/messaging/conversations${query({
    search: search.trim() || null,
    unread: filter === "unread" ? true : null,
    channel: filter === "sms" || filter === "whatsapp" ? filter : null,
  })}`;
  const list = useApi<ConversationList>(listPath);
  const options = useApi<{ channels: ChannelOption[]; can_send: boolean; can_manage: boolean }>("/messaging/channels");
  const listReload = list.reload;
  const reloadList = useCallback(() => void listReload(), [listReload]);
  usePolling(reloadList, true);

  useEffect(() => {
    const number = params.get("number");
    if (number) setActive(number);
  }, [params]);

  function open(number: string, record?: { type: string; id: string } | null) {
    setActive(number);
    setPendingRecord(record ?? null);
    router.replace(`/integrations/messages?number=${encodeURIComponent(number)}`);
  }

  const items = list.data?.items ?? [];
  const noChannels = options.data && !options.data.channels.some((c) => c.ready);

  return (
    <div className="ui-stack" style={{ gap: 14 }}>
      <PageHeader
        title="Messages"
        description="Texts and WhatsApp messages with your contacts, leads and accounts. Replies land here and on the record."
        actions={
          <>
            {options.data?.can_manage ? (
              <Link className="ui-btn" href="/integrations">
                <Settings2 size={15} /> Channels
              </Link>
            ) : null}
            {can("records.write") ? (
              <button type="button" className="ui-btn ui-btn-primary" onClick={() => setComposing(true)} disabled={Boolean(noChannels)}>
                <MessageSquarePlus size={15} /> New message
              </button>
            ) : null}
          </>
        }
      />

      {noChannels ? (
        <Alert tone="info">
          No messaging channel is on yet.{" "}
          {options.data?.can_manage ? (
            <>
              Turn on <Link href="/integrations/twilio_sms">Twilio SMS</Link> or <Link href="/integrations/whatsapp">WhatsApp</Link> to start texting.
            </>
          ) : (
            "Ask a workspace admin to turn on Twilio SMS or WhatsApp."
          )}
        </Alert>
      ) : null}

      <div className={`imsg-shell${active ? " has-thread" : ""}`}>
        <aside className="imsg-side" aria-label="Conversations">
          <div className="imsg-side-head">
            <label className="imsg-search">
              <Search size={15} aria-hidden />
              <input value={term} onChange={(event) => setTerm(event.target.value)} placeholder="Search name, number or text" aria-label="Search conversations" />
            </label>
            <div className="imsg-filters" role="tablist" aria-label="Show">
              {(
                [
                  ["all", "All"],
                  ["unread", `Unread${list.data?.unread_total ? ` · ${list.data.unread_total}` : ""}`],
                  ["sms", "SMS"],
                  ["whatsapp", "WhatsApp"],
                ] as [Filter, string][]
              ).map(([value, label]) => (
                <button key={value} type="button" role="tab" aria-selected={filter === value} className={`int-chip${filter === value ? " on" : ""}`} onClick={() => setFilter(value)}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="imsg-side-list">
            {list.error ? <Alert>{list.error}</Alert> : null}
            {!list.data ? (
              <LoadingRows rows={6} />
            ) : items.length ? (
              items.map((item) => (
                <button key={item.number} type="button" className={`imsg-item${item.unread ? " unread" : ""}`} aria-current={active === item.number} onClick={() => open(item.number)}>
                  <div className="imsg-item-top">
                    <span className="imsg-item-name">{item.record?.label ?? item.pretty}</span>
                    <span className="imsg-item-time">{relativeTime(item.last_at)}</span>
                  </div>
                  <div className="imsg-item-bottom">
                    <span className="imsg-item-preview">
                      {item.last?.direction === "outbound" ? "You: " : ""}
                      {item.last?.body || (item.last?.media.length ? "Attachment" : "")}
                    </span>
                    {item.unread ? <span className="chat-unread">{item.unread}</span> : null}
                  </div>
                  <div className="imsg-item-tags">
                    {item.channels.map((channel) => (
                      <ChannelChip key={channel} channel={channel} />
                    ))}
                    {item.record ? <span className="ui-muted ui-small">{item.pretty}</span> : null}
                    {item.opted_out ? <Badge tone="danger">Opted out</Badge> : null}
                  </div>
                </button>
              ))
            ) : (
              <EmptyState title={search || filter !== "all" ? "No conversations match" : "No messages yet"} message={search || filter !== "all" ? "Try another search or filter." : "Texts you send and receive appear here."} />
            )}
          </div>
        </aside>

        {active ? (
          <ThreadView
            key={active}
            number={active}
            pendingRecord={pendingRecord}
            onResolved={(normalized) => open(normalized, pendingRecord)}
            onBack={() => {
              setActive(null);
              router.replace("/integrations/messages");
            }}
            onChanged={reloadList}
          />
        ) : (
          <section className="imsg-main imsg-placeholder">
            <EmptyState title="Pick a conversation" message="Or start a new message to a contact, lead or any number." />
          </section>
        )}
      </div>

      {composing ? (
        <NewMessage
          onClose={() => setComposing(false)}
          onOpen={(number, record) => {
            setComposing(false);
            open(number, record);
          }}
        />
      ) : null}
    </div>
  );
}

function ThreadView({
  number,
  pendingRecord,
  onResolved,
  onBack,
  onChanged,
}: {
  number: string;
  pendingRecord: { type: string; id: string } | null;
  /** The number as stored (E.164) when it was typed differently. */
  onResolved: (number: string) => void;
  onBack: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { can } = useSession();
  const thread = useApi<Thread>(`/messaging/thread?number=${encodeURIComponent(number)}`);
  const [olderBusy, setOlderBusy] = useState(false);
  const [linking, setLinking] = useState(false);
  const [confirmOptOut, setConfirmOptOut] = useState(false);
  const [busy, setBusy] = useState(false);
  const messages = thread.data?.messages ?? [];
  const lastId = messages.length ? messages[messages.length - 1].id : "";
  const scroller = useStickToBottom(lastId);
  const setThread = thread.setData;
  const path = `/messaging/thread?number=${encodeURIComponent(number)}`;
  // Poll for new messages without dropping earlier pages the person loaded.
  const refresh = useCallback(async () => {
    try {
      const latest = await api.get<Thread>(path);
      setThread((current) => {
        if (!current) return latest;
        const ids = new Set(latest.messages.map((m) => m.id));
        const earlier = current.messages.filter((m) => !ids.has(m.id) && (!latest.messages.length || m.created_at < latest.messages[0].created_at));
        return { ...latest, messages: [...earlier, ...latest.messages], more: earlier.length ? current.more : latest.more };
      });
    } catch {
      // Keep what's on screen; the next poll tries again.
    }
  }, [path, setThread]);
  const poll = useCallback(() => void refresh(), [refresh]);
  usePolling(poll, true);

  const resolved = thread.data?.number;
  useEffect(() => {
    if (resolved && resolved !== number) onResolved(resolved);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resolved, number]);

  useEffect(() => {
    // Reading a thread marks it read on the server; refresh the list's counts.
    if (thread.data) onChanged();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [Boolean(thread.data)]);

  async function older() {
    if (!messages.length) return;
    setOlderBusy(true);
    try {
      const page = await api.get<Thread>(`/messaging/thread?number=${encodeURIComponent(number)}&before=${messages[0].id}&mark_read=false`);
      thread.setData((current) => (current ? { ...current, messages: [...page.messages, ...current.messages], more: page.more } : current));
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setOlderBusy(false);
    }
  }

  function appended(message: ChannelMessage) {
    thread.setData((current) => (current ? { ...current, messages: [...current.messages, message] } : current));
    onChanged();
  }

  async function retry(message: ChannelMessage) {
    try {
      const result = await api.post<SendResult>(`/messaging/messages/${message.id}/retry`);
      appended(result.message);
      if (!result.ok) toast(result.error ?? "Still not delivered.", "danger");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function link(record: { type: string; id: string } | null) {
    setBusy(true);
    try {
      const meta = await api.post<Omit<Thread, "messages" | "more" | "channels">>("/messaging/thread/link", {
        number,
        record_type: record?.type ?? null,
        record_id: record?.id ?? null,
      });
      thread.setData((current) => (current ? { ...current, ...meta } : current));
      toast(record ? "Conversation linked." : "Conversation unlinked.");
      setLinking(false);
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function setOptOut(optedOut: boolean) {
    setBusy(true);
    try {
      const result = await api.post<{ opted_out: OptOut }>("/messaging/opt-out", { number, opted_out: optedOut });
      thread.setData((current) => (current ? { ...current, opted_out: result.opted_out } : current));
      toast(optedOut ? "AIVA won't message this number." : "Messages to this number are allowed again.");
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
      setConfirmOptOut(false);
    }
  }

  const data = thread.data;
  const record = data?.record ?? null;
  const linkedRecord = record && !record.suggested ? record : null;

  return (
    <section className="imsg-main" aria-label={`Conversation with ${prettyNumber(number)}`}>
      <header className="imsg-head">
        <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm imsg-back" onClick={onBack} aria-label="Back to conversations">
          <ArrowLeft size={16} />
        </button>
        <div style={{ minWidth: 0, flex: 1 }}>
          <h2 className="ui-truncate">{linkedRecord?.label ?? prettyNumber(number)}</h2>
          <p>
            {linkedRecord ? (
              <>
                {prettyNumber(number)} · <Link href={linkedRecord.href}>{linkedRecord.kind}</Link>
              </>
            ) : record?.suggested ? (
              <>
                Looks like <Link href={record.href}>{record.label}</Link> ({record.kind.toLowerCase()}).{" "}
                {can("records.write") ? (
                  <button type="button" className="imsg-linkish" disabled={busy} onClick={() => void link({ type: record.type, id: record.id })}>
                    Link it
                  </button>
                ) : null}
              </>
            ) : (
              "Not linked to a record"
            )}
          </p>
        </div>
        <div className="ui-actions">
          {can("records.write") ? (
            <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setLinking(true)}>
              <Link2 size={14} /> {linkedRecord ? "Change link" : "Link to record"}
            </button>
          ) : null}
          {linkedRecord && can("records.write") ? (
            <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" title="Unlink" aria-label="Unlink from record" disabled={busy} onClick={() => void link(null)}>
              <Unlink size={14} />
            </button>
          ) : null}
          {data && can("records.write") ? (
            data.opted_out ? (
              can("data.privacy") ? (
                <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" disabled={busy} onClick={() => void setOptOut(false)}>
                  <ShieldCheck size={14} /> Allow messages
                </button>
              ) : null
            ) : (
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" disabled={busy} onClick={() => setConfirmOptOut(true)}>
                <Ban size={14} /> Opt out
              </button>
            )
          ) : null}
          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Refresh" title="Refresh" onClick={() => void refresh()}>
            <RefreshCw size={14} />
          </button>
        </div>
      </header>

      <div className="imsg-thread" ref={scroller}>
        {thread.error && !data ? <Alert>{thread.error}</Alert> : null}
        {!data ? (
          <LoadingRows rows={5} />
        ) : (
          <>
            {data.more ? (
              <button type="button" className="ui-btn ui-btn-sm" style={{ alignSelf: "center" }} disabled={olderBusy} onClick={() => void older()}>
                {olderBusy ? <span className="ui-spinner" /> : null} Earlier messages
              </button>
            ) : null}
            <MessageList messages={messages} onRetry={can("records.write") ? (m) => void retry(m) : undefined} empty="No messages with this number yet. Say hello below." />
          </>
        )}
      </div>

      {data && can("records.write") ? (
        <Composer
          number={data.number}
          channels={data.channels}
          whatsappWindowUntil={data.whatsapp_window_until}
          optedOut={data.opted_out}
          record={linkedRecord ? null : pendingRecord}
          onSent={appended}
        />
      ) : null}

      {linking ? (
        <Modal open title="Link to a record" description="Every message with this number will show on the record you pick." onClose={() => setLinking(false)}>
          <RecordSearch types={["contact", "lead", "account"]} placeholder="Search contacts, leads and accounts…" onPick={(found: FoundRecord) => void link({ type: found.type, id: found.id })} />
        </Modal>
      ) : null}
      <ConfirmDialog
        open={confirmOptOut}
        title="Stop messaging this number?"
        message={`${prettyNumber(number)} goes on the do-not-contact list. Nobody in the workspace can text or WhatsApp it until someone who handles privacy requests allows it again, or the person texts START.`}
        confirmLabel="Opt out"
        busy={busy}
        onConfirm={() => void setOptOut(true)}
        onClose={() => setConfirmOptOut(false)}
      />
    </section>
  );
}

type RecordNumbers = { numbers: { number: string; pretty: string; label: string; opted_out: OptOut }[] };

function NewMessage({ onClose, onOpen }: { onClose: () => void; onOpen: (number: string, record: { type: string; id: string } | null) => void }) {
  const toast = useToast();
  const [record, setRecord] = useState<FoundRecord | null>(null);
  const [numbers, setNumbers] = useState<RecordNumbers["numbers"] | null>(null);
  const [typed, setTyped] = useState("");
  const [loading, setLoading] = useState(false);

  async function pick(found: FoundRecord) {
    setRecord(found);
    setLoading(true);
    try {
      const data = await api.get<RecordNumbers>(`/messaging/records/${found.type}/${found.id}?limit=1`);
      setNumbers(data.numbers.filter((n) => n.number));
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setNumbers([]);
    } finally {
      setLoading(false);
    }
  }

  const typedValid = useMemo(() => typed.replace(/\D/g, "").length >= 8, [typed]);

  return (
    <Modal
      open
      title="New message"
      description="Pick a contact, lead or account, or type any number with its country code."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={!typedValid} onClick={() => onOpen(typed.trim(), null)}>
            Continue with this number
          </button>
        </>
      }
    >
      <div className="ui-stack" style={{ gap: 16 }}>
        <Field label="Record">
          <RecordSearch types={["contact", "lead", "account"]} placeholder="Search contacts, leads and accounts…" onPick={(found) => void pick(found)} />
        </Field>
        {record ? (
          <div className="imsg-pick">
            <strong>{record.title}</strong>
            {loading ? (
              <span className="ui-muted ui-small">Loading numbers…</span>
            ) : numbers && numbers.length ? (
              <div className="imsg-numbers">
                {numbers.map((n) => (
                  <button key={n.number} type="button" className="int-chip" disabled={Boolean(n.opted_out)} title={n.opted_out ? "Opted out" : undefined} onClick={() => onOpen(n.number, { type: record.type, id: record.id })}>
                    {n.label} · {n.pretty}
                  </button>
                ))}
              </div>
            ) : (
              <span className="ui-muted ui-small">This record has no phone number. Type one below.</span>
            )}
          </div>
        ) : null}
        <Field label="Or a phone number" hint="Include the country code, like +44 7700 900123. Numbers without one use the workspace's default country.">
          <input className="ui-input" inputMode="tel" value={typed} onChange={(event) => setTyped(event.target.value)} placeholder="+1 214 555 0100" />
        </Field>
      </div>
    </Modal>
  );
}
