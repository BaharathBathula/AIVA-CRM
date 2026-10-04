import {
  aivaRequest,
} from "@/lib/api";

import type {
  Task,
  TaskCreatePayload,
  TaskPriority,
  TaskStatus,
  TaskType,
  TaskUpdatePayload,
} from "@/types/task";


export interface TaskFilters {
  accountId?: string;
  contactId?: string;
  leadId?: string;
  opportunityId?: string;

  assignedToUserId?: string;

  status?: TaskStatus;
  priority?: TaskPriority;
  taskType?: TaskType;

  isAiGenerated?: boolean;
  isOverdue?: boolean;

  search?: string;

  skip?: number;
  limit?: number;
}


export function getTasks(
  filters: TaskFilters = {}
): Promise<Task[]> {
  const params =
    new URLSearchParams();

  if (filters.accountId) {
    params.set(
      "account_id",
      filters.accountId
    );
  }

  if (filters.contactId) {
    params.set(
      "contact_id",
      filters.contactId
    );
  }

  if (filters.leadId) {
    params.set(
      "lead_id",
      filters.leadId
    );
  }

  if (filters.opportunityId) {
    params.set(
      "opportunity_id",
      filters.opportunityId
    );
  }

  if (filters.assignedToUserId) {
    params.set(
      "assigned_to_user_id",
      filters.assignedToUserId
    );
  }

  if (filters.status) {
    params.set(
      "status",
      filters.status
    );
  }

  if (filters.priority) {
    params.set(
      "priority",
      filters.priority
    );
  }

  if (filters.taskType) {
    params.set(
      "task_type",
      filters.taskType
    );
  }

  if (
    filters.isAiGenerated
    !== undefined
  ) {
    params.set(
      "is_ai_generated",
      String(
        filters.isAiGenerated
      )
    );
  }

  if (
    filters.isOverdue
    !== undefined
  ) {
    params.set(
      "is_overdue",
      String(
        filters.isOverdue
      )
    );
  }

  if (filters.search) {
    params.set(
      "search",
      filters.search
    );
  }

  if (
    filters.skip
    !== undefined
  ) {
    params.set(
      "skip",
      String(filters.skip)
    );
  }

  if (
    filters.limit
    !== undefined
  ) {
    params.set(
      "limit",
      String(filters.limit)
    );
  }

  const query =
    params.toString();

  return aivaRequest<Task[]>(
    `/tasks${
      query ? `?${query}` : ""
    }`
  );
}


export function getTask(
  taskId: string
): Promise<Task> {
  return aivaRequest<Task>(
    `/tasks/${taskId}`
  );
}


export function createTask(
  payload: TaskCreatePayload
): Promise<Task> {
  return aivaRequest<Task>(
    "/tasks",
    {
      method: "POST",
      body: JSON.stringify(
        payload
      ),
    }
  );
}


export function updateTask(
  taskId: string,
  payload: TaskUpdatePayload
): Promise<Task> {
  return aivaRequest<Task>(
    `/tasks/${taskId}`,
    {
      method: "PATCH",
      body: JSON.stringify(
        payload
      ),
    }
  );
}


export function updateTaskStatus(
  taskId: string,
  taskStatus: TaskStatus
): Promise<Task> {
  return aivaRequest<Task>(
    `/tasks/${taskId}/status`,
    {
      method: "PATCH",
      body: JSON.stringify({
        status: taskStatus,
      }),
    }
  );
}


export function assignTask(
  taskId: string,
  userId: string | null
): Promise<Task> {
  return aivaRequest<Task>(
    `/tasks/${taskId}/assign`,
    {
      method: "PATCH",
      body: JSON.stringify({
        assigned_to_user_id:
          userId,
      }),
    }
  );
}


export function completeTask(
  taskId: string
): Promise<Task> {
  return aivaRequest<Task>(
    `/tasks/${taskId}/complete`,
    {
      method: "POST",
    }
  );
}


export function reopenTask(
  taskId: string
): Promise<Task> {
  return aivaRequest<Task>(
    `/tasks/${taskId}/reopen`,
    {
      method: "POST",
    }
  );
}


export function deleteTask(
  taskId: string
): Promise<void> {
  return aivaRequest<void>(
    `/tasks/${taskId}`,
    {
      method: "DELETE",
    }
  );
}
