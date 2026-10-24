"use client";

import Link from "next/link";

import { SettingsShell } from "@/components/settings-shell";
import { NumberInput, SecretInput, SettingRow, SettingsBody, Toggle, useWorkspaceSettings } from "@/components/settings-kit";
import { Alert, Card, PageHeader } from "@/components/ui";

type Recording = {
  record_mode: "external" | "all" | "manual" | "none";
  notetaker_name: string;
  consent_notice: string;
  add_consent_to_invites: boolean;
  retention_days: number;
  keep_audio: boolean;
  transcription_provider: "none" | "deepgram" | "assemblyai";
  language: string;
  auto_summarize: boolean;
  redact_numbers: boolean;
};

const MODES: { value: Recording["record_mode"]; label: string; hint: string }[] = [
  { value: "external", label: "Customer meetings", hint: "Keep meetings with people outside your company." },
  { value: "all", label: "Every meeting", hint: "Also keep team meetings (see Team meetings for exceptions)." },
  { value: "manual", label: "Only when added", hint: "Nothing is kept unless someone adds the transcript." },
  { value: "none", label: "Off", hint: "Conversation capture is turned off for the workspace." },
];

const PROVIDERS: { value: Recording["transcription_provider"]; label: string; hint: string }[] = [
  { value: "none", label: "Transcripts only", hint: "Upload the .vtt or .srt file Zoom, Teams or Meet creates. Nothing else to set up." },
  { value: "deepgram", label: "Deepgram", hint: "Upload recordings; transcribed in seconds with speaker labels." },
  { value: "assemblyai", label: "AssemblyAI", hint: "Upload recordings; transcribed in the background within a few minutes." },
];

const LANGUAGES = [
  { value: "en", label: "English" },
  { value: "es", label: "Spanish" },
  { value: "fr", label: "French" },
  { value: "de", label: "German" },
  { value: "pt", label: "Portuguese" },
  { value: "it", label: "Italian" },
  { value: "nl", label: "Dutch" },
  { value: "hi", label: "Hindi" },
  { value: "ja", label: "Japanese" },
];

export default function RecordingPage() {
  return (
    <SettingsShell feature="conversation_intelligence">
      <RecordingSettings />
    </SettingsShell>
  );
}

function RecordingSettings() {
  const state = useWorkspaceSettings<Recording>("recording");
  const off = !state.canEdit;
  const keyName = state.draft && state.draft.transcription_provider !== "none" ? `${state.draft.transcription_provider}_api_key` : null;
  const keyMissing = Boolean(keyName && !state.secretsSet[keyName] && !state.pendingSecrets[keyName]);

  return (
    <>
      <PageHeader
        title="Recording configuration"
        description={
          <>
            What AIVA keeps from your calls and meetings, how people are told, and how recordings become transcripts. Conversations show up in{" "}
            <Link href="/conversations">Home → Conversations</Link>.
          </>
        }
      />
      <SettingsBody state={state}>
        {(draft) => (
          <div className="ui-stack">
            <Card title="What gets captured">
              <div className="settings-rows">
                <SettingRow label="Keep conversations from" hint={MODES.find((m) => m.value === draft.record_mode)?.hint}>
                  <select
                    className="ui-select sm"
                    aria-label="Keep conversations from"
                    style={{ width: 220 }}
                    value={draft.record_mode}
                    disabled={off}
                    onChange={(event) => state.set("record_mode", event.target.value as Recording["record_mode"])}
                  >
                    {MODES.map((mode) => (
                      <option key={mode.value} value={mode.value}>
                        {mode.label}
                      </option>
                    ))}
                  </select>
                </SettingRow>
                <SettingRow label="Notetaker name" hint="How AIVA appears in meeting invites and notes.">
                  <input
                    className="ui-input sm"
                    aria-label="Notetaker name"
                    style={{ maxWidth: 260 }}
                    value={draft.notetaker_name}
                    maxLength={80}
                    disabled={off}
                    onChange={(event) => state.set("notetaker_name", event.target.value)}
                  />
                </SettingRow>
              </div>
            </Card>

            <Card title="Consent">
              <div className="settings-rows">
                <SettingRow label="Add a recording notice to invites" hint="Meetings scheduled from AIVA include the notice below in the invitation.">
                  <Toggle label="Add a recording notice to invites" checked={draft.add_consent_to_invites} disabled={off} onChange={(value) => state.set("add_consent_to_invites", value)} />
                </SettingRow>
                <SettingRow label="Notice" hint="Many states and countries require telling everyone a call is recorded. Keep it short and clear." stacked>
                  <textarea
                    className="ui-textarea"
                    aria-label="Recording notice"
                    rows={3}
                    maxLength={1000}
                    value={draft.consent_notice}
                    disabled={off}
                    onChange={(event) => state.set("consent_notice", event.target.value)}
                  />
                </SettingRow>
              </div>
            </Card>

            <Card title="Transcription">
              <div className="settings-rows">
                <SettingRow label="How recordings become transcripts" stacked>
                  <div className="conv-provider-grid" role="radiogroup" aria-label="Transcription provider">
                    {PROVIDERS.map((provider) => (
                      <button
                        key={provider.value}
                        type="button"
                        role="radio"
                        aria-checked={draft.transcription_provider === provider.value}
                        className={`conv-provider${draft.transcription_provider === provider.value ? " on" : ""}`}
                        disabled={off}
                        onClick={() => state.set("transcription_provider", provider.value)}
                      >
                        <strong>{provider.label}</strong>
                        <span>{provider.hint}</span>
                      </button>
                    ))}
                  </div>
                </SettingRow>
                {keyName ? (
                  <SettingRow
                    label={`${draft.transcription_provider === "deepgram" ? "Deepgram" : "AssemblyAI"} API key`}
                    hint={
                      draft.transcription_provider === "deepgram"
                        ? "Create a key in the Deepgram console (Settings → API keys). It's stored encrypted and never shown again."
                        : "Find your key on the AssemblyAI dashboard. It's stored encrypted and never shown again."
                    }
                  >
                    <SecretInput
                      name={keyName}
                      label={`${draft.transcription_provider} API key`}
                      isSet={Boolean(state.secretsSet[keyName])}
                      pending={state.pendingSecrets[keyName]}
                      onChange={state.setSecret}
                      disabled={off}
                    />
                  </SettingRow>
                ) : null}
                {keyMissing ? (
                  <Alert tone="warning">Add the API key so recordings can be transcribed. Until then, upload transcript files instead.</Alert>
                ) : null}
                <SettingRow label="Language" hint="The main language spoken on your calls.">
                  <select className="ui-select sm" aria-label="Language" style={{ width: 180 }} value={draft.language} disabled={off} onChange={(event) => state.set("language", event.target.value)}>
                    {[...LANGUAGES, ...(LANGUAGES.some((l) => l.value === draft.language) ? [] : [{ value: draft.language, label: draft.language }])].map((language) => (
                      <option key={language.value} value={language.value}>
                        {language.label}
                      </option>
                    ))}
                  </select>
                </SettingRow>
              </div>
            </Card>

            <Card title="Notes and privacy">
              <div className="settings-rows">
                <SettingRow label="Write a summary automatically" hint="A short recap and the next steps people agreed to. Uses AI words when AI is connected.">
                  <Toggle label="Write a summary automatically" checked={draft.auto_summarize} disabled={off} onChange={(value) => state.set("auto_summarize", value)} />
                </SettingRow>
                <SettingRow label="Hide card and ID numbers" hint="Replaces credit card and social security numbers in transcripts with [redacted number].">
                  <Toggle label="Hide card and ID numbers" checked={draft.redact_numbers} disabled={off} onChange={(value) => state.set("redact_numbers", value)} />
                </SettingRow>
              </div>
            </Card>

            <Card title="Retention">
              <div className="settings-rows">
                <SettingRow label="Keep conversations for" hint="After this, the transcript, notes and recording are deleted automatically.">
                  <NumberInput label="Days to keep conversations" value={draft.retention_days} min={7} max={3650} disabled={off} onChange={(value) => state.set("retention_days", value ?? 365)} suffix="days" />
                </SettingRow>
                <SettingRow label="Keep the audio" hint="Off: the recording is deleted once it's transcribed and only the text is kept. On: people allowed to download can play it back.">
                  <Toggle label="Keep the audio" checked={draft.keep_audio} disabled={off} onChange={(value) => state.set("keep_audio", value)} />
                </SettingRow>
              </div>
            </Card>
          </div>
        )}
      </SettingsBody>
    </>
  );
}
