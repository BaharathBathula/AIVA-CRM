"use client";

import Link from "next/link";

import { useSession } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { ColorDot, SettingRow, SettingsBody, Toggle, useWorkspaceSettings, type SettingsResponse } from "@/components/settings-kit";
import { Card, PageHeader } from "@/components/ui";
import { useApi } from "@/lib/use-api";

type Prospecting = {
  duplicate_rule: "email" | "email_or_name_company" | "off";
  block_duplicates: boolean;
  auto_enrich_new: boolean;
  default_contact_stage: string;
  sequence_stage: string;
  replied_stage: string;
  finished_stage: string;
  check_suppression_on_import: boolean;
  block_role_addresses: boolean;
};

type Stage = { key: string; label: string; category: string; color: string };

const DUPLICATE_RULES: { value: Prospecting["duplicate_rule"]; label: string }[] = [
  { value: "email", label: "Same email address" },
  { value: "email_or_name_company", label: "Same email, or same name at the same company" },
  { value: "off", label: "Don't check" },
];

export default function ProspectingPage() {
  return (
    <SettingsShell>
      <ProspectingSettings />
    </SettingsShell>
  );
}

function StageSelect({
  label,
  value,
  stages,
  onChange,
  disabled,
  allowNone,
}: {
  label: string;
  value: string;
  stages: Stage[];
  onChange: (value: string) => void;
  disabled?: boolean;
  allowNone?: boolean;
}) {
  const known = stages.some((stage) => stage.key === value);
  const current = stages.find((stage) => stage.key === value);
  return (
    <span className="ui-row" style={{ gap: 8 }}>
      {current ? <ColorDot color={current.color} /> : null}
      <select className="ui-select sm" aria-label={label} style={{ width: 220 }} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
        {allowNone ? <option value="">Don&apos;t change the stage</option> : null}
        {!known && value ? <option value={value}>{value.replace(/_/g, " ")} (removed)</option> : null}
        {stages.map((stage) => (
          <option key={stage.key} value={stage.key}>
            {stage.label}
          </option>
        ))}
      </select>
    </span>
  );
}

function ProspectingSettings() {
  const { has } = useSession();
  const state = useWorkspaceSettings<Prospecting>("prospecting");
  const enrichment = useApi<SettingsResponse<{ auto_enrich_new: boolean }>>("/workspace-settings/enrichment");
  const stages = useApi<SettingsResponse<{ contact: Stage[] }>>("/workspace-settings/stages");
  const contactStages = stages.data?.data.contact ?? [];
  const off = !state.canEdit;

  return (
    <>
      <PageHeader
        title="Prospecting configuration"
        description="How AIVA handles new people: duplicate checks, the do-not-contact list, and which contact stage people move through as you reach out."
      />
      <SettingsBody state={state}>
        {(draft) => (
          <div className="ui-stack">
            <Card title="New leads and contacts">
              <div className="settings-rows">
                <SettingRow label="Treat as a duplicate when" hint="AIVA warns about possible duplicates when someone adds a lead or contact.">
                  <select
                    className="ui-select sm"
                    aria-label="Duplicate rule"
                    style={{ width: 320 }}
                    value={draft.duplicate_rule}
                    disabled={off}
                    onChange={(event) => state.set("duplicate_rule", event.target.value as Prospecting["duplicate_rule"])}
                  >
                    {DUPLICATE_RULES.map((rule) => (
                      <option key={rule.value} value={rule.value}>
                        {rule.label}
                      </option>
                    ))}
                  </select>
                </SettingRow>
                {draft.duplicate_rule !== "off" ? (
                  <SettingRow label="Block duplicates" hint="Refuse to save a duplicate instead of only warning about it.">
                    <Toggle label="Block duplicates" checked={draft.block_duplicates} disabled={off} onChange={(value) => state.set("block_duplicates", value)} />
                  </SettingRow>
                ) : null}
                <SettingRow label="Block shared inboxes" hint="Don't allow addresses like info@, sales@ or support@ on new leads and contacts.">
                  <Toggle label="Block shared inboxes" checked={draft.block_role_addresses} disabled={off} onChange={(value) => state.set("block_role_addresses", value)} />
                </SettingRow>
                <SettingRow
                  label="Enrich new records automatically"
                  hint={
                    has("data_enrichment") ? (
                      <>
                        Fill in what AIVA can work out for free, such as the company from the email domain and seniority from the job title, on every new
                        lead, contact and account. Paid providers run from <Link href="/settings/enrichment">Waterfall enrichment</Link>.
                      </>
                    ) : (
                      "Fill in what AIVA can work out for free on every new record. Needs data enrichment in your plan."
                    )
                  }
                >
                  <span className="ui-row" style={{ gap: 10 }}>
                    <span className="ui-small ui-strong">{enrichment.data?.data.auto_enrich_new ? "On" : "Off"}</span>
                    <Link href="/settings/enrichment" className="ui-btn ui-btn-sm">
                      Change in Waterfall enrichment
                    </Link>
                  </span>
                </SettingRow>
                <SettingRow
                  label="Skip the do-not-contact list on import"
                  hint={
                    <>
                      Leave out people on the <Link href="/settings/removal-requests">do-not-contact list</Link> when importing a file. People who asked to
                      be erased are always left out.
                    </>
                  }
                >
                  <Toggle
                    label="Check the do-not-contact list on import"
                    checked={draft.check_suppression_on_import}
                    disabled={off}
                    onChange={(value) => state.set("check_suppression_on_import", value)}
                  />
                </SettingRow>
              </div>
            </Card>

            <Card title="Contact stages">
              <p className="ui-muted ui-small" style={{ marginTop: 0 }}>
                AIVA moves contacts through these stages as you reach out to them. Change the list of stages in{" "}
                <Link href="/settings/contact-fields">Contact fields &amp; stages</Link>.
              </p>
              <div className="settings-rows">
                <SettingRow label="New contacts start as">
                  <StageSelect
                    label="Stage for new contacts"
                    value={draft.default_contact_stage}
                    stages={contactStages}
                    disabled={off}
                    onChange={(value) => state.set("default_contact_stage", value)}
                  />
                </SettingRow>
                <SettingRow label="When added to a sequence" hint="Leave unchanged if you don't want sequences to touch the stage.">
                  <StageSelect
                    label="Stage when added to a sequence"
                    value={draft.sequence_stage}
                    stages={contactStages}
                    disabled={off}
                    allowNone
                    onChange={(value) => state.set("sequence_stage", value)}
                  />
                </SettingRow>
                <SettingRow label="When they reply">
                  <StageSelect
                    label="Stage when they reply"
                    value={draft.replied_stage}
                    stages={contactStages}
                    disabled={off}
                    allowNone
                    onChange={(value) => state.set("replied_stage", value)}
                  />
                </SettingRow>
                <SettingRow label="When a sequence ends without a reply">
                  <StageSelect
                    label="Stage when a sequence finishes"
                    value={draft.finished_stage}
                    stages={contactStages}
                    disabled={off}
                    allowNone
                    onChange={(value) => state.set("finished_stage", value)}
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
