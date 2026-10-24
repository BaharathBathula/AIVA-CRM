"use client";

import { CustomFieldsSummary, StageEditor } from "@/components/data-settings";
import { SettingsShell } from "@/components/settings-shell";
import { PageHeader } from "@/components/ui";

export default function AccountFieldsPage() {
  return (
    <SettingsShell>
      <PageHeader
        title="Account fields & stages"
        description="The stages companies move through, from lead to customer, and the extra fields you keep on every account."
      />
      <div className="ui-stack">
        <StageEditor objectType="account" />
        <CustomFieldsSummary objectType="account" title="Custom account fields" />
      </div>
    </SettingsShell>
  );
}
