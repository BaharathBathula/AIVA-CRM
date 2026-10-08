import type { OpportunityPriority } from "@/types/opportunity";

export type BulkPriorityResult = {
  updated_count: number;
  updated_ids: string[];
  requested_count: number;
  priority: OpportunityPriority;
};

export async function bulkChangeOpportunityPriority(
  opportunityIds: string[],
  priority: OpportunityPriority,
): Promise<BulkPriorityResult> {
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

  const { getAivaAccessToken } = await import("@/lib/auth");
  const token = getAivaAccessToken();

  if (!token) {
    throw new Error(
      "Your session has expired. Please sign in again.",
    );
  }

  const response = await fetch(
    "/api/aiva/opportunities/bulk/priority",
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        opportunity_ids: opportunityIds,
        priority,
      }),
    },
  );

  if (!response.ok) {
    let message = `Priority update failed (${response.status}).`;

    try {
      const body: unknown = await response.json();

      if (
        body &&
        typeof body === "object" &&
        "detail" in body
      ) {
        const detail = body.detail;

        if (typeof detail === "string") {
          message = detail;
        }
      }
    } catch {
      // Keep the HTTP status error when the response is not JSON.
    }

    throw new Error(message);
  }

  return (await response.json()) as BulkPriorityResult;
}
