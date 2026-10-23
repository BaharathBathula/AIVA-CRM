"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Copy, Eye, Mail, Send, TestTube2, Users } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ConditionEditor, cleanConditions, type Condition } from "@/components/condition-editor";
import { EmailEditor } from "@/components/email-editor";
import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, Field, Kpi, LoadingRows, Modal, PageHeader, Tabs } from "@/components/ui";
import { dateTime, number } from "@/lib/format";
import { CAMPAIGN_TONE, type CampaignSummary } from "@/lib/marketing";
import type { Catalog } from "@/lib/platform-types";
import { api, errorMessage, useApi, useDebounced } from "@/lib/use-api";

interface Campaign extends CampaignSummary {
  body: string;
  template_id: string | null;
  from_name: string | null;
  reply_to: string | null;
  delivery: "mailbox" | "server" | "outbox";
}

interface Recipient {
  id: string;
  email: string;
  name: string | null;
  record_type: string;
  record_id: string;
  status: string;
  delivery: string | null;
  error: string | null;
  sent_at: string | null;
  opened_at: string | null;
  open_count: number;
  unsubscribed_at: string | null;
  subject: string | null;
  body: string | null;
}

export default function CampaignPage() {
  return (
    <FeaturePage feature="mass_email">
      <CampaignDetail />
    </FeaturePage>
  );
}

function CampaignDetail() {
  const { campaignId } = useParams<{ campaignId: string }>();
  const campaign = useApi<Campaign>(`/campaigns/${campaignId}`);
  if (campaign.error) return <Alert>{campaign.error}</Alert>;
  if (!campaign.data) return <Card><LoadingRows rows={10} /></Card>;
  return campaign.data.status === "draft" ? (
    <CampaignEditor campaign={campaign.data} onSaved={(next) => campaign.setData(next)} reload={campaign.reload} />
  ) : (
    <CampaignResults campaign={campaign.data} />
  );
}

const DELIVERY_NOTE = {
  mailbox: "Sends from your connected mailbox.",
  server: "Sends through the workspace mail server.",
  outbox: "No mail server is connected, so messages are kept in the outbox instead of being delivered. Connect Gmail or Outlook in Settings → Integrations to send for real.",
};

function CampaignEditor({ campaign, onSaved, reload }: { campaign: Campaign; onSaved: (c: Campaign) => void; reload: () => Promise<void> }) {
  const toast = useToast();
  const { has, can } = useSession();
  const router = useRouter();
  const catalog = useApi<Catalog>("/meta/catalog");
  const templates = useApi<{ id: string; name: string; subject: string; body: string }[]>("/email-templates");
  const [name, setName] = useState(campaign.name);
  const [subject, setSubject] = useState(campaign.subject);
  const [body, setBody] = useState(campaign.body);
  const [objectType, setObjectType] = useState(campaign.audience.object_type ?? "contact");
  const [conditions, setConditions] = useState<Condition[]>((campaign.audience.filters as Condition[]) ?? []);
  const [tier, setTier] = useState(campaign.audience.abm_tier ?? "");
  const [fromName, setFromName] = useState(campaign.from_name ?? "");
  const [replyTo, setReplyTo] = useState(campaign.reply_to ?? "");
  const [preview, setPreview] = useState<{ count: number; sample: { id: string; name: string; email: string }[] } | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [schedule, setSchedule] = useState("");
  const [confirmSend, setConfirmSend] = useState(false);
  const [busy, setBusy] = useState(false);
  const editable = can("marketing.manage");

  const object = catalog.data?.objects.find((o) => o.key === objectType);
  const audience = useMemo(
    () => ({ object_type: objectType, filters: cleanConditions(conditions), abm_tier: objectType === "contact" && tier ? tier : null }),
    [objectType, conditions, tier]
  );
  const audienceKey = useDebounced(JSON.stringify(audience), 400);

  useEffect(() => {
    let active = true;
    setPreviewError(null);
    api
      .post<{ count: number; sample: { id: string; name: string; email: string }[] }>("/campaigns/audience-preview", JSON.parse(audienceKey))
      .then((result) => active && setPreview(result))
      .catch((caught) => active && setPreviewError(errorMessage(caught)));
    return () => {
      active = false;
    };
  }, [audienceKey]);

  function payload() {
    return {
      name: name.trim() || campaign.name,
      subject,
      body,
      template_id: campaign.template_id,
      audience,
      from_name: fromName.trim() || null,
      reply_to: replyTo.trim() || null,
    };
  }

  async function save(quiet = false) {
    setBusy(true);
    try {
      const saved = await api.patch<Campaign>(`/campaigns/${campaign.id}`, payload());
      onSaved({ ...saved, delivery: campaign.delivery });
      if (!quiet) toast("Draft saved.");
      return true;
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function sendTest() {
    if (!(await save(true))) return;
    try {
      const result = await api.post<{ message: string; delivery: string }>(`/campaigns/${campaign.id}/test`, {});
      toast(result.message, result.delivery === "smtp" ? "success" : "danger");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function send() {
    if (!(await save(true))) return;
    setBusy(true);
    try {
      await api.post(`/campaigns/${campaign.id}/send`, { scheduled_at: schedule ? new Date(schedule).toISOString() : null });
      toast(schedule ? "Campaign scheduled." : "Campaign sent.");
      setConfirmSend(false);
      await reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    await api.del(`/campaigns/${campaign.id}`);
    router.push("/marketing/campaigns");
  }

  return (
    <>
      <PageHeader
        eyebrow={
          <Link href="/marketing/campaigns" className="ui-row" style={{ gap: 6, color: "inherit", textDecoration: "none" }}>
            <ArrowLeft size={14} /> Campaigns
          </Link>
        }
        title={campaign.name}
        description="Draft. Choose who receives it, write the email, then send now or schedule it."
        actions={
          editable ? (
            <>
              <button type="button" className="ui-btn" disabled={busy} onClick={() => void sendTest()}>
                <TestTube2 size={15} /> Send me a test
              </button>
              <button type="button" className="ui-btn" disabled={busy} onClick={() => void save()}>
                Save draft
              </button>
              <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !preview?.count} onClick={() => setConfirmSend(true)}>
                <Send size={15} /> Review and send
              </button>
            </>
          ) : null
        }
      />
      <div className="ui-stack" style={{ gap: 16 }}>
        <Alert tone={campaign.delivery === "outbox" ? "warning" : "info"}>{DELIVERY_NOTE[campaign.delivery]}</Alert>
        <Card title="1. Audience" icon={<Users size={16} />}>
          <div className="ui-stack">
            <div className="ui-form-grid">
              <Field label="Campaign name">
                <input className="ui-input" value={name} disabled={!editable} onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label="Send to">
                <select
                  className="ui-select"
                  value={objectType}
                  disabled={!editable}
                  onChange={(e) => {
                    setObjectType(e.target.value);
                    setConditions([]);
                  }}
                >
                  <option value="contact">Contacts</option>
                  <option value="lead">Leads</option>
                </select>
              </Field>
              {objectType === "contact" && has("abm") ? (
                <Field label="Target accounts" hint="Only contacts at your ABM target accounts.">
                  <select className="ui-select" value={tier} disabled={!editable} onChange={(e) => setTier(e.target.value)}>
                    <option value="">Any account</option>
                    <option value="all">All target accounts</option>
                    <option value="tier_1">Tier 1 only</option>
                    <option value="tier_2">Tier 2 only</option>
                    <option value="tier_3">Tier 3 only</option>
                  </select>
                </Field>
              ) : null}
            </div>
            <div>
              <div className="ui-small ui-strong" style={{ marginBottom: 8 }}>
                Only people where all of these match
              </div>
              {object && catalog.data ? (
                <ConditionEditor
                  fields={object.fields}
                  operators={catalog.data.operators}
                  conditions={conditions}
                  onChange={setConditions}
                  includePeriod
                  disabled={!editable}
                  emptyLabel={`No filters — everyone with an email address who hasn't unsubscribed.`}
                />
              ) : (
                <LoadingRows rows={2} />
              )}
            </div>
            <div className="ui-insight">
              {previewError ? (
                <span>{previewError}</span>
              ) : preview ? (
                <>
                  <strong>
                    {number(preview.count)} {preview.count === 1 ? "person" : "people"} will get this email
                  </strong>
                  <span>
                    {preview.sample.length ? `Including ${preview.sample.map((p) => p.name || p.email).join(", ")}.` : "Nobody matches yet."} People who unsubscribed or
                    have no email are left out.
                  </span>
                </>
              ) : (
                <span>Counting…</span>
              )}
            </div>
          </div>
        </Card>
        <Card
          title="2. Email"
          icon={<Mail size={16} />}
          actions={
            editable && templates.data?.length ? (
              <select
                className="ui-select sm"
                aria-label="Use a template"
                value=""
                onChange={(e) => {
                  const template = templates.data?.find((t) => t.id === e.target.value);
                  if (template) {
                    setSubject(template.subject);
                    setBody(template.body);
                  }
                }}
              >
                <option value="">Use a template…</option>
                {templates.data.map((template) => (
                  <option key={template.id} value={template.id}>
                    {template.name}
                  </option>
                ))}
              </select>
            ) : null
          }
        >
          <div className="ui-stack">
            <div className="ui-form-grid">
              <Field label="From name" hint="Defaults to the sender's name.">
                <input className="ui-input" value={fromName} disabled={!editable} onChange={(e) => setFromName(e.target.value)} />
              </Field>
              <Field label="Reply-to address">
                <input className="ui-input" type="email" value={replyTo} disabled={!editable} onChange={(e) => setReplyTo(e.target.value)} />
              </Field>
            </div>
            <EmailEditor subject={subject} body={body} onSubject={setSubject} onBody={setBody} disabled={!editable} />
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              An unsubscribe link and an open-tracking pixel are added to every email automatically.
            </p>
          </div>
        </Card>
        {editable ? (
          <div className="ui-row">
            <button type="button" className="ui-btn ui-btn-ghost" onClick={() => void remove()}>
              Delete draft
            </button>
          </div>
        ) : null}
      </div>

      <Modal
        open={confirmSend}
        title="Send this campaign?"
        description={`${number(preview?.count ?? 0)} people will receive “${subject}”.`}
        onClose={() => setConfirmSend(false)}
        footer={
          <>
            <button type="button" className="ui-btn" onClick={() => setConfirmSend(false)}>
              Cancel
            </button>
            <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void send()}>
              <Send size={15} /> {schedule ? "Schedule" : "Send now"}
            </button>
          </>
        }
      >
        <Field label="When" hint="Leave empty to send right away.">
          <input className="ui-input" type="datetime-local" value={schedule} onChange={(e) => setSchedule(e.target.value)} />
        </Field>
      </Modal>
    </>
  );
}

function CampaignResults({ campaign }: { campaign: Campaign }) {
  const router = useRouter();
  const toast = useToast();
  const { can } = useSession();
  const [tab, setTab] = useState<"all" | "opened" | "unsubscribed" | "failed">("all");
  const recipients = useApi<{ total: number; items: Recipient[] }>(`/campaigns/${campaign.id}/recipients${tab === "all" ? "" : `?status=${tab}`}`);
  const [viewing, setViewing] = useState<Recipient | null>(null);
  const stats = campaign.stats;

  async function duplicate() {
    try {
      const copy = await api.post<CampaignSummary>(`/campaigns/${campaign.id}/duplicate`);
      router.push(`/marketing/campaigns/${copy.id}`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <>
      <PageHeader
        eyebrow={
          <Link href="/marketing/campaigns" className="ui-row" style={{ gap: 6, color: "inherit", textDecoration: "none" }}>
            <ArrowLeft size={14} /> Campaigns
          </Link>
        }
        title={campaign.name}
        description={campaign.subject}
        actions={
          <>
            <Badge tone={CAMPAIGN_TONE[campaign.status]}>{campaign.status}</Badge>
            {can("marketing.manage") ? (
              <button type="button" className="ui-btn" onClick={() => void duplicate()}>
                <Copy size={15} /> Duplicate
              </button>
            ) : null}
          </>
        }
      />
      {campaign.status === "scheduled" ? <Alert tone="info">Scheduled for {dateTime(campaign.scheduled_at)}.</Alert> : null}
      {stats.outbox ? (
        <Alert tone="warning">
          {stats.outbox} message{stats.outbox === 1 ? " was" : "s were"} kept in the outbox because no mail server was connected. Open a recipient to read their copy.
        </Alert>
      ) : null}
      <div className="ui-grid ui-grid-4" style={{ margin: "14px 0 16px" }}>
        <Kpi label="Recipients" value={number(stats.recipients ?? 0)} />
        <Kpi label="Sent" value={number(stats.sent ?? 0)} note={stats.failed ? `${stats.failed} failed` : undefined} tone={stats.failed ? "danger" : undefined} />
        <Kpi label="Opened" value={number(stats.opened ?? 0)} note={`${stats.open_rate ?? 0}% open rate`} />
        <Kpi label="Unsubscribed" value={number(stats.unsubscribed ?? 0)} />
      </div>
      <Card
        title="Recipients"
        actions={
          <Tabs
            label="Recipient filter"
            value={tab}
            onChange={setTab}
            items={[
              { value: "all", label: "All" },
              { value: "opened", label: "Opened" },
              { value: "unsubscribed", label: "Unsubscribed" },
              { value: "failed", label: "Failed" },
            ]}
          />
        }
        flush
      >
        {recipients.loading && !recipients.data ? (
          <LoadingRows />
        ) : recipients.data?.items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Recipient</th>
                  <th>Status</th>
                  <th>Sent</th>
                  <th>Opened</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {recipients.data.items.map((r) => (
                  <tr key={r.id}>
                    <td className="cell-title">
                      <Link href={`/${r.record_type === "lead" ? "leads" : "contacts"}/${r.record_id}`}>{r.name || r.email}</Link>
                      <div className="ui-muted ui-small">{r.email}</div>
                    </td>
                    <td>
                      <Badge tone={r.status === "sent" ? "success" : r.status === "failed" ? "danger" : "neutral"}>
                        {r.unsubscribed_at ? "unsubscribed" : r.status === "sent" && r.delivery === "outbox" ? "in outbox" : r.status}
                      </Badge>
                      {r.error ? <div className="ui-small" style={{ color: "var(--danger)" }}>{r.error}</div> : null}
                    </td>
                    <td className="ui-muted">{dateTime(r.sent_at)}</td>
                    <td className="ui-muted">{r.opened_at ? `${dateTime(r.opened_at)}${r.open_count > 1 ? ` · ${r.open_count}×` : ""}` : "—"}</td>
                    <td style={{ textAlign: "right" }}>
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => setViewing(r)}>
                        <Eye size={14} /> View
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty-hint">Nobody here yet.</p>
        )}
      </Card>
      <Modal open={Boolean(viewing)} title={viewing?.subject ?? ""} description={`To ${viewing?.name ?? ""} <${viewing?.email ?? ""}>`} onClose={() => setViewing(null)} size="lg">
        <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.6, fontSize: 14 }}>{viewing?.body}</div>
      </Modal>
    </>
  );
}
