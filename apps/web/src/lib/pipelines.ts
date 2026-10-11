import {
  aivaRequest,
} from "@/lib/api";

import type {
  Pipeline,
  PipelineStage,
} from "@/types/pipeline";


export function getPipelines(): Promise<
  Pipeline[]
> {
  return aivaRequest<Pipeline[]>(
    "/pipelines"
  );
}


export function getPipeline(
  pipelineId: string
): Promise<Pipeline> {
  return aivaRequest<Pipeline>(
    `/pipelines/${pipelineId}`
  );
}


export function getPipelineStages(
  pipelineId: string
): Promise<PipelineStage[]> {
  return aivaRequest<
    PipelineStage[]
  >(
    `/pipelines/${pipelineId}/stages`
  );
}


export function getPipelineStage(
  pipelineId: string,
  stageId: string
): Promise<PipelineStage> {
  return aivaRequest<PipelineStage>(
    `/pipelines/${pipelineId}/stages/${stageId}`
  );
}


/**
 * Create a sales pipeline with seven default stages.
 */
export function createPipeline(
  name: string
): Promise<Pipeline> {
  return aivaRequest<Pipeline>(
    "/pipelines",
    {
      method: "POST",
      body: JSON.stringify({
        name,
      }),
    }
  );
}


/* AIVA_P44_RENAME_PIPELINE */
export function renamePipeline(
  pipelineId: string,
  name: string
): Promise<Pipeline> {
  return aivaRequest<Pipeline>(
    `/pipelines/${pipelineId}`,
    {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }
  );
}

/* AIVA_P52_CREATE_STAGE */
export type CreatePipelineStageInput = {
  name: string;
  probability: number;
  category: "open" | "won" | "lost";
};

export function createPipelineStage(
  pipelineId: string,
  input: CreatePipelineStageInput
): Promise<PipelineStage> {
  return aivaRequest<PipelineStage>(
    `/pipelines/${pipelineId}/stages`,
    {
      method: "POST",
      body: JSON.stringify(input),
    }
  );
}

/* AIVA_P53_UPDATE_STAGE_CLIENT */
export type UpdatePipelineStageInput = {
  name?: string;
  probability?: number;
  category?: "open" | "won" | "lost";
};

export function updatePipelineStage(
  pipelineId: string,
  stageId: string,
  input: UpdatePipelineStageInput
): Promise<PipelineStage> {
  return aivaRequest<PipelineStage>(
    `/pipelines/${pipelineId}/stages/${stageId}`,
    {
      method: "PATCH",
      body: JSON.stringify(input),
    }
  );
}


/* AIVA_P54_REORDER_STAGE_CLIENT */
export function reorderPipelineStages(
  pipelineId: string,
  stageIds: string[]
): Promise<PipelineStage[]> {
  return aivaRequest<PipelineStage[]>(
    `/pipelines/${pipelineId}/stages/reorder`,
    {
      method: "PUT",
      body: JSON.stringify({
        stage_ids: stageIds,
      }),
    }
  );
}
