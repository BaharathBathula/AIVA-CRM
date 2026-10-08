"use client";

import { useEffect, useState } from "react";
import { UserRound, X } from "lucide-react";

export interface BulkOwnerOption {
  id: string;
  name: string;
  email?: string;
}

interface OpportunityBulkOwnerDialogProps {
  open: boolean;
  selectedCount: number;
  owners: BulkOwnerOption[];
  loading?: boolean;
  onClose: () => void;
  onConfirm: (ownerUserId: string) => void | Promise<void>;
}

export function OpportunityBulkOwnerDialog({
  open,
  selectedCount,
  owners,
  loading = false,
  onClose,
  onConfirm,
}: OpportunityBulkOwnerDialogProps) {
  const [ownerUserId, setOwnerUserId] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setOwnerUserId("");
      setError("");
    }
  }, [open]);

  if (!open) return null;

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!ownerUserId) {
      setError("Please select an opportunity owner.");
      return;
    }

    setError("");
    void onConfirm(ownerUserId);
  }

  return (
    <div
      role="presentation"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 16,
        background: "rgba(15, 23, 42, 0.65)",
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="bulk-owner-dialog-title"
        style={{
          width: "100%",
          maxWidth: 480,
          padding: 24,
          borderRadius: 16,
          background: "var(--background, #ffffff)",
          color: "var(--foreground, #111827)",
          boxShadow: "0 24px 70px rgba(0,0,0,0.25)",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 12,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
            }}
          >
            <UserRound size={20} />

            <h2
              id="bulk-owner-dialog-title"
              style={{ margin: 0, fontSize: 18 }}
            >
              Change Opportunity Owner
            </h2>
          </div>

          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            aria-label="Close dialog"
            style={{
              border: 0,
              background: "transparent",
              cursor: loading ? "not-allowed" : "pointer",
            }}
          >
            <X size={20} />
          </button>
        </div>

        <p style={{ marginTop: 16, fontSize: 14 }}>
          Assign <strong>{selectedCount}</strong>{" "}
          selected {selectedCount === 1 ? "opportunity" : "opportunities"}{" "}
          to a new owner.
        </p>

        <form onSubmit={handleSubmit}>
          <label
            htmlFor="bulk-opportunity-owner"
            style={{
              display: "block",
              marginBottom: 8,
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            New Owner
          </label>

          <select
            id="bulk-opportunity-owner"
            value={ownerUserId}
            onChange={(event) => {
              setOwnerUserId(event.target.value);
              setError("");
            }}
            disabled={loading}
            style={{
              width: "100%",
              padding: 12,
              border: "1px solid #cbd5e1",
              borderRadius: 8,
              background: "var(--background, #ffffff)",
              color: "var(--foreground, #111827)",
            }}
          >
            <option value="">Select an owner</option>

            {owners.map((owner) => (
              <option key={owner.id} value={owner.id}>
                {owner.name}
                {owner.email ? ` (${owner.email})` : ""}
              </option>
            ))}
          </select>

          {owners.length === 0 && (
            <p
              role="status"
              style={{
                marginTop: 8,
                color: "#b45309",
                fontSize: 12,
              }}
            >
              No eligible owners are available.
            </p>
          )}

          {error && (
            <p
              role="alert"
              style={{
                marginTop: 8,
                color: "#dc2626",
                fontSize: 12,
              }}
            >
              {error}
            </p>
          )}

          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              gap: 10,
              marginTop: 24,
            }}
          >
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              style={{
                padding: "10px 16px",
                border: "1px solid #cbd5e1",
                borderRadius: 8,
                background: "transparent",
                cursor: "pointer",
              }}
            >
              Cancel
            </button>

            <button
              type="submit"
              disabled={
                loading ||
                !ownerUserId ||
                owners.length === 0
              }
              style={{
                padding: "10px 16px",
                border: 0,
                borderRadius: 8,
                background: "#4f46e5",
                color: "#ffffff",
                fontWeight: 600,
                cursor: loading ? "wait" : "pointer",
                opacity: loading || !ownerUserId ? 0.6 : 1,
              }}
            >
              {loading ? "Updating..." : "Update Owner"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
