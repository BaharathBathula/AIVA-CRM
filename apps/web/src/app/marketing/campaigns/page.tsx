"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Megaphone, Plus } from "lucide-react";
import { Suspense, useEffect, useState } from "react";

import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, EmptyState, Field, Kpi, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { dateTime, number, relativeTime } from "@/lib/format";
import { CAMPAIGN_TONE, type CampaignSummary } from "@/lib/marketing";
import { api, errorMessage, useApi } from "@/lib/use-api";

export default function CampaignsPage() {
  return (
    <FeaturePage feature="mass_email">
      <Suspense fallback={<LoadingRows />}>
        <Campaigns />
      </Suspense>
    </FeaturePage>
  );
}

function Campaigns() {
  const { can } = useSession();
  const params = useSearchParams();
  const campaigns = useApi<CampaignSummary[]>("/campaigns");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (params.get("new") === "1") setCreating(true);
  }, [params]);

  const rows = campaigns.data ?? [];
  const sent = rows.filter((c) => c.status === "sent");
  const totalSent = sent.reduce((sum, c) => sum + (c.stats.sent ?? 0), 0);
  const totalOpened = sent.reduce((sum, c) => sum + (c.stats.opened ?? 0), 0);

  return (
    <>
      <PageHeader
        title="Email campaigns"
        description="Send personalised email to a filtered list of leads or contacts, with open tracking and one-click unsubscribe."
        actions={
          can("marketing.manage") ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setCreating(true)}>
              <Plus size={16} /> New campaign
            </button>
          ) : null
        }
      />
      {campaigns.error ? <Alert>{campaigns.error}</Alert> : null}
      <div className="ui-grid ui-grid-4" style={{ marginBottom: 16 }}>
        <Kpi label="Campaigns sent" value={number(sent.length)} />
        <Kpi label="Emails delivered" value={number(totalSent)} />
        <Kpi label="Opens" value={number(totalOpened)} note={totalSent ? `${Math.round((totalOpened / totalSent) * 100)}% open rate` : undefined} />
        <Kpi label="Drafts and scheduled" value={number(rows.filter((c) => c.status === "draft" || c.status === "scheduled").length)} />
      </div>
      <Card flush>
        {campaigns.loading && !campaigns.data ? (
          <LoadingRows />
        ) : rows.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Campaign</th>
                  <th>Status</th>
                  <th>Audience</th>
                  <th className="ui-num">Sent</th>
                  <th className="ui-num">Opened</th>
                  <th className="ui-num">Unsubscribed</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((campaign) => (
                  <tr key={campaign.id}>
                    <td className="cell-title">
                      <Link href={`/marketing/campaigns/${campaign.id}`}>{campaign.name}</Link>
                      <div className="ui-muted ui-small ui-truncate" style={{ maxWidth: 360 }}>
                        {campaign.subject || "No subject yet"}
                      </div>
                    </td>
                    <td>
                      <Badge tone={CAMPAIGN_TONE[campaign.status]}>{campaign.status}</Badge>
                    </td>
                    <td>{campaign.audience.object_type === "lead" ? "Leads" : "Contacts"}{campaign.audience.abm_tier ? " · target accounts" : ""}</td>
                    <td className="ui-num">{campaign.stats.sent ?? "—"}</td>
                    <td className="ui-num">{campaign.stats.sent ? `${campaign.stats.opened ?? 0} (${campaign.stats.open_rate ?? 0}%)` : "—"}</td>
                    <td className="ui-num">{campaign.stats.unsubscribed ?? "—"}</td>
                    <td className="ui-muted">
                      {campaign.status === "scheduled" ? dateTime(campaign.scheduled_at) : campaign.completed_at ? relativeTime(campaign.completed_at) : relativeTime(campaign.updated_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<Megaphone size={20} />} title="No campaigns yet" message="Create one to email a filtered list of leads or contacts." />
        )}
      </Card>
      <NewCampaignModal open={creating} onClose={() => setCreating(false)} />
    </>
  );
}

function NewCampaignModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const toast = useToast();
  const templates = useApi<{ id: string; name: string }[]>(open ? "/email-templates" : null);
  const [name, setName] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [objectType, setObjectType] = useState("contact");
  const [busy, setBusy] = useState(false);

  async function create() {
    setBusy(true);
    try {
      const campaign = await api.post<CampaignSummary>("/campaigns", {
        name: name.trim(),
        template_id: templateId || null,
        audience: { object_type: objectType, filters: [] },
      });
      router.push(`/marketing/campaigns/${campaign.id}`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title="New campaign"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !name.trim()} onClick={() => void create()}>
            Continue
          </button>
        </>
      }
    >
      <div className="ui-stack">
        <Field label="Campaign name" required>
          <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Spring renewal reminders" />
        </Field>
        <Field label="Send to">
          <select className="ui-select" value={objectType} onChange={(e) => setObjectType(e.target.value)}>
            <option value="contact">Contacts</option>
            <option value="lead">Leads</option>
          </select>
        </Field>
        <Field label="Start from a template" hint="Optional. You can edit the email in the next step.">
          <select className="ui-select" value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
            <option value="">Blank email</option>
            {(templates.data ?? []).map((template) => (
              <option key={template.id} value={template.id}>
                {template.name}
              </option>
            ))}
          </select>
        </Field>
      </div>
    </Modal>
  );
}
