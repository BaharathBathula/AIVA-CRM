"use client";

import { Bookmark, Trash2 } from "lucide-react";

import type {
  OpportunityCustomSavedView,
} from "@/lib/opportunities/saved-views";

interface OpportunityCustomViewsProps {
  views: OpportunityCustomSavedView[];
  activeViewId: string | null;
  onSelect: (view: OpportunityCustomSavedView) => void;
  onDelete: (view: OpportunityCustomSavedView) => void;
}

export function OpportunityCustomViews({
  views,
  activeViewId,
  onSelect,
  onDelete,
}: OpportunityCustomViewsProps) {
  if (views.length === 0) {
    return null;
  }

  return (
    <section
      className="opportunitySavedViews"
      aria-label="Custom opportunity views"
    >
      <div className="opportunitySavedViewsHeader">
        <Bookmark size={16} aria-hidden="true" />
        <span>My Saved Views</span>
      </div>

      <div className="opportunitySavedViewsList">
        {views.map((view) => {
          const active = activeViewId === view.id;

          return (
            <div
              key={view.id}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <button
                type="button"
                className={
                  "opportunitySavedViewButton" +
                  (active ? " active" : "")
                }
                aria-pressed={active}
                onClick={() => onSelect(view)}
              >
                <Bookmark size={14} aria-hidden="true" />
                <span>{view.name}</span>
              </button>

              <button
                type="button"
                className="opportunitySavedViewButton"
                aria-label={`Delete saved view ${view.name}`}
                title={`Delete ${view.name}`}
                onClick={() => onDelete(view)}
              >
                <Trash2 size={14} aria-hidden="true" />
              </button>
            </div>
          );
        })}
      </div>
    </section>
  );
}
