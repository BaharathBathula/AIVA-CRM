"use client";

import { useState, type FormEvent } from "react";

import {
  updatePipelineStage,
} from "@/lib/pipelines";

import type {
  PipelineStage,
} from "@/types/pipeline";

type Props = {
  pipelineId: string;
  stage: PipelineStage;
  onClose: () => void;
  onUpdated: (stage: PipelineStage) => void;
};

export function EditStageDialog({
  pipelineId,
  stage,
  onClose,
  onUpdated,
}: Props) {
  const [name, setName] = useState(stage.name);
  const [probability, setProbability] = useState(
    String(stage.probability)
  );
  const [category, setCategory] = useState<
    "open" | "won" | "lost"
  >(stage.category as "open" | "won" | "lost");

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(
    event: FormEvent<HTMLFormElement>
  ) {
    event.preventDefault();

    if (saving) return;

    const normalizedName = name.trim();
    const probabilityValue = Number(probability);

    if (!normalizedName || normalizedName.length > 120) {
      setError("Stage name must contain 1–120 characters.");
      return;
    }

    if (
      probability.trim() === "" ||
      !Number.isInteger(probabilityValue) ||
      probabilityValue < 0 ||
      probabilityValue > 100
    ) {
      setError(
        "Probability must be a whole number between 0 and 100."
      );
      return;
    }

    try {
      setSaving(true);
      setError(null);

      const updated = await updatePipelineStage(
        pipelineId,
        stage.id,
        {
          name: normalizedName,
          probability: probabilityValue,
          category,
        }
      );

      onUpdated(updated);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to update stage."
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      role="presentation"
      className="aiva-stage-overlay fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-stage-title"
        className="aiva-stage-dialog w-full max-w-md rounded-xl bg-white p-6 shadow-xl"
      >
        <h2
          id="edit-stage-title"
          className="text-xl font-semibold text-gray-900"
        >
          Edit Pipeline Stage
        </h2>

        <p className="mt-1 text-sm text-gray-500">
          Update the selected sales pipeline stage.
        </p>

        <form
          onSubmit={handleSubmit}
          className="mt-5 space-y-4"
        >
          <div>
            <label
              htmlFor="edit-stage-name"
              className="mb-1 block text-sm font-medium text-gray-700"
            >
              Stage Name
            </label>
            <input
              id="edit-stage-name"
              autoFocus
              required
              maxLength={120}
              value={name}
              onChange={(event) =>
                setName(event.target.value)
              }
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-gray-900"
            />
          </div>

          <div>
            <label
              htmlFor="edit-stage-probability"
              className="mb-1 block text-sm font-medium text-gray-700"
            >
              Probability (%)
            </label>
            <input
              id="edit-stage-probability"
              type="number"
              min={0}
              max={100}
              step={1}
              required
              value={probability}
              onChange={(event) =>
                setProbability(event.target.value)
              }
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-gray-900"
            />
          </div>

          <div>
            <label
              htmlFor="edit-stage-category"
              className="mb-1 block text-sm font-medium text-gray-700"
            >
              Category
            </label>
            <select
              id="edit-stage-category"
              value={category}
              onChange={(event) =>
                setCategory(
                  event.target.value as
                    "open" | "won" | "lost"
                )
              }
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-gray-900"
            >
              <option value="open">Open</option>
              <option value="won">Won</option>
              <option value="lost">Lost</option>
            </select>
          </div>

          {error && (
            <p
              role="alert"
              className="text-sm text-red-600"
            >
              {error}
            </p>
          )}

          <div className="aiva-stage-actions flex justify-end gap-3 pt-2">
            <button
              type="button"
              disabled={saving}
              onClick={onClose}
              className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700"
            >
              Cancel
            </button>

            <button
              type="submit"
              disabled={saving}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {saving ? "Saving..." : "Save Changes"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
