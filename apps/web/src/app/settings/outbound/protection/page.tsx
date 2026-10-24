"use client";

import { SettingsShell } from "@/components/settings-shell";
import {
  DaysPicker,
  HourSelect,
  NumberInput,
  SettingRow,
  SettingsBody,
  TIMEZONES,
  TagsInput,
  Toggle,
  useWorkspaceSettings,
} from "@/components/settings-kit";
import { Card, PageHeader } from "@/components/ui";

type Protection = {
  daily_limit_per_mailbox: number;
  hourly_limit_per_mailbox: number;
  min_seconds_between: number;
  bounce_pause_percent: number;
  bounce_window: number;
  skip_role_addresses: boolean;
  send_days: number[];
  window_start_hour: number;
  window_end_hour: number;
  timezone: string;
  holidays: string[];
  mailbox_limits: Record<string, number>;
};

export default function SendingProtectionPage() {
  return (
    <SettingsShell>
      <SendingProtection />
    </SettingsShell>
  );
}

function SendingProtection() {
  const state = useWorkspaceSettings<Protection>("sending");
  const off = !state.canEdit;
  return (
    <>
      <PageHeader
        title="Sending protection"
        description="Guardrails for automatic sequence emails so mailboxes stay healthy and messages land in the inbox. Manual emails you send yourself aren't limited."
      />
      <SettingsBody state={state}>
        {(draft) => (
          <div className="ui-stack">
            <Card title="Sending window">
              <div className="settings-rows">
                <SettingRow label="Days" hint="Automatic emails only go out on these days. Anything due on other days waits for the next sending day.">
                  <DaysPicker value={draft.send_days} disabled={off} onChange={(value) => state.set("send_days", value)} />
                </SettingRow>
                <SettingRow label="Hours" hint="Local time in the time zone below.">
                  <HourSelect label="Start" value={draft.window_start_hour} disabled={off} onChange={(value) => state.set("window_start_hour", value)} />
                  <span className="ui-muted">to</span>
                  <HourSelect label="End" value={draft.window_end_hour} from={1} to={24} disabled={off} onChange={(value) => state.set("window_end_hour", value)} />
                </SettingRow>
                <SettingRow label="Time zone">
                  <select className="ui-select sm" aria-label="Time zone" style={{ width: 240 }} value={draft.timezone} disabled={off} onChange={(event) => state.set("timezone", event.target.value)}>
                    {[draft.timezone, ...TIMEZONES.filter((zone) => zone !== draft.timezone)].map((zone) => (
                      <option key={zone} value={zone}>
                        {zone.replace(/_/g, " ")}
                      </option>
                    ))}
                  </select>
                </SettingRow>
                <SettingRow label="Holidays" hint="Dates (YYYY-MM-DD) when nothing is sent, such as public holidays." stacked>
                  <TagsInput
                    label="Holiday dates"
                    value={draft.holidays}
                    disabled={off}
                    placeholder="2026-12-25"
                    transform={(value) => (/^\d{4}-\d{2}-\d{2}$/.test(value) ? value : "")}
                    onChange={(value) => state.set("holidays", value.sort())}
                  />
                </SettingRow>
              </div>
            </Card>

            <Card title="Limits per mailbox">
              <div className="settings-rows">
                <SettingRow label="Emails per day" hint="Google and Microsoft start filtering new mailboxes that send hundreds a day. 50–200 is a safe range. You can set a different limit for one mailbox on the Mailboxes page.">
                  <NumberInput label="Emails per day" value={draft.daily_limit_per_mailbox} min={1} max={5000} disabled={off} onChange={(value) => state.set("daily_limit_per_mailbox", value ?? 1)} suffix="a day" />
                </SettingRow>
                <SettingRow label="Emails per hour">
                  <NumberInput label="Emails per hour" value={draft.hourly_limit_per_mailbox} min={1} max={1000} disabled={off} onChange={(value) => state.set("hourly_limit_per_mailbox", value ?? 1)} suffix="an hour" />
                </SettingRow>
                <SettingRow label="Time between emails" hint="Spacing emails out looks like a person sending, not a blast.">
                  <NumberInput label="Seconds between emails" value={draft.min_seconds_between} min={0} max={3600} disabled={off} onChange={(value) => state.set("min_seconds_between", value ?? 0)} suffix="seconds" />
                </SettingRow>
              </div>
            </Card>

            <Card title="Bounces and risky addresses">
              <div className="settings-rows">
                <SettingRow label="Pause a mailbox when bounces reach" hint="Measured over the mailbox's most recent emails. Sending resumes once the rate drops.">
                  <NumberInput label="Bounce rate" value={draft.bounce_pause_percent} min={0.5} max={50} step={0.5} disabled={off} onChange={(value) => state.set("bounce_pause_percent", value ?? 5)} suffix="%" width={90} />
                  <span className="ui-muted ui-small">of the last</span>
                  <NumberInput label="Emails measured" value={draft.bounce_window} min={20} max={1000} disabled={off} onChange={(value) => state.set("bounce_window", value ?? 100)} suffix="emails" width={100} />
                </SettingRow>
                <SettingRow label="Skip shared inboxes" hint="Don't enroll addresses like info@, sales@ or support@ in sequences; they rarely reply and often bounce.">
                  <Toggle label="Skip shared inboxes" checked={draft.skip_role_addresses} disabled={off} onChange={(value) => state.set("skip_role_addresses", value)} />
                </SettingRow>
              </div>
            </Card>
          </div>
        )}
      </SettingsBody>
    </>
  );
}
