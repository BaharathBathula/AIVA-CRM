"use client";

import { useEffect, useRef, useState } from "react";
import { BookmarkPlus, X } from "lucide-react";

interface OpportunitySaveViewDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: (name: string) => void;
  existingNames?: string[];
}

export function OpportunitySaveViewDialog({
  open,
  onClose,
  onSave,
  existingNames = [],
}: OpportunitySaveViewDialogProps) {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setName("");
      setError("");
      inputRef.current?.focus();
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;

    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }

    window.addEventListener("keydown", handleEscape);

    return () => {
      window.removeEventListener("keydown", handleEscape);
    };
  }, [open, onClose]);

  if (!open) return null;

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const trimmed = name.trim();

    if (!trimmed) {
      setError("Enter a name for this view.");
      return;
    }

    if (trimmed.length > 60) {
      setError("View names must be 60 characters or fewer.");
      return;
    }

    if (
      existingNames.some(
        (existing) =>
          existing.toLowerCase() === trimmed.toLowerCase()
      )
    ) {
      setError("A saved view with this name already exists.");
      return;
    }

    onSave(trimmed);
  }

  return (
    <div
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 16,
        background: "rgba(15, 23, 42, 0.55)",
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="save-view-title"
        style={{
          width: "100%",
          maxWidth: 440,
          padding: 24,
          borderRadius: 14,
          background: "var(--background, #fff)",
          color: "var(--foreground, #111827)",
          boxShadow: "0 20px 60px rgba(0,0,0,0.2)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            marginBottom: 20,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <BookmarkPlus size={20} />
            <h2
              id="save-view-title"
              style={{ fontSize: 18, fontWeight: 700, margin: 0 }}
            >
              Save Current View
            </h2>
          </div>

          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            style={{
              border: 0,
              background: "transparent",
              color: "inherit",
              cursor: "pointer",
            }}
          >
            <X size={20} />
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <label
            htmlFor="opportunity-view-name"
            style={{
              display: "block",
              marginBottom: 8,
              fontSize: 13,
              fontWeight: 600,
            }}
          >
            View Name
          </label>

          <input
            ref={inputRef}
            id="opportunity-view-name"
            type="text"
            value={name}
            maxLength={60}
            onChange={(event) => {
              setName(event.target.value);
              setError("");
            }}
            placeholder="e.g. My Q4 Enterprise Deals"
            aria-invalid={Boolean(error)}
            aria-describedby={error ? "save-view-error" : undefined}
            style={{
              width: "100%",
              padding: "11px 12px",
              border: "1px solid var(--border, #d1d5db)",
              borderRadius: 8,
              background: "transparent",
              color: "inherit",
              fontSize: 14,
              boxSizing: "border-box",
            }}
          />

          {error && (
            <p
              id="save-view-error"
              role="alert"
              style={{ color: "#dc2626", fontSize: 12, marginTop: 8 }}
            >
              {error}
            </p>
          )}

          <p
            style={{
              marginTop: 10,
              fontSize: 12,
              opacity: 0.7,
            }}
          >
            Save your current opportunity filters and sorting for later use.
          </p>

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
              style={{
                padding: "10px 16px",
                border: "1px solid var(--border, #d1d5db)",
                borderRadius: 8,
                background: "transparent",
                color: "inherit",
                cursor: "pointer",
              }}
            >
              Cancel
            </button>

            <button
              type="submit"
              style={{
                padding: "10px 16px",
                border: 0,
                borderRadius: 8,
                background: "#6366f1",
                color: "#fff",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              Save View
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
