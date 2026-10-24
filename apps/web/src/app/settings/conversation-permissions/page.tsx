"use client";

import Link from "next/link";

import { SettingsShell } from "@/components/settings-shell";
import { SettingRow, SettingsBody, Toggle, useWorkspaceSettings } from "@/components/settings-kit";
import { Card, PageHeader } from "@/components/ui";

type Access = {
  view: "participants" | "team" | "everyone";
  managers_see_reports: boolean;
  download: "owner" | "managers" | "everyone";
  delete: "owner" | "admins";
  reviewers_see_all: boolean;
};

const VIEW: { value: Access["view"]; label: string; hint: string }[] = [
  { value: "everyone", label: "Everyone in the workspace", hint: "Anyone can open any conversation. Good for small teams that learn from each other." },
  { value: "team", label: "The owner's team", hint: "People see conversations owned by someone on their own team, plus ones they took part in." },
  { value: "participants", label: "Only people on the call", hint: "The owner and the people who attended. Use the options below to let managers and coaches in." },
];

export default function ConversationPermissionsPage() {
  return (
    <SettingsShell feature="conversation_intelligence">
      <ConversationPermissions />
    </SettingsShell>
  );
}

function ConversationPermissions() {
  const state = useWorkspaceSettings<Access>("conversation_access");
  const off = !state.canEdit;
  return (
    <>
      <PageHeader
        title="Team permissions"
        description={
          <>
            Who can see, play, download and delete conversations. Who can score calls is part of each role&apos;s permissions (
            <Link href="/settings/roles">Permission profiles</Link> → “Coach conversations”).
          </>
        }
      />
      <SettingsBody state={state}>
        {(draft) => (
          <div className="ui-stack">
            <Card title="Viewing">
              <div className="settings-rows">
                <SettingRow label="Who can open a conversation" hint={VIEW.find((v) => v.value === draft.view)?.hint}>
                  <select
                    className="ui-select sm"
                    aria-label="Who can open a conversation"
                    style={{ width: 260 }}
                    value={draft.view}
                    disabled={off}
                    onChange={(event) => state.set("view", event.target.value as Access["view"])}
                  >
                    {VIEW.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </SettingRow>
                <SettingRow label="Managers see their reports' calls" hint="People whose role can see all records can open every conversation.">
                  <Toggle label="Managers see their reports' calls" checked={draft.managers_see_reports} disabled={off} onChange={(value) => state.set("managers_see_reports", value)} />
                </SettingRow>
                <SettingRow label="Coaches see every call" hint="People who can score conversations can open all of them to review.">
                  <Toggle label="Coaches see every call" checked={draft.reviewers_see_all} disabled={off} onChange={(value) => state.set("reviewers_see_all", value)} />
                </SettingRow>
              </div>
            </Card>

            <Card title="Recordings and deleting">
              <div className="settings-rows">
                <SettingRow label="Who can play and download recordings" hint="Only applies when audio is kept (Recording configuration → Keep the audio).">
                  <select
                    className="ui-select sm"
                    aria-label="Who can play and download recordings"
                    style={{ width: 260 }}
                    value={draft.download}
                    disabled={off}
                    onChange={(event) => state.set("download", event.target.value as Access["download"])}
                  >
                    <option value="owner">Only the conversation&apos;s owner</option>
                    <option value="managers">The owner and managers</option>
                    <option value="everyone">Anyone who can open it</option>
                  </select>
                </SettingRow>
                <SettingRow label="Who can delete a conversation" hint="Admins can always delete. Deleting removes the transcript, notes, recording and reviews.">
                  <select
                    className="ui-select sm"
                    aria-label="Who can delete a conversation"
                    style={{ width: 260 }}
                    value={draft.delete}
                    disabled={off}
                    onChange={(event) => state.set("delete", event.target.value as Access["delete"])}
                  >
                    <option value="owner">The owner and admins</option>
                    <option value="admins">Only admins</option>
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
