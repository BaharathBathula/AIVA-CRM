"use client";

import Link from "next/link";
import { GitBranch, Settings2 } from "lucide-react";
import { useEffect, useState } from "react";

import { CustomFieldsSummary } from "@/components/data-settings";
import { SettingsShell } from "@/components/settings-shell";
import { Alert, Badge, Card, EmptyState, LoadingRows, PageHeader, type Tone } from "@/components/ui";
import { getPipelineStages, getPipelines } from "@/lib/pipelines";
import { errorMessage } from "@/lib/use-api";
import type { Pipeline, PipelineStage } from "@/types/pipeline";

const CATEGORY_TONE: Record<string, Tone> = { open: "info", won: "success", lost: "danger" };

export default function DealFieldsPage() {
  return (
    <SettingsShell>
      <DealFields />
    </SettingsShell>
  );
}

function DealFields() {
  const [pipelines, setPipelines] = useState<(Pipeline & { stages: PipelineStage[] })[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const list = await getPipelines();
        const withStages = await Promise.all(list.map(async (pipeline) => ({ ...pipeline, stages: await getPipelineStages(pipeline.id) })));
        if (active) setPipelines(withStages.sort((a, b) => Number(b.is_default) - Number(a.is_default) || a.name.localeCompare(b.name)));
      } catch (caught) {
        if (active) setError(errorMessage(caught));
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  return (
    <>
      <PageHeader
        title="Deal fields & stages"
        description="Your sales pipelines, the stages deals move through, and the extra fields on every opportunity."
        actions={
          <Link className="ui-btn ui-btn-primary" href="/pipeline">
            <Settings2 size={15} /> Manage pipelines
          </Link>
        }
      />
      <div className="ui-stack">
        {error ? <Alert>{error}</Alert> : null}
        {!pipelines && !error ? (
          <div className="ui-card">
            <LoadingRows rows={4} />
          </div>
        ) : null}
        {pipelines && !pipelines.length ? (
          <div className="ui-card">
            <EmptyState icon={<GitBranch size={20} />} title="No pipelines yet" message="Create a pipeline from the Pipeline page to start tracking deals." />
          </div>
        ) : null}
        {pipelines?.map((pipeline) => (
          <Card
            key={pipeline.id}
            title={pipeline.name}
            icon={<GitBranch size={16} />}
            actions={
              <span className="ui-row" style={{ gap: 6 }}>
                {pipeline.is_default ? <Badge tone="primary">Default</Badge> : null}
                {!pipeline.is_active ? <Badge>Inactive</Badge> : null}
              </span>
            }
          >
            <ol className="dm-stage-flow">
              {[...pipeline.stages]
                .sort((a, b) => a.position - b.position)
                .map((stage) => (
                  <li key={stage.id}>
                    <strong>{stage.name}</strong>
                    <span className="ui-row" style={{ gap: 6 }}>
                      <Badge tone={CATEGORY_TONE[stage.category] ?? "neutral"}>{stage.category}</Badge>
                      <span className="ui-muted ui-small">{stage.probability}% likely</span>
                    </span>
                  </li>
                ))}
            </ol>
          </Card>
        ))}
        <CustomFieldsSummary objectType="opportunity" title="Custom deal fields" />
      </div>
    </>
  );
}
