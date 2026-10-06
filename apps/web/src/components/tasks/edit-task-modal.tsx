"use client";

import {
  FormEvent,
  useEffect,
  useState,
} from "react";

import {
  Pencil,
  X,
} from "lucide-react";

import {
  updateTask,
} from "@/lib/tasks";

import type {
  Task,
  TaskPriority,
  TaskStatus,
  TaskType,
} from "@/types/task";


type Props = {
  open: boolean;
  task: Task | null;

  onClose: () => void;

  onUpdated: (
    task: Task
  ) => void;
};


function toLocalDateTime(
  value: string | null
) {
  if (!value) {
    return "";
  }

  const date =
    new Date(value);

  const offset =
    date.getTimezoneOffset();

  return new Date(
    date.getTime()
    - offset * 60_000
  )
    .toISOString()
    .slice(0, 16);
}


export function EditTaskModal({
  open,
  task,
  onClose,
  onUpdated,
}: Props) {
  const [title, setTitle] =
    useState("");

  const [
    description,
    setDescription,
  ] = useState("");

  const [
    taskType,
    setTaskType,
  ] = useState<TaskType>(
    "general"
  );

  const [
    priority,
    setPriority,
  ] = useState<TaskPriority>(
    "medium"
  );

  const [
    status,
    setStatus,
  ] = useState<TaskStatus>(
    "open"
  );

  const [dueAt, setDueAt] =
    useState("");

  const [saving, setSaving] =
    useState(false);

  const [error, setError] =
    useState<string | null>(
      null
    );


  useEffect(() => {
    if (!task) {
      return;
    }

    setTitle(task.title);

    setDescription(
      task.description || ""
    );

    setTaskType(
      task.task_type
    );

    setPriority(
      task.priority
    );

    setStatus(
      task.status
    );

    setDueAt(
      toLocalDateTime(
        task.due_at
      )
    );

    setError(null);
  }, [task]);


  if (
    !open ||
    !task
  ) {
    return null;
  }


  async function submit(
    event: FormEvent
  ) {
    event.preventDefault();

    if (
      !task ||
      !title.trim()
    ) {
      return;
    }

    try {
      setSaving(true);
      setError(null);

      const updated =
        await updateTask(
          task.id,
          {
            title:
              title.trim(),

            description:
              description.trim()
                || null,

            task_type:
              taskType,

            priority,

            status,

            due_at:
              dueAt
                ? new Date(
                    dueAt
                  ).toISOString()
                : null,
          }
        );

      onUpdated(updated);
      onClose();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to update task."
      );
    } finally {
      setSaving(false);
    }
  }


  return (
    <div className="modalBackdrop">
      <div className="modalCard">
        <div className="modalHeader">
          <div>
            <div className="modalTitleIcon">
              <Pencil
                size={18}
              />
            </div>

            <div>
              <h2>
                Edit Task
              </h2>

              <p>
                Update task details,
                status and priority.
              </p>
            </div>
          </div>

          <button
            type="button"
            className="modalClose"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </div>

        <form
          className="accountForm"
          onSubmit={submit}
        >
          <label>
            Task title

            <input
              required
              value={title}
              onChange={(event) =>
                setTitle(
                  event.target.value
                )
              }
            />
          </label>

          <label>
            Description

            <textarea
              rows={4}
              value={description}
              onChange={(event) =>
                setDescription(
                  event.target.value
                )
              }
            />
          </label>

          <div className="formGrid">
            <label>
              Type

              <select
                value={taskType}
                onChange={(event) =>
                  setTaskType(
                    event.target.value as TaskType
                  )
                }
              >
                <option value="general">
                  General
                </option>

                <option value="call">
                  Call
                </option>

                <option value="email">
                  Email
                </option>

                <option value="meeting">
                  Meeting
                </option>

                <option value="follow_up">
                  Follow Up
                </option>

                <option value="demo">
                  Demo
                </option>

                <option value="proposal">
                  Proposal
                </option>

                <option value="review">
                  Review
                </option>

                <option value="renewal">
                  Renewal
                </option>
              </select>
            </label>

            <label>
              Priority

              <select
                value={priority}
                onChange={(event) =>
                  setPriority(
                    event.target.value as TaskPriority
                  )
                }
              >
                <option value="low">
                  Low
                </option>

                <option value="medium">
                  Medium
                </option>

                <option value="high">
                  High
                </option>

                <option value="urgent">
                  Urgent
                </option>
              </select>
            </label>

            <label>
              Status

              <select
                value={status}
                onChange={(event) =>
                  setStatus(
                    event.target.value as TaskStatus
                  )
                }
              >
                <option value="open">
                  Open
                </option>

                <option value="in_progress">
                  In Progress
                </option>

                <option value="completed">
                  Completed
                </option>

                <option value="cancelled">
                  Cancelled
                </option>
              </select>
            </label>

            <label>
              Due date

              <input
                type="datetime-local"
                value={dueAt}
                onChange={(event) =>
                  setDueAt(
                    event.target.value
                  )
                }
              />
            </label>
          </div>

          {error && (
            <div className="formError">
              {error}
            </div>
          )}

          <div className="modalFooter">
            <button
              type="button"
              className="secondaryButton"
              onClick={onClose}
            >
              Cancel
            </button>

            <button
              type="submit"
              className="createButton"
              disabled={
                saving ||
                !title.trim()
              }
            >
              {saving
                ? "Saving..."
                : "Save Changes"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
