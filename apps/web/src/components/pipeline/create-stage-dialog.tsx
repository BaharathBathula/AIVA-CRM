"use client";

import { useState, type FormEvent } from "react";
import { createPipelineStage } from "@/lib/pipelines";
import type { PipelineStage } from "@/types/pipeline";

type Props = {
  pipelineId: string;
  onClose: () => void;
  onCreated: (stage: PipelineStage) => void;
};

export function CreateStageDialog({
  pipelineId,
  onClose,
  onCreated,
}: Props) {
  const [name, setName] = useState("");
  const [probability, setProbability] = useState("0");
  const [category, setCategory] =
    useState<"open" | "won" | "lost">("open");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (saving) return;

    const normalizedName = name.trim();
    const value = Number(probability);

    if (!normalizedName || normalizedName.length > 120) {
      setError("Stage name must contain 1–120 characters.");
      return;
    }

    if (
      probability.trim() === "" ||
      !Number.isInteger(value) ||
      value < 0 ||
      value > 100
    ) {
      setError("Probability must be a whole number between 0 and 100.");
      return;
    }

    try {
      setSaving(true);
      setError(null);

      const stage = await createPipelineStage(pipelineId, {
        name: normalizedName,
        probability: value,
        category,
      });

      onCreated(stage);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Unable to create stage."
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      role="presentation"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-stage-title"
        className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl"
      >
        <h2
          id="create-stage-title"
          className="text-xl font-semibold text-gray-900"
        >
          Add Pipeline Stage
        </h2>

        <p className="mt-1 text-sm text-gray-500">
          Create a new stage in the selected sales pipeline.
        </p>

        <form onSubmit={handleSubmit} className="mt-5 space-y-4">
          <div>
            <label
              htmlFor="stage-name"
              className="mb-1 block text-sm font-medium text-gray-700"
            >
              Stage Name
            </label>
            <input
              id="stage-name"
              autoFocus
              required
              maxLength={120}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Technical Review"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-gray-900"
            />
          </div>

          <div>
            <label
              htmlFor="stage-probability"
              className="mb-1 block text-sm font-medium text-gray-700"
            >
              Probability (%)
            </label>
            <input
              id="stage-probability"
              type="number"
              min={0}
              max={100}
              step={1}
              required
              value={probability}
              onChange={(event) => setProbability(event.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-gray-900"
            />
          </div>

          <div>
            <label
              htmlFor="stage-category"
              className="mb-1 block text-sm font-medium text-gray-700"
            >
              Category
            </label>
            <select
              id="stage-category"
              value={category}
              onChange={(event) =>
                setCategory(
                  event.target.value as "open" | "won" | "lost"
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
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-3 pt-2">
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
              {saving ? "Creating..." : "Create Stage"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
