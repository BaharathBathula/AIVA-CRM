"use client";

import {
  Bookmark,
  CalendarDays,
  Flag,
  ListFilter,
  Target,
  Trophy,
} from "lucide-react";

export type OpportunityViewId =
  | "all_opportunities"
  | "closing_this_month"
  | "high_priority"
  | "commit_forecast"
  | "won_deals";

interface OpportunitySavedViewsProps {
  activeView: OpportunityViewId | null;
  onSelectView: (view: OpportunityViewId) => void;
}

const BUILT_IN_VIEWS = [
  {
    id: "all_opportunities",
    label: "All Opportunities",
    icon: ListFilter,
  },
  {
    id: "closing_this_month",
    label: "Closing This Month",
    icon: CalendarDays,
  },
  {
    id: "high_priority",
    label: "High Priority",
    icon: Flag,
  },
  {
    id: "commit_forecast",
    label: "Commit Forecast",
    icon: Target,
  },
  {
    id: "won_deals",
    label: "Won Deals",
    icon: Trophy,
  },
] as const;

export function OpportunitySavedViews({
  activeView,
  onSelectView,
}: OpportunitySavedViewsProps) {
  return (
    <section
      className="opportunitySavedViews"
      aria-label="Opportunity saved views"
    >
      <div className="opportunitySavedViewsHeader">
        <Bookmark size={16} aria-hidden="true" />
        <span>Saved Views</span>
      </div>

      <div
        className="opportunitySavedViewsList"
        role="group"
        aria-label="Filter opportunities by saved view"
      >
        {BUILT_IN_VIEWS.map((view) => {
          const Icon = view.icon;
          const isActive = activeView === view.id;

          return (
            <button
              key={view.id}
              type="button"
              className={
                "opportunitySavedViewButton" +
                (isActive ? " active" : "")
              }
              aria-pressed={isActive}
              onClick={() => onSelectView(view.id)}
            >
              <Icon size={15} aria-hidden="true" />
              <span>{view.label}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
