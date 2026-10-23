"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Archive, Hash, Link2, Lock, LogOut, MessageSquarePlus, Pencil, Plus, Send, Trash2, UserPlus, Users, X } from "lucide-react";
import { Fragment, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { RecordSearch, type FoundRecord } from "@/components/record-search";
import { Alert, Avatar, Field, LoadingRows, Modal } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";
import type { ChatChannel, ChatMessage, Member } from "@/lib/workspace";

export default function ChatPage() {
  return (
    <FeaturePage feature="team_chat">
      <Suspense>
        <Chat />
      </Suspense>
    </FeaturePage>
  );
}

const MENTION = /@\[([^\]]{1,120})\]\(user:([0-9a-f-]{36})\)/g;

function renderBody(body: string) {
  const parts: React.ReactNode[] = [];
  let last = 0;
  for (const match of body.matchAll(MENTION)) {
    if (match.index! > last) parts.push(body.slice(last, match.index));
    parts.push(
      <span key={match.index} className="mention">
        @{match[1]}
      </span>
    );
    last = match.index! + match[0].length;
  }
  parts.push(body.slice(last));
  return parts;
}

function dayLabel(value: string) {
  const d = new Date(value);
  const today = new Date();
  const yesterday = new Date(Date.now() - 86400000);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
}

function Chat() {
  const params = useSearchParams();
  const router = useRouter();
  const toast = useToast();
  const { session } = useSession();
  const channels = useApi<{ items: ChatChannel[]; unread_total: number }>("/chat/channels");
  const members = useApi<Member[]>("/meta/members");
  const [activeId, setActiveId] = useState<string | null>(params.get("c"));
  const [creating, setCreating] = useState(false);
  const [starting, setStarting] = useState(false);
  const list = channels.data?.items ?? [];

  const openDirect = useCallback(
    async (userId: string) => {
      try {
        const channel = await api.post<ChatChannel>("/chat/direct", { user_id: userId });
        setStarting(false);
        await channels.reload();
        setActiveId(channel.id);
      } catch (caught) {
        toast(errorMessage(caught), "danger");
      }
    },
    [channels, toast]
  );

  // Deep links: ?c=<channel> and ?dm=<user>.
  useEffect(() => {
    const dm = params.get("dm");
    if (dm) {
      void openDirect(dm);
      router.replace("/chat");
    }
    const c = params.get("c");
    if (c) setActiveId(c);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  useEffect(() => {
    if (!activeId && list.length) setActiveId((list.find((c) => c.name === "general") ?? list[0]).id);
  }, [activeId, list]);

  useEffect(() => {
    const timer = window.setInterval(() => void channels.reload(), 15000);
    return () => window.clearInterval(timer);
  }, [channels]);

  const active = list.find((c) => c.id === activeId) ?? null;
  const groups = [
    { label: "Channels", items: list.filter((c) => c.kind === "channel" && c.is_member && !c.is_archived) },
    { label: "Direct messages", items: list.filter((c) => c.kind === "direct") },
    { label: "More channels", items: list.filter((c) => c.kind === "channel" && !c.is_member && !c.is_archived) },
    { label: "Archived", items: list.filter((c) => c.kind === "channel" && c.is_archived && c.is_member) },
  ];

  return (
    <div className="chat-shell">
      <aside className="chat-side" aria-label="Conversations">
        <div className="chat-side-head">
          <strong style={{ fontSize: 16 }}>Chat</strong>
          <span className="ui-spacer" />
          <Link href="/people" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="People directory" title="People">
            <Users size={15} />
          </Link>
          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="New direct message" title="New message" onClick={() => setStarting(true)}>
            <MessageSquarePlus size={15} />
          </button>
          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="New channel" title="New channel" onClick={() => setCreating(true)}>
            <Plus size={15} />
          </button>
        </div>
        <div className="chat-side-list">
          {channels.loading && !channels.data ? <LoadingRows rows={5} /> : null}
          {groups.map((group) =>
            group.items.length ? (
              <Fragment key={group.label}>
                <div className="chat-group">{group.label}</div>
                {group.items.map((c) => (
                  <button key={c.id} type="button" className={`chat-item${c.unread ? " unread" : ""}`} aria-current={c.id === activeId} onClick={() => setActiveId(c.id)}>
                    {c.kind === "direct" ? <Avatar name={c.title} size={22} /> : c.is_private ? <Lock size={15} /> : <Hash size={15} />}
                    <span className="name">{c.kind === "channel" ? c.name : c.title}</span>
                    {c.unread ? <span className="chat-unread">{c.unread}</span> : null}
                  </button>
                ))}
              </Fragment>
            ) : null
          )}
        </div>
      </aside>
      {active ? (
        <Conversation key={active.id} channel={active} me={session?.user.id ?? ""} members={members.data ?? []} onChanged={() => void channels.reload()} onLeft={() => (setActiveId(null), void channels.reload())} />
      ) : (
        <section className="chat-main" style={{ display: "grid", placeItems: "center" }}>
          <p className="ui-muted">Pick a conversation.</p>
        </section>
      )}
      <NewChannel open={creating} members={members.data ?? []} me={session?.user.id ?? ""} onClose={() => setCreating(false)} onCreated={(c) => (setCreating(false), void channels.reload(), setActiveId(c.id))} />
      <Modal open={starting} title="New message" onClose={() => setStarting(false)}>
        <ul className="mini-list">
          {(members.data ?? []).map((m) => (
            <li key={m.id}>
              <Avatar name={m.full_name} size={28} />
              <div className="mini-main">
                <span className="mini-title">
                  {m.full_name}
                  {m.id === session?.user.id ? " (you)" : ""}
                </span>
                <span className="mini-sub">{m.email}</span>
              </div>
              <button type="button" className="ui-btn ui-btn-sm" onClick={() => void openDirect(m.id)}>
                Message
              </button>
            </li>
          ))}
        </ul>
      </Modal>
    </div>
  );
}

function Conversation({ channel, me, members, onChanged, onLeft }: { channel: ChatChannel; me: string; members: Member[]; onChanged: () => void; onLeft: () => void }) {
  const toast = useToast();
  const { can } = useSession();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [text, setText] = useState("");
  const [record, setRecord] = useState<FoundRecord | null>(null);
  const [linking, setLinking] = useState(false);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<ChatMessage | null>(null);
  const [mention, setMention] = useState<{ query: string; start: number } | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);
  const [sending, setSending] = useState(false);
  const since = useRef<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const atBottom = useRef(true);
  const memberIds = new Set(channel.member_ids);
  const mentionable = (channel.kind === "channel" && !channel.is_private ? members : members.filter((m) => memberIds.has(m.id))).filter((m) => m.id !== me);
  const matches = mention ? mentionable.filter((m) => m.full_name.toLowerCase().includes(mention.query.toLowerCase())).slice(0, 8) : [];

  const markRead = useCallback(() => {
    if (!channel.is_member) return;
    void api.post(`/chat/channels/${channel.id}/read`).then(() => window.dispatchEvent(new Event("aiva:chat-read"))).catch(() => undefined);
  }, [channel.id, channel.is_member]);

  const scrollDown = useCallback(() => {
    requestAnimationFrame(() => {
      if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
    });
  }, []);

  // First page, then poll for new and edited messages every few seconds.
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const page = await api.get<{ items: ChatMessage[]; has_more: boolean; server_time: string }>(`/chat/channels/${channel.id}/messages?limit=50`);
        if (!active) return;
        setMessages(page.items);
        setHasMore(page.has_more);
        since.current = page.server_time;
        scrollDown();
        markRead();
        onChanged();
      } catch (caught) {
        if (active) toast(errorMessage(caught), "danger");
      } finally {
        if (active) setLoading(false);
      }
    })();
    const timer = window.setInterval(async () => {
      if (!since.current || document.visibilityState !== "visible") return;
      try {
        const page = await api.get<{ items: ChatMessage[]; server_time: string }>(`/chat/channels/${channel.id}/messages?after=${encodeURIComponent(since.current)}`);
        since.current = page.server_time;
        if (!page.items.length) return;
        setMessages((current) => {
          const byId = new Map(current.map((m) => [m.id, m]));
          for (const m of page.items) byId.set(m.id, m);
          return [...byId.values()].sort((a, b) => a.created_at.localeCompare(b.created_at));
        });
        if (atBottom.current) scrollDown();
        markRead();
      } catch {
        // Try again on the next tick.
      }
    }, 4000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel.id]);

  async function older() {
    const first = messages[0];
    if (!first) return;
    const page = await api.get<{ items: ChatMessage[]; has_more: boolean }>(`/chat/channels/${channel.id}/messages?before=${encodeURIComponent(first.created_at)}&limit=50`);
    setMessages((current) => [...page.items, ...current]);
    setHasMore(page.has_more);
  }

  function onType(value: string, caret: number) {
    setText(value);
    const before = value.slice(0, caret);
    const at = before.lastIndexOf("@");
    if (at >= 0 && (at === 0 || /\s/.test(before[at - 1])) && !/[\s[\]]/.test(before.slice(at + 1)) && before.length - at <= 30) {
      setMention({ query: before.slice(at + 1), start: at });
      setMentionIndex(0);
    } else setMention(null);
  }

  function pickMention(member: Member) {
    if (!mention) return;
    const caret = box.current?.selectionStart ?? text.length;
    const next = `${text.slice(0, mention.start)}@[${member.full_name}](user:${member.id}) ${text.slice(caret)}`;
    setText(next);
    setMention(null);
    requestAnimationFrame(() => box.current?.focus());
  }

  async function send() {
    const body = text.trim();
    if (!body || sending) return;
    setSending(true);
    try {
      if (editing) {
        const saved = await api.patch<ChatMessage>(`/chat/messages/${editing.id}`, { body });
        setMessages((current) => current.map((m) => (m.id === saved.id ? saved : m)));
        setEditing(null);
      } else {
        const message = await api.post<ChatMessage>(`/chat/channels/${channel.id}/messages`, { body, record_type: record?.type ?? null, record_id: record?.id ?? null });
        setMessages((current) => (current.some((m) => m.id === message.id) ? current : [...current, message]));
        setRecord(null);
        scrollDown();
      }
      setText("");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setSending(false);
    }
  }

  async function remove(message: ChatMessage) {
    try {
      await api.del(`/chat/messages/${message.id}`);
      setMessages((current) => current.map((m) => (m.id === message.id ? { ...m, deleted: true, body: "", record: null } : m)));
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function join() {
    try {
      await api.post(`/chat/channels/${channel.id}/join`);
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function leave() {
    try {
      await api.post(`/chat/channels/${channel.id}/leave`);
      toast(`You left #${channel.name}.`);
      onLeft();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function archive() {
    try {
      await api.patch(`/chat/channels/${channel.id}`, { is_archived: !channel.is_archived });
      onChanged();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  const owner = channel.my_role === "owner" || can("admin.settings");
  const canPost = channel.is_member && !channel.is_archived;
  let lastDay = "";

  return (
    <section className="chat-main" aria-label={channel.title}>
      <header className="chat-head">
        {channel.kind === "direct" ? <Avatar name={channel.title} size={30} /> : channel.is_private ? <Lock size={18} /> : <Hash size={18} />}
        <div style={{ minWidth: 0, flex: 1 }}>
          <h2>{channel.kind === "channel" ? channel.name : channel.title}</h2>
          <p>{channel.topic ?? (channel.kind === "direct" ? "Direct message" : `${channel.member_count} member${channel.member_count === 1 ? "" : "s"}`)}</p>
        </div>
        {channel.kind === "channel" && channel.is_member ? (
          <>
            <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setAdding(true)}>
              <UserPlus size={14} /> Add people
            </button>
            {owner && channel.name !== "general" ? (
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => void archive()}>
                <Archive size={14} /> {channel.is_archived ? "Unarchive" : "Archive"}
              </button>
            ) : null}
            {channel.name !== "general" ? (
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => void leave()}>
                <LogOut size={14} /> Leave
              </button>
            ) : null}
          </>
        ) : null}
      </header>
      <div
        className="chat-messages"
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
      >
        {hasMore ? (
          <button type="button" className="ui-btn ui-btn-sm" style={{ alignSelf: "center" }} onClick={() => void older()}>
            Load earlier messages
          </button>
        ) : null}
        {loading ? <LoadingRows rows={4} /> : null}
        {!loading && !messages.length ? (
          <p className="ui-muted" style={{ textAlign: "center", marginTop: 40 }}>
            {channel.kind === "direct" ? "Start the conversation." : `This is the start of #${channel.name}.`}
          </p>
        ) : null}
        {messages.map((m) => {
          const day = dayLabel(m.created_at);
          const separator = day !== lastDay;
          lastDay = day;
          return (
            <Fragment key={m.id}>
              {separator ? <div className="chat-day">{day}</div> : null}
              <div className="chat-msg">
                <Avatar name={m.author} size={32} />
                <div className="body">
                  <div className="meta">
                    <strong>{m.author}</strong>
                    <span>{new Date(m.created_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>
                    {m.edited_at && !m.deleted ? <span>(edited)</span> : null}
                  </div>
                  {m.deleted ? (
                    <div className="text deleted">This message was deleted.</div>
                  ) : (
                    <>
                      <div className="text">{renderBody(m.body)}</div>
                      {m.record ? (
                        <Link href={m.record.href} className="chat-record">
                          <Link2 size={14} /> {m.record.label} <span>{m.record.kind}</span>
                        </Link>
                      ) : null}
                    </>
                  )}
                </div>
                {!m.deleted && (m.is_mine || can("admin.settings")) ? (
                  <div className="tools">
                    {m.is_mine ? (
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Edit message" onClick={() => (setEditing(m), setText(m.body), box.current?.focus())}>
                        <Pencil size={13} />
                      </button>
                    ) : null}
                    <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Delete message" onClick={() => void remove(m)}>
                      <Trash2 size={13} />
                    </button>
                  </div>
                ) : null}
              </div>
            </Fragment>
          );
        })}
      </div>
      {canPost ? (
        <div className="chat-compose">
          {editing ? (
            <div className="ui-row ui-small" style={{ gap: 8 }}>
              Editing your message
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => (setEditing(null), setText(""))}>
                Cancel
              </button>
            </div>
          ) : null}
          {record ? (
            <span className="record-chip" style={{ justifySelf: "start" }}>
              <Link2 size={13} /> {record.title}
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Remove link" onClick={() => setRecord(null)}>
                <X size={13} />
              </button>
            </span>
          ) : null}
          <div style={{ position: "relative" }}>
            {mention && matches.length ? (
              <div className="mention-menu" role="listbox" aria-label="Mention someone">
                {matches.map((m, i) => (
                  <button key={m.id} type="button" role="option" aria-selected={i === mentionIndex} onMouseDown={(e) => (e.preventDefault(), pickMention(m))}>
                    {m.full_name}
                  </button>
                ))}
              </div>
            ) : null}
            <textarea
              ref={box}
              className="ui-textarea"
              value={text}
              placeholder={channel.kind === "direct" ? `Message ${channel.title}` : `Message #${channel.name} — type @ to mention someone`}
              aria-label="Message"
              onChange={(e) => onType(e.target.value, e.target.selectionStart ?? e.target.value.length)}
              onKeyDown={(e) => {
                if (mention && matches.length) {
                  if (e.key === "ArrowDown") return e.preventDefault(), setMentionIndex((i) => (i + 1) % matches.length);
                  if (e.key === "ArrowUp") return e.preventDefault(), setMentionIndex((i) => (i - 1 + matches.length) % matches.length);
                  if (e.key === "Enter" || e.key === "Tab") return e.preventDefault(), pickMention(matches[mentionIndex]);
                  if (e.key === "Escape") return setMention(null);
                }
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
          </div>
          <div className="chat-compose-row">
            {!editing ? (
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setLinking(true)}>
                <Link2 size={14} /> Link a record
              </button>
            ) : null}
            <span className="ui-muted ui-small">Enter to send · Shift+Enter for a new line</span>
            <span className="ui-spacer" />
            <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" onClick={() => void send()} disabled={!text.trim() || sending}>
              <Send size={14} /> {editing ? "Save" : "Send"}
            </button>
          </div>
        </div>
      ) : channel.is_archived ? (
        <div className="chat-compose">
          <Alert tone="info">This channel is archived. Its history stays readable.</Alert>
        </div>
      ) : (
        <div className="chat-compose">
          <div className="ui-row" style={{ gap: 10 }}>
            <span className="ui-muted">You&apos;re viewing #{channel.name}.</span>
            <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" onClick={() => void join()}>
              Join channel
            </button>
          </div>
        </div>
      )}
      <Modal open={linking} title="Link a record" onClose={() => setLinking(false)}>
        <RecordSearch onPick={(r) => (setRecord(r), setLinking(false), box.current?.focus())} />
      </Modal>
      <AddPeople open={adding} channel={channel} members={members} onClose={() => setAdding(false)} onAdded={() => (setAdding(false), onChanged())} />
    </section>
  );
}

function NewChannel({ open, members, me, onClose, onCreated }: { open: boolean; members: Member[]; me: string; onClose: () => void; onCreated: (channel: ChatChannel) => void }) {
  const [name, setName] = useState("");
  const [topic, setTopic] = useState("");
  const [isPrivate, setPrivate] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const preview = useMemo(() => name.trim().toLowerCase().replace(/^#/, "").replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, ""), [name]);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const channel = await api.post<ChatChannel>("/chat/channels", { name, topic: topic.trim() || null, is_private: isPrivate, member_ids: picked });
      setName("");
      setTopic("");
      setPicked([]);
      setPrivate(false);
      onCreated(channel);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title="New channel"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void create()} disabled={busy || !preview}>
            Create
          </button>
        </>
      }
    >
      {error ? (
        <div style={{ marginBottom: 12 }}>
          <Alert>{error}</Alert>
        </div>
      ) : null}
      <div className="ui-stack">
        <Field label="Name" hint={preview ? `#${preview}` : "Lowercase letters, numbers and dashes."}>
          <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="deal-desk" />
        </Field>
        <Field label="Topic">
          <input className="ui-input" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="What's this channel for?" />
        </Field>
        <label className="ui-check">
          <input type="checkbox" checked={isPrivate} onChange={(e) => setPrivate(e.target.checked)} /> Private — only people you add can see it
        </label>
        <div className="ui-field">
          <span className="ui-label">Add people</span>
          <div className="check-grid">
            {members
              .filter((m) => m.id !== me)
              .map((m) => (
                <label key={m.id} className="ui-check">
                  <input type="checkbox" checked={picked.includes(m.id)} onChange={() => setPicked(picked.includes(m.id) ? picked.filter((p) => p !== m.id) : [...picked, m.id])} /> {m.full_name}
                </label>
              ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}

function AddPeople({ open, channel, members, onClose, onAdded }: { open: boolean; channel: ChatChannel; members: Member[]; onClose: () => void; onAdded: () => void }) {
  const toast = useToast();
  const [picked, setPicked] = useState<string[]>([]);
  const outside = members.filter((m) => !channel.member_ids.includes(m.id));
  async function add() {
    try {
      await api.post(`/chat/channels/${channel.id}/members`, { user_ids: picked });
      setPicked([]);
      toast("Added.");
      onAdded();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }
  return (
    <Modal
      open={open}
      title={`Add people to #${channel.name}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void add()} disabled={!picked.length}>
            Add
          </button>
        </>
      }
    >
      {outside.length ? (
        <div className="check-grid">
          {outside.map((m) => (
            <label key={m.id} className="ui-check">
              <input type="checkbox" checked={picked.includes(m.id)} onChange={() => setPicked(picked.includes(m.id) ? picked.filter((p) => p !== m.id) : [...picked, m.id])} /> {m.full_name}
            </label>
          ))}
        </div>
      ) : (
        <p className="ui-muted">Everyone is already here.</p>
      )}
    </Modal>
  );
}
