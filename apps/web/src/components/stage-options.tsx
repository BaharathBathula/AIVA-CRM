"use client";

import { useApi } from "@/lib/use-api";

/**
 * Account and contact stages come from Settings → Account / Contact fields & stages.
 * These helpers fall back to the built-in stages while the settings load.
 */

export type StageDef = { key: string; label: string; category: string; color: string };

const DEFAULT_ACCOUNT: StageDef[] = [
  { key: "prospect", label: "Prospect", category: "open", color: "blue" },
  { key: "lead", label: "Lead", category: "open", color: "teal" },
  { key: "customer", label: "Customer", category: "positive", color: "green" },
  { key: "partner", label: "Partner", category: "positive", color: "purple" },
  { key: "inactive", label: "Inactive", category: "closed", color: "gray" },
  { key: "churned", label: "Churned", category: "negative", color: "red" },
];

export function useStages(objectType: "account" | "contact") {
  const settings = useApi<{ data: { account: StageDef[]; contact: StageDef[] } }>("/workspace-settings/stages");
  const list = settings.data?.data[objectType] ?? (objectType === "account" ? DEFAULT_ACCOUNT : []);
  const labels = Object.fromEntries(list.map((stage) => [stage.key, stage.label]));
  return { stages: list, labels, loading: settings.loading };
}

/** <option>s for an account stage <select>, keeping a value that's no longer in the list. */
export function AccountStageOptions({ current }: { current?: string | null }) {
  const { stages } = useStages("account");
  const known = stages.some((stage) => stage.key === current);
  return (
    <>
      {stages.map((stage) => (
        <option key={stage.key} value={stage.key}>
          {stage.label}
        </option>
      ))}
      {current && !known ? <option value={current}>{current.replace(/_/g, " ")}</option> : null}
    </>
  );
}
