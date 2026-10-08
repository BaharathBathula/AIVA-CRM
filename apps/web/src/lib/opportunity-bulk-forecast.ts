import type { ForecastCategory } from "@/types/opportunity";

export type BulkForecastResult = {
  updated_count: number;
  updated_ids: string[];
  requested_count: number;
  forecast_category: ForecastCategory;
};

const ALLOWED_FORECAST_CATEGORIES: ForecastCategory[] = [
  "pipeline",
  "best_case",
  "commit",
  "closed",
  "omitted",
];

export async function bulkChangeOpportunityForecast(
  opportunityIds: string[],
  forecastCategory: ForecastCategory,
): Promise<BulkForecastResult> {
  if (opportunityIds.length === 0) {
    throw new Error("Select at least one opportunity.");
  }

  if (opportunityIds.length > 100) {
    throw new Error(
      "You can update a maximum of 100 opportunities at once.",
    );
  }

  if (new Set(opportunityIds).size !== opportunityIds.length) {
    throw new Error("Duplicate opportunity IDs are not allowed.");
  }

  if (!ALLOWED_FORECAST_CATEGORIES.includes(forecastCategory)) {
    throw new Error("Invalid forecast category.");
  }

  const { getAivaAccessToken } = await import("@/lib/auth");
  const token = getAivaAccessToken();

  if (!token) {
    throw new Error(
      "Your session has expired. Please sign in again.",
    );
  }

  const response = await fetch(
    "/api/aiva/opportunities/bulk/forecast",
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        opportunity_ids: opportunityIds,
        forecast_category: forecastCategory,
      }),
    },
  );

  if (!response.ok) {
    let message = `Forecast update failed (${response.status}).`;

    try {
      const body: unknown = await response.json();

      if (
        body &&
        typeof body === "object" &&
        "detail" in body &&
        typeof body.detail === "string"
      ) {
        message = body.detail;
      }
    } catch {
      // Preserve the HTTP error when the response is not JSON.
    }

    throw new Error(message);
  }

  return (await response.json()) as BulkForecastResult;
}
