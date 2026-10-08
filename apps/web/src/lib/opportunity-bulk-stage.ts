import { aivaRequest } from "@/lib/api";
import {
  getAivaAccessToken,
  getAivaSession,
} from "@/lib/auth";

export interface BulkStageUpdateResult {
  updated_count: number;
  updated_ids: string[];
  requested_count: number;
  pipeline_id: string;
  stage_id: string;
}

export async function bulkChangeOpportunityStage(
  opportunityIds: string[],
  pipelineId: string,
  stageId: string
): Promise<BulkStageUpdateResult> {
  const token = getAivaAccessToken();
  const session = getAivaSession();

  if (!token || !session) {
    throw new Error(
      "Your login session has expired. Please sign in again."
    );
  }

  if (!["owner", "admin"].includes(session.role)) {
    throw new Error(
      "Only organization owners and administrators can change opportunity stages."
    );
  }

  if (
    opportunityIds.length < 1 ||
    opportunityIds.length > 100
  ) {
    throw new Error(
      "Select between 1 and 100 opportunities."
    );
  }

  if (
    new Set(opportunityIds).size !==
    opportunityIds.length
  ) {
    throw new Error(
      "Duplicate opportunity selections are not allowed."
    );
  }

  if (!pipelineId || !stageId) {
    throw new Error(
      "Select both a destination pipeline and stage."
    );
  }

  return aivaRequest<BulkStageUpdateResult>(
    "/opportunities/bulk/stage",
    {
      method: "PATCH",
      body: JSON.stringify({
        opportunity_ids: opportunityIds,
        pipeline_id: pipelineId,
        stage_id: stageId,
      }),
    }
  );
}
