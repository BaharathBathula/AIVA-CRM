"use client";

import { useEffect, useState } from "react";

import type { ForecastCategory } from "@/types/opportunity";

type BulkForecastDialogProps = {
  open: boolean;
  selectedCount: number;
  loading: boolean;
  error?: string | null;
  onClose: () => void;
  onConfirm: (category: ForecastCategory) => void | Promise<void>;
};

const FORECAST_OPTIONS: {
  value: ForecastCategory;
  label: string;
  description: string;
}[] = [
  {
    value: "pipeline",
    label: "Pipeline",
    description: "Opportunity is part of the active sales pipeline.",
  },
  {
    value: "best_case",
    label: "Best Case",
    description: "Potential revenue if favorable conditions are met.",
  },
  {
    value: "commit",
    label: "Commit",
    description: "Revenue expected to close in the forecast period.",
  },
  {
    value: "closed",
    label: "Closed",
    description: "Revenue categorized as closed.",
  },
  {
    value: "omitted",
    label: "Omitted",
    description: "Exclude from the active sales forecast.",
  },
];

export function OpportunityBulkForecastDialog({
  open,
  selectedCount,
  loading,
  error,
  onClose,
  onConfirm,
}: BulkForecastDialogProps) {
  const [category, setCategory] =
    useState<ForecastCategory>("pipeline");

  useEffect(() => {
    if (open) {
      setCategory("pipeline");
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;

    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape" && !loading) {
        onClose();
      }
    }

    window.addEventListener("keydown", handleEscape);

    return () => {
      window.removeEventListener("keydown", handleEscape);
    };
  }, [open, loading, onClose]);

  if (!open) return null;

  return (
    <div className="aiva-forecast-overlay">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="bulk-forecast-title"
        className="aiva-forecast-modal"
      >
        <h2 id="bulk-forecast-title">
          Update Forecast
        </h2>

        <p className="aiva-forecast-description">
          Update the forecast category for{" "}
          <strong>{selectedCount}</strong>{" "}
          selected{" "}
          {selectedCount === 1
            ? "opportunity"
            : "opportunities"}.
        </p>

        <fieldset
          disabled={loading}
          className="aiva-forecast-options"
        >
          <legend>Select forecast category</legend>

          {FORECAST_OPTIONS.map((option) => (
            <label
              key={option.value}
              className={
                category === option.value
                  ? "aiva-forecast-option selected"
                  : "aiva-forecast-option"
              }
            >
              <input
                type="radio"
                name="bulk-forecast"
                value={option.value}
                checked={category === option.value}
                onChange={() => setCategory(option.value)}
              />

              <span>
                <strong>{option.label}</strong>
                <small>{option.description}</small>
              </span>
            </label>
          ))}
        </fieldset>

        {error && (
          <p
            role="alert"
            className="aiva-forecast-error"
          >
            {error}
          </p>
        )}

        <div className="aiva-forecast-actions">
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            className="aiva-forecast-cancel"
          >
            Cancel
          </button>

          <button
            type="button"
            disabled={loading || selectedCount === 0}
            onClick={() => void onConfirm(category)}
            className="aiva-forecast-confirm"
          >
            {loading
              ? "Updating..."
              : "Update Forecast"}
          </button>
        </div>
      </div>
    </div>
  );
}
