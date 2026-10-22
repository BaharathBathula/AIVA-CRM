"use client";

import { useEffect, useState, type FormEvent } from "react";

import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Card, Field, LoadingRows, PageHeader } from "@/components/ui";
import { CURRENCIES, MONTHS, TIMEZONES } from "@/lib/constants";
import { date } from "@/lib/format";
import type { Organization } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

export default function OrganizationPage() {
  return (
    <SettingsShell>
      <OrganizationForm />
    </SettingsShell>
  );
}

function OrganizationForm() {
  const { can, refresh } = useSession();
  const toast = useToast();
  const editable = can("admin.settings");
  const { data, loading, setData } = useApi<Organization>("/admin/organization");
  const [name, setName] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [timezone, setTimezone] = useState("UTC");
  const [fiscalStart, setFiscalStart] = useState(1);
  const [timeout, setTimeoutMinutes] = useState(30);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!data) return;
    setName(data.name);
    setCurrency(String(data.settings.currency ?? "USD"));
    setTimezone(String(data.settings.timezone ?? "UTC"));
    setFiscalStart(Number(data.settings.fiscal_year_start_month ?? 1));
    setTimeoutMinutes(Number(data.settings.session_timeout_minutes ?? 30));
  }, [data]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const result = await api.patch<Organization>("/admin/organization", {
        name: name.trim(),
        settings: {
          ...(data?.settings ?? {}),
          currency,
          timezone,
          fiscal_year_start_month: fiscalStart,
          session_timeout_minutes: timeout,
        },
      });
      setData(result);
      await refresh();
      toast("Workspace settings saved.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Organization"
        description="Workspace-wide defaults used for money, dates, forecasting periods and security."
      />
      {loading && !data ? (
        <div className="ui-card">
          <LoadingRows rows={6} />
        </div>
      ) : data ? (
        <form onSubmit={save} className="ui-stack">
          {!editable ? <Alert tone="info">Only administrators can change these settings.</Alert> : null}
          {error ? <Alert>{error}</Alert> : null}
          <Card title="Workspace">
            <div className="ui-form-grid">
              <Field label="Workspace name" required>
                <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} disabled={!editable} required />
              </Field>
              <Field label="Workspace URL name" hint={`Created ${date(data.created_at)} · ${data.member_count} members`}>
                <input className="ui-input" value={data.slug} disabled />
              </Field>
            </div>
          </Card>
          <Card title="Regional">
            <div className="ui-form-grid">
              <Field label="Default currency">
                <select className="ui-select" value={currency} onChange={(e) => setCurrency(e.target.value)} disabled={!editable}>
                  {CURRENCIES.map((code) => (
                    <option key={code}>{code}</option>
                  ))}
                </select>
              </Field>
              <Field label="Default time zone">
                <select className="ui-select" value={timezone} onChange={(e) => setTimezone(e.target.value)} disabled={!editable}>
                  {TIMEZONES.map((zone) => (
                    <option key={zone} value={zone}>
                      {zone.replace(/_/g, " ")}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Fiscal year starts in" hint="Forecast quarters follow your fiscal calendar.">
                <select className="ui-select" value={fiscalStart} onChange={(e) => setFiscalStart(Number(e.target.value))} disabled={!editable}>
                  {MONTHS.map((month, index) => (
                    <option key={month} value={index + 1}>
                      {month}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          </Card>
          <Card
            title="Security policy"
            footer={
              editable ? (
                <button className="ui-btn ui-btn-primary" type="submit" disabled={saving}>
                  {saving ? <span className="ui-spinner" /> : null}
                  Save settings
                </button>
              ) : null
            }
          >
            <div className="ui-form-grid">
              <Field label="Access token lifetime" hint="How long a sign-in stays valid before it silently refreshes.">
                <select className="ui-select" value={timeout} onChange={(e) => setTimeoutMinutes(Number(e.target.value))} disabled={!editable}>
                  {[15, 30, 60, 120, 240].map((minutes) => (
                    <option key={minutes} value={minutes}>
                      {minutes < 60 ? `${minutes} minutes` : `${minutes / 60} hour${minutes > 60 ? "s" : ""}`}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Password policy">
                <input className="ui-input" value="10+ characters, 3 character types" disabled />
              </Field>
            </div>
          </Card>
        </form>
      ) : null}
    </>
  );
}
