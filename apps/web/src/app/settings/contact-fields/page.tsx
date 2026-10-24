"use client";

import { CustomFieldsSummary, StageEditor } from "@/components/data-settings";
import { SettingsShell } from "@/components/settings-shell";
import { PageHeader } from "@/components/ui";

export default function ContactFieldsPage() {
  return (
    <SettingsShell>
      <PageHeader
        title="Contact fields & stages"
        description="The stages people move through as you reach out, and the extra fields you keep on every contact."
      />
      <div className="ui-stack">
        <StageEditor objectType="contact" />
        <CustomFieldsSummary objectType="contact" title="Custom contact fields" />
      </div>
    </SettingsShell>
  );
}
