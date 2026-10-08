"use client";

import {
  CheckSquare2,
  ChevronDown,
  Layers3,
  Trash2,
  UserRound,
  Flag,
  Target,
  X,
} from "lucide-react";

import type {
  OpportunityBulkActionType,
} from "@/lib/opportunities/bulk-actions";

interface OpportunityBulkToolbarProps {
  selectedCount: number;
  visibleSelectedCount: number;
  visibleCount: number;
  allVisibleSelected: boolean;
  onSelectAllVisible: () => void;
  onClearSelection: () => void;
  onAction: (action: OpportunityBulkActionType) => void;
}

const actions = [
  {
    id: "change_owner",
    label: "Change Owner",
    icon: UserRound,
  },
  {
    id: "change_stage",
    label: "Change Stage",
    icon: Layers3,
  },
  {
    id: "change_priority",
    label: "Change Priority",
    icon: Flag,
  },
  {
    id: "change_forecast",
    label: "Update Forecast",
    icon: Target,
  },
  {
    id: "delete",
    label: "Delete",
    icon: Trash2,
  },
] as const;

export function OpportunityBulkToolbar({
  selectedCount,
  visibleSelectedCount,
  visibleCount,
  allVisibleSelected,
  onSelectAllVisible,
  onClearSelection,
  onAction,
}: OpportunityBulkToolbarProps) {
  if (selectedCount === 0) {
    return null;
  }

  return (
    <section
      aria-label="Opportunity bulk actions"
      style={{
        padding: "14px 18px",
        borderTop: "1px solid var(--border)",
        borderBottom: "1px solid var(--border)",
        background: "rgba(99,102,241,0.05)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
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
          <CheckSquare2 size={18} color="#6366f1" />

          <div>
            <strong style={{ fontSize: 13 }}>
              {selectedCount} selected
            </strong>

            <div style={{ fontSize: 12, opacity: 0.7 }}>
              {visibleSelectedCount} selected in current view
            </div>
          </div>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 8,
          }}
        >
          {!allVisibleSelected && visibleCount > 0 && (
            <button
              type="button"
              className="opportunitySavedViewButton"
              onClick={onSelectAllVisible}
            >
              <CheckSquare2 size={14} />
              Select all {visibleCount} visible
            </button>
          )}

          {actions.map((action) => {
            const Icon = action.icon;

            return (
              <button
                key={action.id}
                type="button"
                className="opportunitySavedViewButton"
                onClick={() => onAction(action.id)}
                style={
                  action.id === "delete"
                    ? { color: "#dc2626" }
                    : undefined
                }
              >
                <Icon size={14} />
                {action.label}
              </button>
            );
          })}

          <button
            type="button"
            className="opportunitySavedViewButton"
            onClick={onClearSelection}
          >
            <X size={14} />
            Clear
          </button>
        </div>
      </div>
    </section>
  );
}
