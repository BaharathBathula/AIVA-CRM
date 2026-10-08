export interface OpportunitySavedViewFilters {
  query: string;
  stageFilter: string;
  priorityFilter: string;
  pipelineFilter: string;
  forecastFilter: string;
  opportunityTypeFilter: string;
  closeDateFilter: string;
  sortOption: string;
}

export interface OpportunityCustomSavedView {
  id: string;
  name: string;
  filters: OpportunitySavedViewFilters;
  createdAt: string;
  updatedAt: string;
}

export const OPPORTUNITY_SAVED_VIEWS_STORAGE_KEY =
  "aiva.crm.opportunities.savedViews.v1";

export function loadOpportunitySavedViews():
  OpportunityCustomSavedView[] {
  if (typeof window === "undefined") {
    return [];
  }

  try {
    const stored = window.localStorage.getItem(
      OPPORTUNITY_SAVED_VIEWS_STORAGE_KEY
    );

    if (!stored) {
      return [];
    }

    const parsed: unknown = JSON.parse(stored);

    if (!Array.isArray(parsed)) {
      return [];
    }

    return parsed.filter(
      (item): item is OpportunityCustomSavedView =>
        typeof item === "object" &&
        item !== null &&
        typeof item.id === "string" &&
        typeof item.name === "string" &&
        typeof item.filters === "object" &&
        item.filters !== null
    );
  } catch {
    return [];
  }
}

export function saveOpportunitySavedViews(
  views: OpportunityCustomSavedView[]
): void {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.setItem(
    OPPORTUNITY_SAVED_VIEWS_STORAGE_KEY,
    JSON.stringify(views)
  );
}
