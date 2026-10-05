"use client";

import {
  FormEvent,
  useState,
} from "react";

import {
  ListTodo,
  X,
} from "lucide-react";

import {
  createTask,
} from "@/lib/tasks";

import type {
  Task,
  TaskPriority,
  TaskType,
} from "@/types/task";


type Props = {
  open: boolean;

  onClose: () => void;

  onCreated: (
    task: Task
  ) => void;
};


export function CreateTaskModal({
  open,
  onClose,
  onCreated,
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

  const [dueAt, setDueAt] =
    useState("");

  const [saving, setSaving] =
    useState(false);

  const [error, setError] =
    useState<string | null>(
      null
    );


  if (!open) {
    return null;
  }


  async function submit(
    event: FormEvent
  ) {
    event.preventDefault();

    if (!title.trim()) {
      return;
    }

    try {
      setSaving(true);
      setError(null);

      const task =
        await createTask({
          title:
            title.trim(),

          description:
            description.trim()
              || null,

          task_type:
            taskType,

          priority,

          due_at:
            dueAt
              ? new Date(
                  dueAt
                ).toISOString()
              : null,

          source: "manual",
        });

      onCreated(task);

      setTitle("");
      setDescription("");
      setTaskType(
        "general"
      );
      setPriority(
        "medium"
      );
      setDueAt("");

      onClose();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to create task."
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
              <ListTodo
                size={18}
              />
            </div>

            <div>
              <h2>
                New Task
              </h2>

              <p>
                Create a follow-up,
                action or CRM task.
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
              placeholder="Follow up with Acme"
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
              placeholder="Add task details..."
            />
          </label>

          <div className="formGrid">
            <label>
              Task type

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
          </div>

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
                ? "Creating..."
                : "Create Task"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
