"use client";

import { SettingsShell } from "@/components/settings-shell";
import { SettingRow, SettingsBody, Toggle, useWorkspaceSettings } from "@/components/settings-kit";
import { Alert, Card, PageHeader } from "@/components/ui";
import { useApi } from "@/lib/use-api";

type Tracking = {
  open_tracking: boolean;
  click_tracking: boolean;
  unsubscribe_link: boolean;
  unsubscribe_text: string;
  tracking_domain: string | null;
};

export default function TrackingPage() {
  return (
    <SettingsShell>
      <TrackingSettings />
    </SettingsShell>
  );
}

function TrackingSettings() {
  const state = useWorkspaceSettings<Tracking>("tracking");
  // The domains list tells us the address a tracking domain's CNAME should point to.
  const domains = useApi<{ app_host: string | null }>("/outbound/domains");
  const appHost = domains.data?.app_host ?? "your AIVA address";
  const off = !state.canEdit;
  return (
    <>
      <PageHeader
        title="Tracking"
        description="What AIVA adds to sequence emails to see opens and clicks, and the unsubscribe link every email carries."
      />
      <SettingsBody state={state}>
        {(draft) => (
          <div className="ui-stack">
            <Card title="Opens and clicks">
              <div className="settings-rows">
                <SettingRow
                  label="Track opens"
                  hint="Adds a tiny invisible image to each email. Apple Mail and some security scanners load images automatically, so open rates run high; treat them as a rough signal and trust replies more."
                >
                  <Toggle label="Track opens" checked={draft.open_tracking} disabled={off} onChange={(value) => state.set("open_tracking", value)} />
                </SettingRow>
                <SettingRow label="Track clicks" hint="Links go through a short redirect so AIVA can see who clicked. The person still lands on the same page.">
                  <Toggle label="Track clicks" checked={draft.click_tracking} disabled={off} onChange={(value) => state.set("click_tracking", value)} />
                </SettingRow>
                <SettingRow
                  label="Custom tracking domain"
                  hint={
                    <>
                      Optional, and recommended once you send in volume. Use a subdomain of your sending domain, like track.yourcompany.com, and add a
                      CNAME record pointing it to <span className="ui-mono">{appHost}</span>. Leave blank to use AIVA&apos;s address.
                    </>
                  }
                  stacked
                >
                  <input
                    className="ui-input sm"
                    aria-label="Custom tracking domain"
                    placeholder="track.yourcompany.com"
                    autoCapitalize="none"
                    style={{ maxWidth: 360 }}
                    value={draft.tracking_domain ?? ""}
                    disabled={off}
                    onChange={(event) => state.set("tracking_domain", event.target.value.trim() ? event.target.value : null)}
                  />
                </SettingRow>
              </div>
            </Card>

            <Card title="Unsubscribe">
              <div className="settings-rows">
                <SettingRow
                  label="Add an unsubscribe link"
                  hint="Gmail and Yahoo require a one-click unsubscribe for anyone sending in bulk. People who unsubscribe go on the do-not-contact list and leave every sequence."
                >
                  <Toggle label="Add an unsubscribe link" checked={draft.unsubscribe_link} disabled={off} onChange={(value) => state.set("unsubscribe_link", value)} />
                </SettingRow>
                {!draft.unsubscribe_link ? (
                  <Alert tone="warning">
                    Without an unsubscribe link, large mailbox providers are much more likely to send your emails to spam. The one-click unsubscribe
                    header is still added to every email.
                  </Alert>
                ) : (
                  <SettingRow label="Link text" hint="Shown at the bottom of each email, followed by the link." stacked>
                    <input
                      className="ui-input sm"
                      aria-label="Unsubscribe link text"
                      maxLength={200}
                      value={draft.unsubscribe_text}
                      disabled={off}
                      onChange={(event) => state.set("unsubscribe_text", event.target.value)}
                    />
                  </SettingRow>
                )}
              </div>
            </Card>
          </div>
        )}
      </SettingsBody>
    </>
  );
}
