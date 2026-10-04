export type TaskType =
  | "general"
  | "call"
  | "email"
  | "meeting"
  | "follow_up"
  | "demo"
  | "proposal"
  | "review"
  | "renewal";

export type TaskStatus =
  | "open"
  | "in_progress"
  | "completed"
  | "cancelled";

export type TaskPriority =
  | "low"
  | "medium"
  | "high"
  | "urgent";

export type TaskSource =
  | "manual"
  | "system"
  | "ai"
  | "email"
  | "meeting"
  | "workflow";


export interface Task {
  id: string;
  organization_id: string;

  account_id: string | null;
  contact_id: string | null;
  lead_id: string | null;
  opportunity_id: string | null;

  assigned_to_user_id: string | null;
  created_by_user_id: string | null;

  parent_task_id: string | null;

  title: string;
  description: string | null;

  task_type: TaskType;
  status: TaskStatus;
  priority: TaskPriority;
  source: TaskSource;

  start_at: string | null;
  due_at: string | null;
  reminder_at: string | null;
  completed_at: string | null;

  is_recurring: boolean;
  recurrence_rule: string | null;

  is_ai_generated: boolean;

  external_id: string | null;

  tags: string[];
  task_metadata: Record<
    string,
    unknown
  >;

  created_at: string;
  updated_at: string;
}


export interface TaskCreatePayload {
  account_id?: string | null;
  contact_id?: string | null;
  lead_id?: string | null;
  opportunity_id?: string | null;

  assigned_to_user_id?: string | null;
  parent_task_id?: string | null;

  title: string;
  description?: string | null;

  task_type?: TaskType;
  status?: TaskStatus;
  priority?: TaskPriority;
  source?: TaskSource;

  start_at?: string | null;
  due_at?: string | null;
  reminder_at?: string | null;

  is_recurring?: boolean;
  recurrence_rule?: string | null;

  is_ai_generated?: boolean;

  external_id?: string | null;

  tags?: string[];

  task_metadata?: Record<
    string,
    unknown
  >;
}


export interface TaskUpdatePayload {
  account_id?: string | null;
  contact_id?: string | null;
  lead_id?: string | null;
  opportunity_id?: string | null;

  assigned_to_user_id?: string | null;
  parent_task_id?: string | null;

  title?: string;
  description?: string | null;

  task_type?: TaskType;
  status?: TaskStatus;
  priority?: TaskPriority;
  source?: TaskSource;

  start_at?: string | null;
  due_at?: string | null;
  reminder_at?: string | null;

  is_recurring?: boolean;
  recurrence_rule?: string | null;

  external_id?: string | null;

  tags?: string[];

  task_metadata?: Record<
    string,
    unknown
  >;
}
