"use client";

import { useEffect, useMemo, useState } from "react";

export interface BulkStageOption {
  id: string;
  name: string;
  pipeline_id: string;
  is_active?: boolean;
}

export interface BulkPipelineOption {
  id: string;
  name: string;
  is_active?: boolean;
  stages: BulkStageOption[];
}

interface OpportunityBulkStageDialogProps {
  open: boolean;
  selectedCount: number;
  pipelines: BulkPipelineOption[];
  loading?: boolean;
  error?: string;
  onClose: () => void;
  onConfirm: (
    pipelineId: string,
    stageId: string
  ) => void | Promise<void>;
}

export function OpportunityBulkStageDialog({
  open,
  selectedCount,
  pipelines,
  loading = false,
  error,
  onClose,
  onConfirm,
}: OpportunityBulkStageDialogProps) {
  const [pipelineId, setPipelineId] = useState("");
  const [stageId, setStageId] = useState("");

  const activePipelines = useMemo(
    () =>
      pipelines.filter(
        (pipeline) => pipeline.is_active !== false
      ),
    [pipelines]
  );

  const selectedPipeline = activePipelines.find(
    (pipeline) => pipeline.id === pipelineId
  );

  const stages = (selectedPipeline?.stages ?? []).filter(
    (stage) => stage.is_active !== false
  );

  useEffect(() => {
    if (!open) {
      setPipelineId("");
      setStageId("");
    }
  }, [open]);

  if (!open) {
    return null;
  }

  const canSubmit =
    !loading &&
    selectedCount > 0 &&
    Boolean(pipelineId) &&
    Boolean(stageId) &&
    stages.some((stage) => stage.id === stageId);

  return (
    <div
      role="presentation"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "rgba(15, 23, 42, 0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 16,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="bulk-stage-title"
        style={{
          width: "100%",
          maxWidth: 460,
          padding: 24,
          background: "#ffffff",
          color: "#0f172a",
          borderRadius: 12,
          boxShadow: "0 20px 50px rgba(0,0,0,0.2)",
        }}
      >
        <h2
          id="bulk-stage-title"
          style={{
            fontSize: 20,
            fontWeight: 700,
            marginBottom: 8,
          }}
        >
          Change Opportunity Stage
        </h2>

        <p
          style={{
            marginBottom: 20,
            color: "#475569",
          }}
        >
          Update {selectedCount} selected{" "}
          {selectedCount === 1
            ? "opportunity"
            : "opportunities"}.
        </p>

        <label
          htmlFor="bulk-pipeline"
          style={{
            display: "block",
            marginBottom: 6,
          }}
        >
          Destination Pipeline
        </label>

        <select
          id="bulk-pipeline"
          value={pipelineId}
          disabled={loading}
          onChange={(event) => {
            setPipelineId(event.target.value);
            setStageId("");
          }}
          style={{
            width: "100%",
            padding: 10,
            marginBottom: 16,
            border: "1px solid #cbd5e1",
            borderRadius: 6,
          }}
        >
          <option value="">Select pipeline</option>

          {activePipelines.map((pipeline) => (
            <option
              key={pipeline.id}
              value={pipeline.id}
            >
              {pipeline.name}
            </option>
          ))}
        </select>

        <label
          htmlFor="bulk-stage"
          style={{
            display: "block",
            marginBottom: 6,
          }}
        >
          Destination Stage
        </label>

        <select
          id="bulk-stage"
          value={stageId}
          disabled={!pipelineId || loading}
          onChange={(event) =>
            setStageId(event.target.value)
          }
          style={{
            width: "100%",
            padding: 10,
            marginBottom: 16,
            border: "1px solid #cbd5e1",
            borderRadius: 6,
          }}
        >
          <option value="">Select stage</option>

          {stages.map((stage) => (
            <option
              key={stage.id}
              value={stage.id}
            >
              {stage.name}
            </option>
          ))}
        </select>

        {error && (
          <p
            role="alert"
            style={{
              color: "#b91c1c",
              fontSize: 13,
              marginBottom: 12,
            }}
          >
            {error}
          </p>
        )}

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 12,
          }}
        >
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            style={{
              padding: "10px 16px",
            }}
          >
            Cancel
          </button>

          <button
            type="button"
            disabled={!canSubmit}
            onClick={() => {
              void onConfirm(pipelineId, stageId);
            }}
            style={{
              padding: "10px 16px",
              borderRadius: 6,
              background: canSubmit
                ? "#2563eb"
                : "#94a3b8",
              color: "#ffffff",
            }}
          >
            {loading
              ? "Updating..."
              : "Update Stage"}
          </button>
        </div>
      </div>
    </div>
  );
}
