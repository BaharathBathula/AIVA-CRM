"use client";

import { useEffect, useState } from "react";

import type { OpportunityPriority } from "@/types/opportunity";

type BulkPriorityDialogProps = {
  open: boolean;
  selectedCount: number;
  loading: boolean;
  error?: string | null;
  onClose: () => void;
  onConfirm: (priority: OpportunityPriority) => void | Promise<void>;
};

const PRIORITY_OPTIONS: {
  value: OpportunityPriority;
  label: string;
  description: string;
}[] = [
  {
    value: "low",
    label: "Low",
    description: "Can be addressed later",
  },
  {
    value: "medium",
    label: "Medium",
    description: "Normal business priority",
  },
  {
    value: "high",
    label: "High",
    description: "Requires prompt attention",
  },
  {
    value: "critical",
    label: "Critical",
    description: "Requires immediate attention",
  },
];

export function OpportunityBulkPriorityDialog({
  open,
  selectedCount,
  loading,
  error,
  onClose,
  onConfirm,
}: BulkPriorityDialogProps) {
  const [priority, setPriority] =
    useState<OpportunityPriority>("medium");

  useEffect(() => {
    if (open) {
      setPriority("medium");
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
    <div
      role="presentation"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="bulk-priority-title"
        className="w-full max-w-md rounded-xl border border-gray-200 bg-white p-6 shadow-xl"
      >
        <h2
          id="bulk-priority-title"
          className="text-xl font-semibold text-gray-900"
        >
          Change Priority
        </h2>

        <p className="mt-2 text-sm text-gray-600">
          Update the priority for{" "}
          <strong>{selectedCount}</strong>{" "}
          selected{" "}
          {selectedCount === 1
            ? "opportunity"
            : "opportunities"}.
        </p>

        <fieldset
          disabled={loading}
          className="mt-5 space-y-3"
        >
          <legend className="mb-3 text-sm font-medium text-gray-800">
            Select new priority
          </legend>

          {PRIORITY_OPTIONS.map((option) => (
            <label
              key={option.value}
              className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 ${
                priority === option.value
                  ? "border-blue-600 bg-blue-50"
                  : "border-gray-200"
              }`}
            >
              <input
                type="radio"
                name="bulk-priority"
                value={option.value}
                checked={priority === option.value}
                onChange={() => setPriority(option.value)}
                className="mt-1"
              />

              <span>
                <span className="block text-sm font-semibold text-gray-900">
                  {option.label}
                </span>

                <span className="block text-xs text-gray-600">
                  {option.description}
                </span>
              </span>
            </label>
          ))}
        </fieldset>

        {error && (
          <p
            role="alert"
            className="mt-4 text-sm text-red-600"
          >
            {error}
          </p>
        )}

        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 disabled:opacity-50"
          >
            Cancel
          </button>

          <button
            type="button"
            disabled={loading || selectedCount === 0}
            onClick={() => void onConfirm(priority)}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            {loading
              ? "Updating..."
              : "Update Priority"}
          </button>
        </div>
      </div>
    </div>
  );
}
