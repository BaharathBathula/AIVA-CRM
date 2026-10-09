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
