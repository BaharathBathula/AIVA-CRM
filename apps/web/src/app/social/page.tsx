"use client";

import { CalendarClock, ExternalLink, Megaphone, Plug, RefreshCw, Send, Trash2, Undo2 } from "lucide-react";
import { useMemo, useState } from "react";

import { FeaturePage } from "@/components/plan";
import { useToast } from "@/components/providers";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader, Tabs } from "@/components/ui";
import { dateTime, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";
import { NETWORK_LABEL, POST_STATUS, type Network, type SocialChannel, type SocialPost } from "@/lib/workspace";

export default function SocialPage() {
  return (
    <FeaturePage feature="social">
      <Social />
    </FeaturePage>
  );
}

function NetworkBadge({ network }: { network: Network | null }) {
  if (!network) return <span className="network-badge" style={{ background: "var(--muted)" }}>?</span>;
  const letter = { linkedin: "in", x: "X", facebook: "f", instagram: "IG" }[network];
  return (
    <span className={`network-badge ${network}`} aria-hidden>
      {letter}
    </span>
  );
}

function xLength(text: string, link: string) {
  return text.replace(/https?:\/\/\S+/g, "x".repeat(23)).length + (link && !text.includes(link) ? 24 : 0);
}

function Social() {
  const toast = useToast();
  const channels = useApi<{ items: SocialChannel[]; can_manage: boolean; limits: Record<Network, number> }>("/social/channels");
  const [filter, setFilter] = useState<"all" | "scheduled" | "published" | "draft" | "failed">("all");
  const posts = useApi<{ items: SocialPost[]; counts: Record<string, number>; can_manage: boolean }>(`/social/posts${filter === "all" ? "" : `?status=${filter}`}`);
  const [connecting, setConnecting] = useState(false);
  const [removing, setRemoving] = useState<SocialChannel | null>(null);
  const [deleting, setDeleting] = useState<SocialPost | null>(null);
  const [editing, setEditing] = useState<SocialPost | null>(null);
  const [text, setText] = useState("");
  const [link, setLink] = useState("");
  const [image, setImage] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [when, setWhen] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const manage = Boolean(channels.data?.can_manage);
  const list = channels.data?.items ?? [];
  const limits = channels.data?.limits;
  const counts = useMemo(
    () =>
      list
        .filter((c) => picked.includes(c.id))
        .map((c) => {
          const used = c.network === "x" ? xLength(text, link) : text.length;
          return { network: c.network, used, limit: limits?.[c.network] ?? 3000 };
        })
        .filter((v, i, a) => a.findIndex((x) => x.network === v.network) === i),
    [list, picked, text, link, limits]
  );
  const over = counts.some((c) => c.used > c.limit);

  function reset() {
    setEditing(null);
    setText("");
    setLink("");
    setImage("");
    setWhen("");
    setError(null);
  }

  function edit(post: SocialPost) {
    setEditing(post);
    setText(post.text);
    setLink(post.link_url ?? "");
    setImage(post.image_url ?? "");
    setPicked(post.channel_ids);
    if (post.scheduled_at) {
      const d = new Date(post.scheduled_at);
      const pad = (n: number) => String(n).padStart(2, "0");
      setWhen(`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`);
    } else setWhen("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function submit(action: "now" | "schedule" | "draft") {
    setError(null);
    if (!text.trim()) return setError("Write the post first.");
    if (!picked.length) return setError("Pick at least one account to post to.");
    if (action === "schedule" && !when) return setError("Pick when to publish.");
    setBusy(true);
    const body = {
      text: text.trim(),
      link_url: link.trim() || null,
      image_url: image.trim() || null,
      channel_ids: picked,
      scheduled_at: action === "schedule" ? new Date(when).toISOString() : null,
    };
    try {
      let post: SocialPost;
      if (editing) {
        post = await api.patch<SocialPost>(`/social/posts/${editing.id}`, body);
        if (action === "now") post = await api.post<SocialPost>(`/social/posts/${editing.id}/publish`);
      } else {
        post = await api.post<SocialPost>("/social/posts", { ...body, publish_now: action === "now" });
      }
      const failed = post.channels.filter((c) => c.result?.status === "failed");
      if (action === "now") {
        if (failed.length) toast(`${failed.length} account${failed.length === 1 ? "" : "s"} failed: ${failed[0].result?.error ?? ""}`, "danger");
        else toast("Published.");
      } else toast(action === "schedule" ? `Scheduled for ${dateTime(post.scheduled_at)}.` : "Draft saved.");
      reset();
      await posts.reload();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function postAction(post: SocialPost, action: "publish" | "unschedule") {
    try {
      const result = await api.post<SocialPost>(`/social/posts/${post.id}/${action}`);
      const failed = result.channels.filter((c) => c.result?.status === "failed");
      toast(action === "publish" ? (failed.length ? `Failed on ${failed.map((f) => f.name).join(", ")}.` : "Published.") : "Moved back to drafts.", failed.length ? "danger" : undefined);
      await posts.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function check(channel: SocialChannel) {
    try {
      const result = await api.post<SocialChannel>(`/social/channels/${channel.id}/check`);
      toast(result.status === "connected" ? `${result.name} is connected.` : `${result.name}: ${result.last_error}`, result.status === "connected" ? undefined : "danger");
      await channels.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <>
      <PageHeader
        title="Social"
        description="Publish and schedule posts to your company's LinkedIn, X, Facebook and Instagram accounts from one place."
        actions={
          manage ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setConnecting(true)}>
              <Plug size={16} /> Connect account
            </button>
          ) : null
        }
      />
      <div className="ui-split">
        <div className="ui-stack">
          {manage ? (
            <Card title={editing ? "Edit post" : "New post"} icon={<Megaphone size={16} />} className="composer">
              {!list.length ? (
                <Alert tone="info">Connect a social account to start posting.</Alert>
              ) : (
                <div className="ui-stack">
                  {error ? <Alert>{error}</Alert> : null}
                  <div className="channel-pick" role="group" aria-label="Post to">
                    {list.map((c) => (
                      <label key={c.id}>
                        <input type="checkbox" checked={picked.includes(c.id)} onChange={() => setPicked(picked.includes(c.id) ? picked.filter((p) => p !== c.id) : [...picked, c.id])} />
                        <NetworkBadge network={c.network} /> {c.name}
                        {c.status !== "connected" ? <Badge tone="danger">Reconnect</Badge> : null}
                      </label>
                    ))}
                  </div>
                  <Field label="Post">
                    <textarea className="ui-textarea" rows={5} value={text} onChange={(e) => setText(e.target.value)} placeholder="Share news, an article or an event…" />
                  </Field>
                  {counts.length ? (
                    <div className="ui-row" style={{ gap: 14, justifyContent: "flex-end" }}>
                      {counts.map((c) => (
                        <span key={c.network} className={`char-count${c.used > c.limit ? " over" : ""}`}>
                          {NETWORK_LABEL[c.network]} {c.used}/{c.limit}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  <div className="ui-form-grid">
                    <Field label="Link (optional)">
                      <input className="ui-input" value={link} onChange={(e) => setLink(e.target.value)} placeholder="https://" />
                    </Field>
                    <Field label="Image link" hint="Needed for Instagram. A public https image URL.">
                      <input className="ui-input" value={image} onChange={(e) => setImage(e.target.value)} placeholder="https://…/image.jpg" />
                    </Field>
                    <Field label="Schedule for (optional)">
                      <input className="ui-input" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
                    </Field>
                  </div>
                  <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
                    {editing ? (
                      <button type="button" className="ui-btn ui-btn-ghost" onClick={reset}>
                        Cancel edit
                      </button>
                    ) : null}
                    <span className="ui-spacer" />
                    <button type="button" className="ui-btn" onClick={() => void submit("draft")} disabled={busy}>
                      Save draft
                    </button>
                    <button type="button" className="ui-btn" onClick={() => void submit("schedule")} disabled={busy || over || !when}>
                      <CalendarClock size={15} /> Schedule
                    </button>
                    <button type="button" className="ui-btn ui-btn-primary" onClick={() => void submit("now")} disabled={busy || over}>
                      {busy ? <span className="ui-spinner" /> : <Send size={15} />} Publish now
                    </button>
                  </div>
                </div>
              )}
            </Card>
          ) : null}
          <Card
            title="Posts"
            flush
            actions={
              <Tabs
                label="Post status"
                value={filter}
                onChange={setFilter}
                items={[
                  { value: "all", label: "All" },
                  { value: "scheduled", label: "Scheduled", count: posts.data?.counts.scheduled },
                  { value: "published", label: "Published" },
                  { value: "draft", label: "Drafts" },
                  { value: "failed", label: "Failed", count: posts.data?.counts.failed, hot: true },
                ]}
              />
            }
          >
            {posts.loading && !posts.data ? (
              <LoadingRows />
            ) : posts.data?.items.length ? (
              posts.data.items.map((post) => (
                <article key={post.id} className="post-card">
                  <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
                    <Badge tone={POST_STATUS[post.status].tone}>{POST_STATUS[post.status].label}</Badge>
                    <span className="ui-muted ui-small">
                      {post.status === "scheduled"
                        ? `Goes out ${dateTime(post.scheduled_at)}`
                        : post.published_at
                          ? `Published ${relativeTime(post.published_at)}`
                          : `Created ${relativeTime(post.created_at)}`}
                      {post.created_by ? ` · by ${post.created_by}` : ""}
                    </span>
                    <span className="ui-spacer" />
                    {manage && ["draft", "scheduled", "failed"].includes(post.status) ? (
                      <>
                        <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => edit(post)}>
                          Edit
                        </button>
                        <button type="button" className="ui-btn ui-btn-sm" onClick={() => void postAction(post, "publish")}>
                          <Send size={13} /> {post.status === "failed" ? "Retry" : "Publish now"}
                        </button>
                      </>
                    ) : null}
                    {manage && post.status === "partial" ? (
                      <button type="button" className="ui-btn ui-btn-sm" onClick={() => void postAction(post, "publish")}>
                        <RefreshCw size={13} /> Retry failed
                      </button>
                    ) : null}
                    {manage && post.status === "scheduled" ? (
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => void postAction(post, "unschedule")}>
                        <Undo2 size={13} /> Unschedule
                      </button>
                    ) : null}
                    {manage ? (
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Delete post" onClick={() => setDeleting(post)}>
                        <Trash2 size={14} />
                      </button>
                    ) : null}
                  </div>
                  <p className="post-text">{post.text}</p>
                  {post.link_url ? (
                    <a href={post.link_url} target="_blank" rel="noopener noreferrer" className="ui-small">
                      {post.link_url}
                    </a>
                  ) : null}
                  <div className="post-results">
                    {post.channels.map((c) => (
                      <span key={c.id} className="record-chip">
                        <NetworkBadge network={c.network} /> {c.name}
                        {c.result?.status === "published" && c.result.url ? (
                          <a href={c.result.url} target="_blank" rel="noopener noreferrer" aria-label={`View on ${c.network ? NETWORK_LABEL[c.network] : "network"}`}>
                            <ExternalLink size={13} />
                          </a>
                        ) : null}
                        {c.result?.status === "failed" ? <Badge tone="danger">{c.result.error ?? "Failed"}</Badge> : null}
                      </span>
                    ))}
                  </div>
                </article>
              ))
            ) : (
              <EmptyState icon={<Megaphone size={20} />} title="No posts here" message="Write a post above to publish it now or schedule it." />
            )}
          </Card>
        </div>
        <Card title="Connected accounts" icon={<Plug size={16} />}>
          {channels.loading && !channels.data ? (
            <LoadingRows rows={3} />
          ) : list.length ? (
            <ul className="mini-list">
              {list.map((c) => (
                <li key={c.id}>
                  <NetworkBadge network={c.network} />
                  <div className="mini-main">
                    <span className="mini-title">{c.name}</span>
                    <span className="mini-sub">
                      {c.network_label}
                      {c.handle ? ` · ${c.handle}` : ""}
                      {c.status !== "connected" ? ` · ${c.last_error ?? "Needs reconnecting"}` : ""}
                    </span>
                  </div>
                  {c.status !== "connected" ? <Badge tone="danger">Error</Badge> : null}
                  {manage ? (
                    <>
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Check ${c.name}`} onClick={() => void check(c)}>
                        <RefreshCw size={14} />
                      </button>
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Disconnect ${c.name}`} onClick={() => setRemoving(c)}>
                        <Trash2 size={14} />
                      </button>
                    </>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              No accounts yet.{manage ? " Connect your company's pages to post from AIVA." : " Ask a marketing admin to connect your company's pages."}
            </p>
          )}
        </Card>
      </div>
      <ConnectModal open={connecting} onClose={() => setConnecting(false)} onConnected={(c) => (setConnecting(false), setPicked((p) => [...p, c.id]), void channels.reload(), toast(`${c.name} connected.`))} />
      <ConfirmDialog
        open={removing !== null}
        title={`Disconnect ${removing?.name ?? "account"}?`}
        message="AIVA forgets the access token. Posts already published stay on the network."
        confirmLabel="Disconnect"
        onConfirm={() =>
          removing &&
          void api
            .del(`/social/channels/${removing.id}`)
            .then(() => (setRemoving(null), channels.reload(), toast("Disconnected.")))
            .catch((caught) => toast(errorMessage(caught), "danger"))
        }
        onClose={() => setRemoving(null)}
      />
      <ConfirmDialog
        open={deleting !== null}
        title="Delete this post?"
        message="It's removed from AIVA. Already published copies stay on the networks."
        onConfirm={() =>
          deleting &&
          void api
            .del(`/social/posts/${deleting.id}`)
            .then(() => (setDeleting(null), posts.reload(), toast("Post deleted.")))
            .catch((caught) => toast(errorMessage(caught), "danger"))
        }
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

const HELP: Record<Network, { id?: string; token: string; steps: string }> = {
  linkedin: {
    id: "Organization ID (company page) or member ID",
    token: "Access token with w_organization_social",
    steps: "In the LinkedIn Developer Portal, create an app with the Community Management API, add your company page, and generate an access token with w_organization_social. The organization ID is the number in your page's admin URL.",
  },
  x: {
    token: "OAuth 2.0 user access token with tweet.write",
    steps: "In the X Developer Portal, create a project and app with OAuth 2.0 (read and write), then authorize the company account to get a user access token with tweet.read, tweet.write and users.read.",
  },
  facebook: {
    id: "Page ID",
    token: "Page access token with pages_manage_posts",
    steps: "In Meta for Developers, create a business app, then use the Graph API Explorer to get a long-lived Page access token with pages_manage_posts and pages_read_engagement. The Page ID is under About → Page transparency.",
  },
  instagram: {
    id: "Instagram business account ID",
    token: "Access token with instagram_content_publish",
    steps: "Link the Instagram professional account to a Facebook Page, then get a token with instagram_basic and instagram_content_publish. The account ID comes from GET /{page-id}?fields=instagram_business_account.",
  },
};

function ConnectModal({ open, onClose, onConnected }: { open: boolean; onClose: () => void; onConnected: (channel: SocialChannel) => void }) {
  const [network, setNetwork] = useState<Network>("linkedin");
  const [externalId, setExternalId] = useState("");
  const [token, setToken] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const help = HELP[network];

  async function connect() {
    setError(null);
    setBusy(true);
    try {
      const channel = await api.post<SocialChannel>("/social/channels", { network, external_id: externalId.trim(), access_token: token.trim(), name: name.trim() || null });
      setToken("");
      setExternalId("");
      setName("");
      onConnected(channel);
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
      title="Connect a social account"
      description="AIVA checks the token with the network before saving it. Tokens are stored encrypted."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void connect()} disabled={busy || token.trim().length < 10 || (Boolean(help.id) && !externalId.trim())}>
            {busy ? <span className="ui-spinner" /> : null} Check and connect
          </button>
        </>
      }
    >
      {error ? (
        <div style={{ marginBottom: 12 }}>
          <Alert>{error}</Alert>
        </div>
      ) : null}
      <div className="ui-form-grid">
        <Field label="Network">
          <select className="ui-select" value={network} onChange={(e) => setNetwork(e.target.value as Network)}>
            {(Object.keys(NETWORK_LABEL) as Network[]).map((n) => (
              <option key={n} value={n}>
                {NETWORK_LABEL[n]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Name in AIVA (optional)">
          <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Taken from the network if empty" />
        </Field>
        {help.id ? (
          <Field label={help.id} required className="span-2">
            <input className="ui-input ui-mono" value={externalId} onChange={(e) => setExternalId(e.target.value)} />
          </Field>
        ) : null}
        <Field label={help.token} required className="span-2">
          <input className="ui-input ui-mono" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
        </Field>
        <p className="ui-muted ui-small span-2" style={{ margin: 0, lineHeight: 1.55 }}>
          {help.steps}
        </p>
      </div>
    </Modal>
  );
}
