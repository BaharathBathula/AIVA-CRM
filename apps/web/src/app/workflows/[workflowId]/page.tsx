"use client";

import { useParams } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { WorkflowBuilder } from "@/components/workflow-builder";

export default function WorkflowPage() {
  const { workflowId } = useParams<{ workflowId: string }>();
  return (
    <AppShell>
      <WorkflowBuilder key={workflowId} workflowId={workflowId} />
    </AppShell>
  );
}
