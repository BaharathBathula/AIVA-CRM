"use client";

import { SettingsShell } from "@/components/settings-shell";
import { NumberInput, SettingRow, SettingsBody, Toggle, useWorkspaceSettings } from "@/components/settings-kit";
import { Card, PageHeader } from "@/components/ui";

type Sharing = {
  document_visibility: "organization" | "private";
  templates_shared: boolean;
  sequences_shared: boolean;
  snippets_shared: boolean;
  reports_shared: boolean;
  meeting_minutes: number;
  task_due_days: number;
  import_owner: "importer" | "territory";
};

export default function SharingPage() {
  return (
    <SettingsShell>
      <SharingSettings />
    </SettingsShell>
  );
}

function SharingSettings() {
  const state = useWorkspaceSettings<Sharing>("sharing");
  const off = !state.canEdit;
  return (
    <>
      <PageHeader
        title="Team sharing & defaults"
        description="What new things start out as — shared with the team or kept private — and the defaults people see when they create something. Anyone can still change the choice on the item itself."
      />
      <SettingsBody state={state}>
        {(draft) => (
          <div className="ui-stack">
            <Card title="Shared with the team by default">
              <div className="settings-rows">
                <SettingRow label="Email templates" hint="New templates are visible to everyone in the workspace.">
                  <Toggle label="Share new email templates" checked={draft.templates_shared} disabled={off} onChange={(value) => state.set("templates_shared", value)} />
                </SettingRow>
                <SettingRow label="Sequences" hint="Shared sequences can be used and edited by anyone who manages sequences.">
                  <Toggle label="Share new sequences" checked={draft.sequences_shared} disabled={off} onChange={(value) => state.set("sequences_shared", value)} />
                </SettingRow>
                <SettingRow label="Snippets">
                  <Toggle label="Share new snippets" checked={draft.snippets_shared} disabled={off} onChange={(value) => state.set("snippets_shared", value)} />
                </SettingRow>
                <SettingRow label="Reports" hint="Private reports are only visible to their owner and admins.">
                  <Toggle label="Share new reports" checked={draft.reports_shared} disabled={off} onChange={(value) => state.set("reports_shared", value)} />
                </SettingRow>
                <SettingRow label="Uploaded documents" hint="Who can see a file uploaded without picking a visibility.">
                  <select
                    className="ui-select sm"
                    aria-label="Document visibility"
                    value={draft.document_visibility}
                    disabled={off}
                    onChange={(event) => state.set("document_visibility", event.target.value as Sharing["document_visibility"])}
                    style={{ width: 230 }}
                  >
                    <option value="organization">Everyone in the workspace</option>
                    <option value="private">Only the uploader and admins</option>
                  </select>
                </SettingRow>
              </div>
            </Card>
            <Card title="Defaults for new items">
              <div className="settings-rows">
                <SettingRow label="Meeting length" hint="Pre-filled when someone schedules a meeting.">
                  <NumberInput label="Meeting length in minutes" value={draft.meeting_minutes} min={5} max={480} step={5} disabled={off} onChange={(value) => state.set("meeting_minutes", value ?? 30)} suffix="minutes" />
                </SettingRow>
                <SettingRow label="Task due date" hint="New tasks are due this many days from today unless someone picks a date. 0 means today.">
                  <NumberInput label="Days until a new task is due" value={draft.task_due_days} min={0} max={60} disabled={off} onChange={(value) => state.set("task_due_days", value ?? 0)} suffix="days" />
                </SettingRow>
                <SettingRow label="Owner of imported accounts and leads" hint="With territories, imported rows without an owner column go to the matching territory's next rep (or to whoever imports them when no territory matches).">
                  <select
                    className="ui-select sm"
                    aria-label="Owner of imported records"
                    value={draft.import_owner}
                    disabled={off}
                    onChange={(event) => state.set("import_owner", event.target.value as Sharing["import_owner"])}
                    style={{ width: 230 }}
                  >
                    <option value="importer">The person importing</option>
                    <option value="territory">Route by territory</option>
                  </select>
                </SettingRow>
              </div>
            </Card>
          </div>
        )}
      </SettingsBody>
    </>
  );
}
