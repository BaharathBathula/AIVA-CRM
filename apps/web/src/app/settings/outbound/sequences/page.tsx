"use client";

import Link from "next/link";

import { SettingsShell } from "@/components/settings-shell";
import { NumberInput, SettingRow, SettingsBody, Toggle, useWorkspaceSettings } from "@/components/settings-kit";
import { Card, PageHeader } from "@/components/ui";

type SequenceDefaults = {
  stop_on_reply: boolean;
  stop_on_meeting: boolean;
  stop_on_bounce: boolean;
  one_active_sequence: boolean;
  allow_reenroll: boolean;
  thread_follow_ups: boolean;
  task_due_hours: number;
};

export default function SequenceSettingsPage() {
  return (
    <SettingsShell>
      <SequenceSettings />
    </SettingsShell>
  );
}

function SequenceSettings() {
  const state = useWorkspaceSettings<SequenceDefaults>("sequences");
  const off = !state.canEdit;
  return (
    <>
      <PageHeader
        title="Sequence settings"
        description={
          <>
            Rules every sequence follows. A sequence can still turn off “stop on reply” or “stop on meeting” for itself in its own settings.{" "}
            <Link href="/sequences">Go to sequences</Link>
          </>
        }
      />
      <SettingsBody state={state}>
        {(draft) => (
          <div className="ui-stack">
            <Card title="When someone should leave a sequence">
              <div className="settings-rows">
                <SettingRow label="They reply" hint="Any reply from the person, picked up from the sender's synced mailbox, finishes the sequence for them.">
                  <Toggle label="Stop when they reply" checked={draft.stop_on_reply} disabled={off} onChange={(value) => state.set("stop_on_reply", value)} />
                </SettingRow>
                <SettingRow label="A meeting is booked with them" hint="Booking a meeting in AIVA with their email address on the invite finishes the sequence.">
                  <Toggle label="Stop when a meeting is booked" checked={draft.stop_on_meeting} disabled={off} onChange={(value) => state.set("stop_on_meeting", value)} />
                </SettingRow>
                <SettingRow label="An email to them bounces" hint="When on, they're also added to the do-not-contact list and their contact stage changes to Bad data. When off, the bounce is recorded but they stay in the sequence (useful if your mail server reports temporary failures as bounces).">
                  <Toggle label="Stop when an email bounces" checked={draft.stop_on_bounce} disabled={off} onChange={(value) => state.set("stop_on_bounce", value)} />
                </SettingRow>
              </div>
            </Card>

            <Card title="Enrolling people">
              <div className="settings-rows">
                <SettingRow label="One sequence at a time" hint="Skip people who are already active in another sequence, so nobody gets two streams of email at once.">
                  <Toggle label="Only one active sequence per person" checked={draft.one_active_sequence} disabled={off} onChange={(value) => state.set("one_active_sequence", value)} />
                </SettingRow>
                <SettingRow label="Allow enrolling someone again" hint="Let people who finished or left a sequence be added to the same sequence again later.">
                  <Toggle label="Allow re-enrolling" checked={draft.allow_reenroll} disabled={off} onChange={(value) => state.set("allow_reenroll", value)} />
                </SettingRow>
              </div>
            </Card>

            <Card title="Emails and tasks">
              <div className="settings-rows">
                <SettingRow
                  label="Send follow-ups in the same thread"
                  hint="Follow-up emails go out as replies (“Re: …”) to the first email, unless a step is set to start a new thread."
                >
                  <Toggle label="Thread follow-ups" checked={draft.thread_follow_ups} disabled={off} onChange={(value) => state.set("thread_follow_ups", value)} />
                </SettingRow>
                <SettingRow label="Manual steps are due within" hint="Call, LinkedIn, manual email and to-do steps become tasks for the sender, due this long after the step starts.">
                  <NumberInput
                    label="Hours until a step's task is due"
                    value={draft.task_due_hours}
                    min={1}
                    max={336}
                    disabled={off}
                    onChange={(value) => state.set("task_due_hours", value ?? 24)}
                    suffix="hours"
                    width={100}
                  />
                </SettingRow>
              </div>
            </Card>
          </div>
        )}
      </SettingsBody>
    </>
  );
}
