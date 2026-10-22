"use client";

import { AppShell } from "@/components/app-shell";
import { WorkflowBuilder } from "@/components/workflow-builder";

export default function NewWorkflowPage() {
  return (
    <AppShell permission="automation.workflows" what="workflows">
      <WorkflowBuilder />
    </AppShell>
  );
}
