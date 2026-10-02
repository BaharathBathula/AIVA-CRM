export type PipelineStageCategory =
  | "open"
  | "won"
  | "lost";


export interface Pipeline {
  id: string;
  organization_id: string;

  name: string;

  is_default: boolean;
  is_active: boolean;

  created_at: string;
  updated_at: string;
}


export interface PipelineStage {
  id: string;
  organization_id: string;

  pipeline_id: string;

  name: string;

  position: number;
  probability: number;

  category: PipelineStageCategory;

  is_active: boolean;

  created_at: string;
  updated_at: string;
}
