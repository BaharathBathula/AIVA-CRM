"use client";

import Link from "next/link";
import { ExternalLink, Globe } from "lucide-react";

import { CopyField } from "@/components/copy-field";
import { SettingsShell } from "@/components/settings-shell";
import { NumberInput, SettingRow, SettingsBody, TagsInput, Toggle, useWorkspaceSettings } from "@/components/settings-kit";
import { Badge, Card, EmptyState, LoadingRows, PageHeader } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { useApi } from "@/lib/use-api";
import type { TrackingSite } from "@/lib/workspace";

type Website = {
  identify_on_form_submit: boolean;
  respect_do_not_track: boolean;
  exclude_paths: string[];
  exclude_ips: string[];
  retention_days: number;
};

export default function WebsiteSettingsPage() {
  return (
    <SettingsShell feature="website_visits">
      <WebsiteSettings />
    </SettingsShell>
  );
}

function snippet(site: TrackingSite, respectDnt: boolean) {
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  return `<script async src="${origin}/aiva-track.js" data-site="${site.site_key}"${respectDnt ? "" : ' data-respect-dnt="false"'}></script>`;
}

function WebsiteSettings() {
  const state = useWorkspaceSettings<Website>("website");
  const sites = useApi<{ items: TrackingSite[]; can_manage: boolean }>("/visits/sites");
  const list = sites.data?.items ?? [];

  return (
    <>
      <PageHeader
        title="Website visitors"
        description="How AIVA tracks visits to your website: who gets identified, what's ignored and how long visit history is kept."
        actions={
          <Link className="ui-btn" href="/visits">
            <ExternalLink size={15} /> Open website visits
          </Link>
        }
      />
      <div className="ui-stack">
        <Card title="Tracking code" icon={<Globe size={16} />}>
          {sites.loading && !sites.data ? (
            <LoadingRows rows={2} />
          ) : list.length ? (
            <div className="ui-stack" style={{ gap: 14 }}>
              <p className="ui-muted ui-small" style={{ margin: 0 }}>
                Paste the line for each site into every page, just before &lt;/head&gt;.
              </p>
              {list.map((site) => (
                <div key={site.id} className="dm-site">
                  <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
                    <strong>{site.name}</strong>
                    <span className="ui-muted ui-small">{site.domain}</span>
                    {site.is_active ? <Badge tone="success">Tracking</Badge> : <Badge>Paused</Badge>}
                    <span className="ui-muted ui-small">{site.last_hit_at ? `Last visit ${relativeTime(site.last_hit_at)}` : "No visits yet"}</span>
                  </div>
                  <CopyField value={snippet(site, state.saved?.respect_do_not_track ?? true)} label="Copy tracking code" />
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              title="No website added yet"
              message={
                <>
                  Add your site on the <Link href="/visits">Website visits</Link> page to get its tracking code.
                </>
              }
            />
          )}
        </Card>

        <SettingsBody state={state}>
          {(draft) => {
            const off = !state.canEdit;
            return (
              <div className="ui-stack">
                <Card title="Identifying visitors">
                  <div className="settings-rows">
                    <SettingRow
                      label="Match visitors who fill in a form"
                      hint="When someone submits one of your web forms, their earlier anonymous visits are linked to their lead or contact."
                    >
                      <Toggle label="Match visitors who fill in a form" checked={draft.identify_on_form_submit} disabled={off} onChange={(value) => state.set("identify_on_form_submit", value)} />
                    </SettingRow>
                    <SettingRow label="Respect “Do Not Track”" hint="Don't record visits from browsers that ask not to be tracked. If you change this, copy the tracking code again — it carries the choice.">
                      <Toggle label="Respect Do Not Track" checked={draft.respect_do_not_track} disabled={off} onChange={(value) => state.set("respect_do_not_track", value)} />
                    </SettingRow>
                  </div>
                </Card>
                <Card title="What to ignore">
                  <div className="settings-rows">
                    <SettingRow label="Pages" hint="Paths that are never recorded, like /careers or /admin. A * at the end matches everything below it." stacked>
                      <TagsInput
                        label="Excluded pages"
                        value={draft.exclude_paths}
                        disabled={off}
                        placeholder="/careers"
                        transform={(value) => (value && !value.startsWith("/") ? `/${value}` : value)}
                        onChange={(value) => state.set("exclude_paths", value)}
                      />
                    </SettingRow>
                    <SettingRow label="IP addresses" hint="Your office or VPN addresses, so your own team's browsing doesn't count. Single addresses or ranges like 203.0.113.0/24." stacked>
                      <TagsInput label="Excluded IP addresses" value={draft.exclude_ips} disabled={off} placeholder="203.0.113.10" onChange={(value) => state.set("exclude_ips", value)} />
                    </SettingRow>
                  </div>
                </Card>
                <Card title="Keeping history">
                  <div className="settings-rows">
                    <SettingRow label="Delete visits older than" hint="Older page views are removed automatically each night. 13 months is a common privacy-friendly choice.">
                      <NumberInput label="Days to keep visits" value={draft.retention_days} min={30} max={1825} disabled={off} onChange={(value) => state.set("retention_days", value ?? 395)} suffix="days" />
                    </SettingRow>
                  </div>
                </Card>
              </div>
            );
          }}
        </SettingsBody>
      </div>
    </>
  );
}
