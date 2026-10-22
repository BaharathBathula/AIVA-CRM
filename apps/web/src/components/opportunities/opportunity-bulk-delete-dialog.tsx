"use client";

import { useEffect, useState } from "react";

type OpportunityBulkDeleteDialogProps = {
  open: boolean;
  selectedCount: number;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: () => void | Promise<void>;
};

export function OpportunityBulkDeleteDialog({
  open,
  selectedCount,
  loading,
  error,
  onClose,
  onConfirm,
}: OpportunityBulkDeleteDialogProps) {
  const [confirmed, setConfirmed] = useState(false);


  useEffect(() => {
    if (!open) return;

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !loading) {
        onClose();
      }
    };

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
        aria-labelledby="bulk-delete-title"
        className="aiva-forecast-modal"
      >
        <h2 id="bulk-delete-title">
          Delete Opportunities
        </h2>

        <p className="aiva-forecast-description">
          You are about to delete{" "}
          <strong>{selectedCount}</strong>{" "}
          {selectedCount === 1
            ? "opportunity"
            : "opportunities"}.
        </p>

        <p className="aiva-forecast-description">
          These opportunities will be removed from
          active CRM views. Their records will be
          retained for audit and potential recovery.
        </p>

        <label
          className="aiva-forecast-option"
          style={{ marginTop: 20 }}
        >
          <input
            type="checkbox"
            checked={confirmed}
            disabled={loading}
            onChange={(event) =>
              setConfirmed(event.target.checked)
            }
          />

          <span>
            <strong>
              I confirm that I want to delete
              the selected opportunities.
            </strong>
          </span>
        </label>

        {error && (
          <p role="alert" className="aiva-forecast-error">
            {error}
          </p>
        )}

        <div className="aiva-forecast-actions">
          <button
            type="button"
            className="aiva-forecast-cancel"
            onClick={onClose}
            disabled={loading}
          >
            Cancel
          </button>

          <button
            type="button"
            onClick={() => void onConfirm()}
            disabled={
              !confirmed ||
              loading ||
              selectedCount === 0
            }
            style={{
              background: "var(--danger)",
              color: "var(--on-ink)",
              border: "1px solid var(--danger)",
            }}
          >
            {loading
              ? "Deleting..."
              : "Delete Opportunities"}
          </button>
        </div>
      </div>
    </div>
  );
}
