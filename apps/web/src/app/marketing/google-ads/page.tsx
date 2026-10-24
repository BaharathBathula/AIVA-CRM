"use client";

import Link from "next/link";
import { Download, KeyRound, MousePointerClick } from "lucide-react";
import { useEffect, useState } from "react";

import { CopyField } from "@/components/copy-field";
import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, Field, Kpi, LoadingRows, PageHeader } from "@/components/ui";
import { compactMoney, dateTime, money } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface GoogleAds {
  connected: boolean;
  integration?: { enabled: boolean; connected: boolean; href: string };
  settings: { lead_conversion_name: string; won_conversion_name: string; timezone: string };
  webhook_url: string;
  has_key: boolean;
  stats: { leads: number; won: number; won_value: number };
  conversions: { kind: "lead" | "won"; gclid: string; name: string; at: string; value: number | null; lead_id: string; campaign: string | null }[];
}

export default function GoogleAdsPage() {
  return (
    <FeaturePage feature="google_ads">
      <GoogleAdsView />
    </FeaturePage>
  );
}

function GoogleAdsView() {
  const toast = useToast();
  const { can } = useSession();
  const data = useApi<GoogleAds>("/google-ads");
  const [settings, setSettings] = useState({ lead_conversion_name: "", won_conversion_name: "", timezone: "" });
  const [key, setKey] = useState<string | null>(null);
  const manage = can("admin.settings");

  useEffect(() => {
    if (data.data) setSettings(data.data.settings);
  }, [data.data]);

  async function save() {
    try {
      data.setData(await api.put<GoogleAds>("/google-ads", settings));
      toast("Google Ads settings saved.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function newKey() {
    try {
      const result = await api.post<{ key: string }>("/google-ads/key");
      setKey(result.key);
      await data.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  if (data.error) return <Alert>{data.error}</Alert>;
  if (!data.data) return <Card><LoadingRows rows={8} /></Card>;
  const d = data.data;

  return (
    <>
      <PageHeader
        title="Google Ads"
        description="Bring Google Ads lead-form leads straight into AIVA, and send closed deals back to Google Ads as offline conversions so it can optimize for revenue."
        actions={
          <a className="ui-btn ui-btn-primary" href="/api/aiva/google-ads/conversions.csv">
            <Download size={15} /> Download conversions
          </a>
        }
      />
      {d.integration ? (
        <div style={{ marginBottom: 16 }}>
          <Alert tone={d.integration.enabled && d.integration.connected ? "success" : "info"}>
            {d.integration.enabled && d.integration.connected ? (
              <>
                The Google Ads integration is connected: conversions upload automatically and campaign spend sits next to your revenue.{" "}
                <Link href={d.integration.href}>Open Google Ads in Integrations</Link>.
              </>
            ) : (
              <>
                Connect the Google Ads integration to upload conversions automatically (no CSV) and see campaign spend next to revenue.{" "}
                <Link href={d.integration.href}>Set it up in Integrations</Link>. The webhook and CSV below keep working.
              </>
            )}
          </Alert>
        </div>
      ) : null}
      <div className="ui-grid ui-grid-3" style={{ marginBottom: 16 }}>
        <Kpi label="Leads from Google Ads" value={d.stats.leads} note="Have a Google click ID" />
        <Kpi label="Won deals" value={d.stats.won} />
        <Kpi label="Won revenue" value={compactMoney(d.stats.won_value)} />
      </div>
      <div className="ui-split" style={{ alignItems: "start" }}>
        <Card title="1. Lead form webhook" icon={<KeyRound size={16} />}>
          <div className="ui-stack">
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              In Google Ads, open your lead form asset → <strong>Lead delivery</strong> → <strong>Webhook integration</strong>, paste this URL and the key below, then
              send test data.
            </p>
            <Field label="Webhook URL">
              <CopyField value={d.webhook_url} label="Copy webhook URL" />
            </Field>
            {key ? (
              <>
                <Alert tone="success">Copy the key now — it won&apos;t be shown again.</Alert>
                <Field label="Key">
                  <CopyField value={key} label="Copy key" />
                </Field>
              </>
            ) : (
              <div className="ui-row" style={{ gap: 10 }}>
                <Badge tone={d.has_key ? "success" : "warning"}>{d.has_key ? "Key set" : "No key yet"}</Badge>
                {manage ? (
                  <button type="button" className="ui-btn ui-btn-sm" onClick={() => void newKey()}>
                    {d.has_key ? "Create a new key" : "Create key"}
                  </button>
                ) : null}
              </div>
            )}
          </div>
        </Card>
        <Card title="2. Offline conversions" icon={<MousePointerClick size={16} />}>
          <div className="ui-stack">
            <p className="ui-muted ui-small" style={{ margin: 0 }}>
              Web forms keep the gclid on every lead. Upload the download in Google Ads → <strong>Goals → Conversions → Uploads</strong>, using these conversion
              action names.
            </p>
            <div className="ui-form-grid">
              <Field label="Lead conversion name">
                <input className="ui-input" value={settings.lead_conversion_name} disabled={!manage} onChange={(e) => setSettings({ ...settings, lead_conversion_name: e.target.value })} />
              </Field>
              <Field label="Closed deal conversion name">
                <input className="ui-input" value={settings.won_conversion_name} disabled={!manage} onChange={(e) => setSettings({ ...settings, won_conversion_name: e.target.value })} />
              </Field>
              <Field label="Time zone">
                <input className="ui-input" value={settings.timezone} disabled={!manage} onChange={(e) => setSettings({ ...settings, timezone: e.target.value })} />
              </Field>
            </div>
            {manage ? (
              <button type="button" className="ui-btn" style={{ width: "fit-content" }} onClick={() => void save()}>
                Save
              </button>
            ) : null}
          </div>
        </Card>
      </div>
      <Card title="Conversions" flush>
        {d.conversions.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Conversion</th>
                  <th>Who</th>
                  <th>Campaign</th>
                  <th>When</th>
                  <th className="ui-num">Value</th>
                </tr>
              </thead>
              <tbody>
                {d.conversions.map((c, i) => (
                  <tr key={`${c.lead_id}-${c.kind}-${i}`}>
                    <td>
                      <Badge tone={c.kind === "won" ? "success" : "info"}>{c.kind === "won" ? d.settings.won_conversion_name : d.settings.lead_conversion_name}</Badge>
                    </td>
                    <td className="cell-title">
                      <Link href={`/leads/${c.lead_id}`}>{c.name}</Link>
                      <div className="ui-muted ui-small ui-mono">{c.gclid}</div>
                    </td>
                    <td>{c.campaign ?? "—"}</td>
                    <td className="ui-muted">{dateTime(c.at)}</td>
                    <td className="ui-num">{c.value !== null ? money(c.value) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty-hint">No Google Ads leads yet. Add ?gclid= tracking (auto-tagging) to ad links pointing at your AIVA forms, or connect the lead form webhook.</p>
        )}
      </Card>
    </>
  );
}
