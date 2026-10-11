"use client";

import { useState } from "react";
import { CheckCircle2, Loader2 } from "lucide-react";

import { reactivatePipelineStage } from "@/lib/pipelines";
import type { PipelineStage } from "@/types/pipeline";

type Props = {
  pipelineId: string;
  stage: PipelineStage;
  onClose: () => void;
  onReactivated: (stage: PipelineStage) => void;
};

export function ReactivateStageDialog({
  pipelineId,
  stage,
  onClose,
  onReactivated,
}: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleReactivate() {
    if (submitting) return;

    setSubmitting(true);
    setError(null);

    try {
      const updated = await reactivatePipelineStage(
        pipelineId,
        stage.id
      );

      onReactivated(updated);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to reactivate this stage."
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
        aria-labelledby="reactivate-stage-title"
        aria-describedby="reactivate-stage-description"
        className="aiva-stage-dialog w-full max-w-md rounded-xl bg-white p-6 shadow-2xl"
      >
        <div className="mb-4 flex items-center gap-3">
          <div className="rounded-full bg-emerald-100 p-3 text-emerald-700">
            <CheckCircle2 size={22} />
          </div>

          <h2
            id="reactivate-stage-title"
            className="text-xl font-semibold text-slate-900"
          >
            Reactivate Stage
          </h2>
        </div>

        <p
          id="reactivate-stage-description"
          className="mb-4 text-sm leading-6 text-slate-600"
        >
          Reactivate{" "}
          <strong className="text-slate-900">
            {stage.name}
          </strong>
          ?
        </p>

        <div className="mb-5 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">
          This stage will become available for new opportunity
          assignments. Its existing name, position, category,
          and probability will be preserved.
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
            onClick={handleReactivate}
            className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {submitting && (
              <Loader2
                size={15}
                className="animate-spin"
              />
            )}

            {submitting
              ? "Reactivating..."
              : "Reactivate Stage"}
          </button>
        </div>
      </div>
    </div>
  );
}
