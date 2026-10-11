"use client";

import { useState } from "react";
import { Loader2, AlertTriangle } from "lucide-react";

import {
  deactivatePipelineStage,
} from "@/lib/pipelines";

import type {
  PipelineStage,
} from "@/types/pipeline";

type Props = {
  pipelineId: string;
  stage: PipelineStage;
  onClose: () => void;
  onDeactivated: (stageId: string) => void;
};

export function DeactivateStageDialog({
  pipelineId,
  stage,
  onClose,
  onDeactivated,
}: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDeactivate() {
    if (submitting) return;

    setSubmitting(true);
    setError(null);

    try {
      await deactivatePipelineStage(
        pipelineId,
        stage.id
      );

      onDeactivated(stage.id);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to deactivate this stage."
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="aiva-stage-overlay fixed inset-0 z-[1100] flex items-center justify-center bg-slate-950/60 p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (
          event.target === event.currentTarget &&
          !submitting
        ) {
          onClose();
        }
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="deactivate-stage-title"
        aria-describedby="deactivate-stage-description"
        className="aiva-stage-dialog w-full max-w-md rounded-xl bg-white p-6 shadow-2xl"
      >
        <div className="mb-4 flex items-center gap-3">
          <div className="rounded-full bg-amber-100 p-3 text-amber-700">
            <AlertTriangle size={22} />
          </div>

          <h2
            id="deactivate-stage-title"
            className="text-xl font-semibold text-slate-900"
          >
            Deactivate Stage
          </h2>
        </div>

        <p
          id="deactivate-stage-description"
          className="mb-4 text-sm leading-6 text-slate-600"
        >
          Are you sure you want to deactivate{" "}
          <strong className="text-slate-900">
            {stage.name}
          </strong>
          ?
        </p>

        <div className="aiva-stage-warning mb-5 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          This stage will no longer be available for new
          opportunity assignments. Deactivation is blocked
          if the stage contains opportunities or is the
          last active stage in the pipeline.
        </div>

        {error && (
          <div
            role="alert"
            className="aiva-stage-error mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700"
          >
            {error}
          </div>
        )}

        <div className="aiva-stage-actions flex justify-end gap-3">
          <button
            type="button"
            disabled={submitting}
            onClick={onClose}
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            Cancel
          </button>

          <button
            type="button"
            disabled={submitting}
            onClick={handleDeactivate}
            className="aiva-stage-danger inline-flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
          >
            {submitting && (
              <Loader2
                size={15}
                className="animate-spin"
              />
            )}

            {submitting
              ? "Deactivating..."
              : "Deactivate Stage"}
          </button>
        </div>
      </div>
    </div>
  );
}
