"use client";

import Link from "next/link";
import { Activity, Flame, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { NumberInput, TagsInput, Toggle } from "@/components/settings-kit";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, Kpi, LoadingRows, Modal, PageHeader, type Tone } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

type Topic = { id: string; name: string; description: string | null; keywords: string[]; url_patterns: string[]; weight: number; is_active: boolean };

type IntentAccount = {
  key: string;
  account_id: string | null;
  lead_id: string | null;
  name: string;
  owner_name: string | null;
  href: string;
  score: number;
  level: "high" | "medium" | "low";
  topics: { name: string; views: number }[];
  visitors: number;
  last_seen: string;
};

type IntentData = { topics: Topic[]; accounts: IntentAccount[]; levels: Record<"high" | "medium" | "low", number>; can_edit: boolean };

const LEVEL_TONE: Record<string, Tone> = { high: "danger", medium: "warning", low: "info" };

export default function IntentPage() {
  return (
    <SettingsShell feature="buying_intent">
      <BuyingIntent />
    </SettingsShell>
  );
}

function BuyingIntent() {
  const toast = useToast();
  const [days, setDays] = useState("30");
  const intent = useApi<IntentData>(`/intent?days=${days}`);
  const [editing, setEditing] = useState<Topic | "new" | null>(null);
  const [deleting, setDeleting] = useState<Topic | null>(null);
  const [busy, setBusy] = useState(false);
  const data = intent.data;
  const canEdit = Boolean(data?.can_edit);

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/intent/topics/${deleting.id}`);
      toast("Topic deleted.");
      setDeleting(null);
      await intent.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Buying intent"
        description="Topics are the subjects that show a company is in market — your pricing page, competitor comparisons, integration docs. When known visitors read them, their account's intent score rises."
        actions={
          canEdit ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
              <Plus size={16} /> New topic
            </button>
          ) : null
        }
      />
      <div className="ui-stack">
        {intent.error ? <Alert>{intent.error}</Alert> : null}
        <Card title="Topics" flush>
          {intent.loading && !data ? (
            <LoadingRows rows={3} />
          ) : data?.topics.length ? (
            <div className="ui-table-wrap">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th>Topic</th>
                    <th>Pages</th>
                    <th>Keywords</th>
                    <th className="ui-num">Weight</th>
                    <th>Status</th>
                    {canEdit ? <th aria-label="Actions" /> : null}
                  </tr>
                </thead>
                <tbody>
                  {data.topics.map((topic) => (
                    <tr key={topic.id} style={{ opacity: topic.is_active ? 1 : 0.55 }}>
                      <td>
                        <div className="cell-title">{topic.name}</div>
                        {topic.description ? <div className="ui-muted ui-small">{topic.description}</div> : null}
                      </td>
                      <td className="ui-mono ui-small">{topic.url_patterns.join(", ") || "—"}</td>
                      <td className="ui-small">{topic.keywords.join(", ") || "—"}</td>
                      <td className="ui-num">×{topic.weight}</td>
                      <td>{topic.is_active ? <Badge tone="success">On</Badge> : <Badge>Off</Badge>}</td>
                      {canEdit ? (
                        <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${topic.name}`} onClick={() => setEditing(topic)}>
                            <Pencil size={14} />
                          </button>
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${topic.name}`} onClick={() => setDeleting(topic)}>
                            <Trash2 size={14} />
                          </button>
                        </td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              icon={<Activity size={20} />}
              title="No topics yet"
              message={
                <>
                  Add one like “Pricing” with the page /pricing. Intent comes from <Link href="/visits">website visits</Link>, so make sure your site has the tracking code.
                </>
              }
            />
          )}
        </Card>

        <div className="ui-grid ui-grid-3">
          <Kpi label="High intent" value={data?.levels.high ?? "—"} tone={data?.levels.high ? "danger" : undefined} />
          <Kpi label="Medium intent" value={data?.levels.medium ?? "—"} />
          <Kpi label="Low intent" value={data?.levels.low ?? "—"} />
        </div>

        <Card
          title="Companies showing intent"
          icon={<Flame size={16} />}
          flush
          actions={
            <select className="ui-select sm" value={days} onChange={(event) => setDays(event.target.value)} aria-label="Period">
              <option value="7">Last 7 days</option>
              <option value="30">Last 30 days</option>
              <option value="90">Last 90 days</option>
            </select>
          }
        >
          {intent.loading && !data ? (
            <LoadingRows rows={4} />
          ) : data?.accounts.length ? (
            <div className="ui-table-wrap">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th>Company</th>
                    <th>Intent</th>
                    <th>Topics</th>
                    <th className="ui-num">People</th>
                    <th>Last seen</th>
                    <th>Owner</th>
                  </tr>
                </thead>
                <tbody>
                  {data.accounts.map((row) => (
                    <tr key={row.key}>
                      <td>
                        <Link href={row.href} className="cell-title">
                          {row.name}
                        </Link>
                        {!row.account_id ? <div className="ui-muted ui-small">Lead (no account yet)</div> : null}
                      </td>
                      <td>
                        <Badge tone={LEVEL_TONE[row.level]}>
                          {row.level[0].toUpperCase() + row.level.slice(1)} · {row.score}
                        </Badge>
                      </td>
                      <td className="ui-small">{row.topics.map((t) => `${t.name} (${t.views})`).join(", ")}</td>
                      <td className="ui-num">{row.visitors}</td>
                      <td className="ui-muted ui-small ui-nowrap">{relativeTime(row.last_seen)}</td>
                      <td className="ui-small">{row.owner_name ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState title="No intent yet" message="When known visitors read pages that match your topics, their companies show up here, highest score first." />
          )}
        </Card>
      </div>

      <TopicModal
        topic={editing}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          await intent.reload();
        }}
      />
      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete topic?"
        message={`“${deleting?.name ?? ""}” stops counting toward intent scores.`}
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function TopicModal({ topic, onClose, onSaved }: { topic: Topic | "new" | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [keywords, setKeywords] = useState<string[]>([]);
  const [paths, setPaths] = useState<string[]>([]);
  const [weight, setWeight] = useState(1);
  const [active, setActive] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!topic) return;
    const t = topic === "new" ? null : topic;
    setName(t?.name ?? "");
    setDescription(t?.description ?? "");
    setKeywords(t?.keywords ?? []);
    setPaths(t?.url_patterns ?? []);
    setWeight(t?.weight ?? 1);
    setActive(t?.is_active ?? true);
    setError(null);
  }, [topic]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const payload = { name, description: description || null, keywords, url_patterns: paths, weight, is_active: active };
    try {
      if (topic === "new") await api.post("/intent/topics", payload);
      else if (topic) await api.put(`/intent/topics/${topic.id}`, payload);
      toast(topic === "new" ? "Topic created." : "Topic saved.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={Boolean(topic)}
      title={topic === "new" ? "New intent topic" : "Edit intent topic"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="topic-form" className="ui-btn ui-btn-primary" disabled={saving || !name.trim() || (!keywords.length && !paths.length)}>
            {saving ? <span className="ui-spinner" /> : null} Save topic
          </button>
        </>
      }
    >
      <form id="topic-form" className="ui-stack" onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        <Field label="Name" required>
          <input className="ui-input" value={name} maxLength={120} onChange={(event) => setName(event.target.value)} placeholder="Pricing" />
        </Field>
        <Field label="Description">
          <input className="ui-input" value={description} maxLength={2000} onChange={(event) => setDescription(event.target.value)} />
        </Field>
        <Field label="Page paths" hint="Pages that count, like /pricing or /compare/* (a * matches anything after it).">
          <TagsInput label="Page paths" value={paths} onChange={setPaths} placeholder="/pricing" transform={(v) => (v && !v.startsWith("/") ? `/${v}` : v)} />
        </Field>
        <Field label="Keywords in the page title or address">
          <TagsInput label="Keywords" value={keywords} onChange={setKeywords} placeholder="pricing, demo, vs" />
        </Field>
        <div className="ui-form-grid">
          <Field label="Weight" hint="How much one view counts (1–10).">
            <NumberInput label="Weight" value={weight} min={1} max={10} onChange={(value) => setWeight(Math.max(1, Math.min(10, value ?? 1)))} width={90} />
          </Field>
          <Field label="Status">
            <span className="ui-row" style={{ height: 44 }}>
              <Toggle label="Topic is active" checked={active} onChange={setActive} />
              <span className="ui-small">{active ? "Counting" : "Off"}</span>
            </span>
          </Field>
        </div>
      </form>
    </Modal>
  );
}
