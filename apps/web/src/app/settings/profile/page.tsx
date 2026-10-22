"use client";

import { useEffect, useState, type FormEvent } from "react";

import { useSession, useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Avatar, Card, Field, PageHeader } from "@/components/ui";
import { dateTime } from "@/lib/format";
import { TIMEZONES } from "@/lib/constants";
import { api, errorMessage } from "@/lib/use-api";


export default function ProfilePage() {
  return (
    <SettingsShell>
      <ProfileForm />
    </SettingsShell>
  );
}

function ProfileForm() {
  const { session, refresh } = useSession();
  const toast = useToast();
  const user = session?.user;
  const [fullName, setFullName] = useState("");
  const [jobTitle, setJobTitle] = useState("");
  const [phone, setPhone] = useState("");
  const [timezone, setTimezone] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    setFullName(user.full_name);
    setJobTitle(user.job_title ?? "");
    setPhone(user.phone ?? "");
    setTimezone(user.timezone ?? "");
  }, [user]);

  if (!session || !user) return null;

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.patch("/auth/me", {
        full_name: fullName.trim(),
        job_title: jobTitle.trim() || null,
        phone: phone.trim() || null,
        timezone: timezone || null,
      });
      await refresh();
      toast("Profile saved.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <PageHeader eyebrow="Settings" title="Your profile" description="How you appear to teammates on records, cases and activity." />
      <div className="ui-stack">
        <Card>
          <div className="ui-row" style={{ gap: 14 }}>
            <Avatar name={user.full_name} size={52} />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 17, fontWeight: 700 }}>{user.full_name}</div>
              <div className="ui-muted ui-small">
                {user.email} · {session.role_name} at {session.organization.name}
              </div>
              <div className="ui-muted ui-small">Last sign-in {dateTime(user.last_login_at)}</div>
            </div>
          </div>
        </Card>

        <form onSubmit={save}>
          <Card
            title="Details"
            footer={
              <button className="ui-btn ui-btn-primary" type="submit" disabled={saving}>
                {saving ? <span className="ui-spinner" /> : null}
                Save changes
              </button>
            }
          >
            {error ? (
              <div style={{ marginBottom: 14 }}>
                <Alert>{error}</Alert>
              </div>
            ) : null}
            <div className="ui-form-grid">
              <Field label="Full name" required>
                <input className="ui-input" value={fullName} onChange={(e) => setFullName(e.target.value)} required />
              </Field>
              <Field label="Email" hint="Contact an admin to change the email you sign in with.">
                <input className="ui-input" value={user.email} disabled />
              </Field>
              <Field label="Job title">
                <input className="ui-input" value={jobTitle} onChange={(e) => setJobTitle(e.target.value)} placeholder="Account executive" />
              </Field>
              <Field label="Phone">
                <input className="ui-input" value={phone} onChange={(e) => setPhone(e.target.value)} type="tel" />
              </Field>
              <Field label="Time zone" hint="Used for due dates and reminders.">
                <select className="ui-select" value={timezone} onChange={(e) => setTimezone(e.target.value)}>
                  <option value="">Workspace default ({String(session.organization.settings.timezone ?? "UTC")})</option>
                  {TIMEZONES.map((zone) => (
                    <option key={zone} value={zone}>
                      {zone.replace(/_/g, " ")}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          </Card>
        </form>
      </div>
    </>
  );
}
