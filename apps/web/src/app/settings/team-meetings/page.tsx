"use client";

import { CalendarClock } from "lucide-react";
import { useEffect } from "react";

import { SettingsShell } from "@/components/settings-shell";
import { SettingRow, SettingsBody, TagsInput, Toggle, useWorkspaceSettings } from "@/components/settings-kit";
import { Badge, Card, EmptyState, LoadingRows, PageHeader } from "@/components/ui";
import { dateTime } from "@/lib/format";
import { useApi } from "@/lib/use-api";

type TeamMeetings = {
  record_internal: boolean;
  internal_domains: string[];
  skip_title_keywords: string[];
  share_notes_with_attendees: boolean;
};

type Preview = {
  items: { id: string; title: string; start_at: string; attendees: number; kept: boolean; reason: string | null }[];
  internal_domains: string[];
};

function cleanDomain(value: string) {
  const domain = value
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .replace(/^www\./, "")
    .replace(/^@/, "")
    .split("/")[0];
  return /^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(domain) ? domain : "";
}

export default function TeamMeetingsPage() {
  return (
    <SettingsShell feature="conversation_intelligence">
      <TeamMeetingSettings />
    </SettingsShell>
  );
}

function TeamMeetingSettings() {
  const state = useWorkspaceSettings<TeamMeetings>("team_meetings");
  const preview = useApi<Preview>("/team-meetings");
  const off = !state.canEdit;
  const saved = state.saved;
  const reloadPreview = preview.reload;

  // Show the effect of saved changes on upcoming meetings.
  useEffect(() => {
    if (saved) void reloadPreview();
  }, [saved, reloadPreview]);

  const items = preview.data?.items ?? [];
  const domains = preview.data?.internal_domains ?? [];

  return (
    <>
      <PageHeader
        title="Team meetings"
        description="Meetings where everyone is from your company — stand-ups, pipeline reviews, 1:1s. Decide whether AIVA keeps them and who gets the notes."
      />
      <SettingsBody state={state}>
        {(draft) => (
          <div className="ui-stack">
            <Card title="Keeping internal meetings">
              <div className="settings-rows">
                <SettingRow label="Keep team meetings" hint="Off: transcripts of meetings with only teammates aren't saved (you can still save one on purpose).">
                  <Toggle label="Keep team meetings" checked={draft.record_internal} disabled={off} onChange={(value) => state.set("record_internal", value)} />
                </SettingRow>
                <SettingRow
                  label="Never keep meetings with these words in the title"
                  hint="Private conversations like 1:1s, interviews and performance reviews are skipped even when team meetings are kept."
                  stacked
                >
                  <TagsInput label="Title words to skip" value={draft.skip_title_keywords} disabled={off} placeholder="1:1, interview" onChange={(value) => state.set("skip_title_keywords", value)} />
                </SettingRow>
                <SettingRow
                  label="Your company's email domains"
                  hint={
                    domains.length
                      ? `People with these addresses count as teammates. AIVA already includes your users' domains: ${domains.join(", ")}.`
                      : "People with these addresses count as teammates, in addition to your AIVA users' own domains."
                  }
                  stacked
                >
                  <TagsInput
                    label="Company email domains"
                    value={draft.internal_domains}
                    disabled={off}
                    placeholder="acme.com"
                    transform={cleanDomain}
                    onChange={(value) => state.set("internal_domains", value)}
                  />
                </SettingRow>
              </div>
            </Card>

            <Card title="Sharing notes">
              <div className="settings-rows">
                <SettingRow label="Send the notes to teammates who attended" hint="When a summary is ready, AIVA users on the meeting get a notification with a link to it.">
                  <Toggle label="Send the notes to teammates who attended" checked={draft.share_notes_with_attendees} disabled={off} onChange={(value) => state.set("share_notes_with_attendees", value)} />
                </SettingRow>
              </div>
            </Card>
          </div>
        )}
      </SettingsBody>

      <div style={{ marginTop: 18 }}>
        <Card title="Team meetings around now" icon={<CalendarClock size={16} />} flush>
          {preview.loading && !preview.data ? (
            <LoadingRows rows={4} />
          ) : items.length ? (
            <div className="ui-table-wrap">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th>Meeting</th>
                    <th>When</th>
                    <th className="ui-num">People</th>
                    <th>Kept?</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id}>
                      <td className="cell-title">{item.title}</td>
                      <td className="ui-nowrap">{dateTime(item.start_at)}</td>
                      <td className="ui-num">{item.attendees}</td>
                      <td>
                        {item.kept ? (
                          <Badge tone="success">Kept</Badge>
                        ) : (
                          <span className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
                            <Badge>Skipped</Badge>
                            {item.reason ? <span className="ui-muted ui-small">{item.reason}</span> : null}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState icon={<CalendarClock size={20} />} title="No team meetings in the last or next two weeks" message="Meetings where every attendee is a teammate show up here with what AIVA would do." />
          )}
        </Card>
      </div>
    </>
  );
}
