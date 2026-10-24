"use client";

import { ChevronDown, ChevronUp, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { SettingsShell } from "@/components/settings-shell";
import { NumberInput, SecretInput, SettingRow, SettingsBody, Toggle, useWorkspaceSettings } from "@/components/settings-kit";
import { Alert, Card, PageHeader } from "@/components/ui";
import { api, errorMessage } from "@/lib/use-api";

type Outcome = { key: string; label: string; connected: boolean; follow_up: boolean };

type Dialer = {
  calling_method: "device" | "twilio";
  outcomes: Outcome[];
  require_notes: boolean;
  auto_follow_up: boolean;
  follow_up_days: number;
  twilio_account_sid: string | null;
  twilio_from_number: string | null;
  record_calls: boolean;
};

/** "Left voicemail" → "left_voicemail", unique among the other outcomes. */
function keyFor(label: string, taken: string[]) {
  const base = label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 36) || "outcome";
  let key = base;
  let n = 2;
  while (taken.includes(key)) key = `${base}_${n++}`;
  return key;
}

export default function DialerPage() {
  return (
    <SettingsShell>
      <DialerSettings />
    </SettingsShell>
  );
}

function DialerSettings() {
  const state = useWorkspaceSettings<Dialer>("dialer");
  const off = !state.canEdit;
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  function setOutcomes(outcomes: Outcome[]) {
    state.set("outcomes", outcomes);
  }

  async function testTwilio() {
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await api.post<{ ok: boolean; message: string }>("/dialer/test"));
    } catch (caught) {
      setTestResult({ ok: false, message: errorMessage(caught) });
    } finally {
      setTesting(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Dialer"
        description="How reps place calls from AIVA, the outcomes they pick when logging a call, and when a follow-up task is created."
      />
      <SettingsBody state={state}>
        {(draft) => {
          const twilioDirty = state.dirty && draft.calling_method === "twilio";
          return (
            <div className="ui-stack">
              <Card title="Calling">
                <div className="settings-rows">
                  <SettingRow
                    label="How calls are placed"
                    hint="Your device opens the phone app, softphone or calling app set up on the computer or phone. Twilio rings the rep's own phone first, then connects them, showing your Twilio number."
                  >
                    <div className="outbound-segment" role="radiogroup" aria-label="How calls are placed">
                      {(
                        [
                          ["device", "This device"],
                          ["twilio", "Twilio"],
                        ] as const
                      ).map(([value, label]) => (
                        <button
                          key={value}
                          type="button"
                          role="radio"
                          aria-checked={draft.calling_method === value}
                          className={draft.calling_method === value ? "on" : undefined}
                          disabled={off}
                          onClick={() => state.set("calling_method", value)}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  </SettingRow>
                  {draft.calling_method === "twilio" ? (
                    <>
                      <SettingRow label="Account SID" hint="From the Twilio console home page; it starts with AC.">
                        <input
                          className="ui-input sm"
                          aria-label="Twilio account SID"
                          style={{ maxWidth: 360 }}
                          placeholder="AC…"
                          autoComplete="off"
                          value={draft.twilio_account_sid ?? ""}
                          disabled={off}
                          onChange={(event) => state.set("twilio_account_sid", event.target.value.trim() || null)}
                        />
                      </SettingRow>
                      <SettingRow label="Auth token" hint="Stored encrypted and never shown again.">
                        <SecretInput
                          name="twilio_auth_token"
                          label="Twilio auth token"
                          isSet={Boolean(state.secretsSet.twilio_auth_token)}
                          pending={state.pendingSecrets.twilio_auth_token}
                          onChange={state.setSecret}
                          disabled={off}
                        />
                      </SettingRow>
                      <SettingRow label="Twilio phone number" hint="The number people see when you call, in +1 format.">
                        <input
                          className="ui-input sm"
                          type="tel"
                          aria-label="Twilio phone number"
                          style={{ maxWidth: 240 }}
                          placeholder="+12145550100"
                          value={draft.twilio_from_number ?? ""}
                          disabled={off}
                          onChange={(event) => state.set("twilio_from_number", event.target.value.trim() || null)}
                        />
                      </SettingRow>
                      <SettingRow label="Record calls" hint="Twilio records from when the person answers. Many places require telling people a call is recorded.">
                        <Toggle label="Record calls" checked={draft.record_calls} disabled={off} onChange={(value) => state.set("record_calls", value)} />
                      </SettingRow>
                      {!off ? (
                        <SettingRow label="Check the connection" hint={twilioDirty ? "Save your changes first, then test." : "Signs in to Twilio with the saved details."}>
                          <button type="button" className="ui-btn ui-btn-sm" disabled={testing || twilioDirty} onClick={() => void testTwilio()}>
                            {testing ? <span className="ui-spinner" /> : null} Test Twilio
                          </button>
                        </SettingRow>
                      ) : null}
                      {testResult ? <Alert tone={testResult.ok ? "success" : "danger"}>{testResult.message}</Alert> : null}
                    </>
                  ) : null}
                </div>
              </Card>

              <Card
                title="Call outcomes"
                actions={
                  !off ? (
                    <button
                      type="button"
                      className="ui-btn ui-btn-sm"
                      disabled={draft.outcomes.length >= 30}
                      onClick={() =>
                        setOutcomes([...draft.outcomes, { key: keyFor("New outcome", draft.outcomes.map((o) => o.key)), label: "New outcome", connected: false, follow_up: false }])
                      }
                    >
                      <Plus size={14} /> Add outcome
                    </button>
                  ) : null
                }
              >
                <p className="ui-muted ui-small" style={{ marginTop: 0 }}>
                  Reps pick one of these when they log a call. “Reached them” counts toward the connect rate on the Calls page. “Follow up” creates a
                  task automatically when follow-ups are on below.
                </p>
                <div className="settings-list">
                  {draft.outcomes.map((outcome, index) => (
                    // Index keys keep the name box focused while a new outcome's key follows its name.
                    <div key={index} className="settings-list-row">
                      <div className="settings-list-handle">
                        <button
                          type="button"
                          aria-label={`Move ${outcome.label} up`}
                          disabled={off || index === 0}
                          onClick={() => {
                            const next = [...draft.outcomes];
                            [next[index - 1], next[index]] = [next[index], next[index - 1]];
                            setOutcomes(next);
                          }}
                        >
                          <ChevronUp size={14} />
                        </button>
                        <button
                          type="button"
                          aria-label={`Move ${outcome.label} down`}
                          disabled={off || index === draft.outcomes.length - 1}
                          onClick={() => {
                            const next = [...draft.outcomes];
                            [next[index + 1], next[index]] = [next[index], next[index + 1]];
                            setOutcomes(next);
                          }}
                        >
                          <ChevronDown size={14} />
                        </button>
                      </div>
                      <div className="settings-list-fields">
                        <input
                          className="ui-input sm grow"
                          aria-label="Outcome name"
                          maxLength={60}
                          value={outcome.label}
                          disabled={off}
                          onChange={(event) => {
                            const label = event.target.value;
                            const next = [...draft.outcomes];
                            const saved = state.saved?.outcomes.some((o) => o.key === outcome.key);
                            // New outcomes take their key from the name; saved ones keep theirs so past calls still match.
                            next[index] = {
                              ...outcome,
                              label,
                              key: saved ? outcome.key : keyFor(label, draft.outcomes.filter((_, i) => i !== index).map((o) => o.key)),
                            };
                            setOutcomes(next);
                          }}
                        />
                        <label className="ui-check ui-small">
                          <input
                            type="checkbox"
                            checked={outcome.connected}
                            disabled={off}
                            onChange={(event) => {
                              const next = [...draft.outcomes];
                              next[index] = { ...outcome, connected: event.target.checked };
                              setOutcomes(next);
                            }}
                          />
                          Reached them
                        </label>
                        <label className="ui-check ui-small">
                          <input
                            type="checkbox"
                            checked={outcome.follow_up}
                            disabled={off}
                            onChange={(event) => {
                              const next = [...draft.outcomes];
                              next[index] = { ...outcome, follow_up: event.target.checked };
                              setOutcomes(next);
                            }}
                          />
                          Follow up
                        </label>
                      </div>
                      <button
                        type="button"
                        className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                        aria-label={`Remove ${outcome.label}`}
                        disabled={off || draft.outcomes.length <= 1}
                        onClick={() => setOutcomes(draft.outcomes.filter((_, i) => i !== index))}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  ))}
                </div>
              </Card>

              <Card title="Logging calls">
                <div className="settings-rows">
                  <SettingRow label="Require notes" hint="Reps must write at least a line about the call before it's logged.">
                    <Toggle label="Require call notes" checked={draft.require_notes} disabled={off} onChange={(value) => state.set("require_notes", value)} />
                  </SettingRow>
                  <SettingRow label="Create follow-up tasks automatically" hint="For outcomes marked “Follow up”, unless the rep picks a different follow-up.">
                    <Toggle label="Automatic follow-up tasks" checked={draft.auto_follow_up} disabled={off} onChange={(value) => state.set("auto_follow_up", value)} />
                  </SettingRow>
                  {draft.auto_follow_up ? (
                    <SettingRow label="Follow up after" hint="0 means the task is due today.">
                      <NumberInput
                        label="Days until the follow-up is due"
                        value={draft.follow_up_days}
                        min={0}
                        max={60}
                        disabled={off}
                        onChange={(value) => state.set("follow_up_days", value ?? 0)}
                        suffix="days"
                        width={90}
                      />
                    </SettingRow>
                  ) : null}
                </div>
              </Card>
            </div>
          );
        }}
      </SettingsBody>
    </>
  );
}
