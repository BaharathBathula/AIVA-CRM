"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  AlertTriangle,
  Bot,
  CalendarDays,
  CheckCircle2,
  Circle,
  Clock3,
  Filter,
  ListTodo,
  Pencil,
  Plus,
  RotateCcw,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";

import {
  Sidebar,
} from "@/components/sidebar";

import {
  CreateTaskModal,
} from "@/components/tasks/create-task-modal";

import {
  EditTaskModal,
} from "@/components/tasks/edit-task-modal";

import {
  Topbar,
} from "@/components/topbar";

import {
  completeTask,
  deleteTask,
  getTasks,
  reopenTask,
} from "@/lib/tasks";

import type {
  Task,
  TaskPriority,
  TaskStatus,
  TaskType,
} from "@/types/task";


type StatusFilter =
  | "all"
  | TaskStatus;

type PriorityFilter =
  | "all"
  | TaskPriority;

type TypeFilter =
  | "all"
  | TaskType;


const statusFilters:
  StatusFilter[] = [
    "all",
    "open",
    "in_progress",
    "completed",
    "cancelled",
  ];


function label(
  value: string
) {
  return value
    .replaceAll("_", " ")
    .replace(
      /\b\w/g,
      (letter) =>
        letter.toUpperCase()
    );
}


function isTaskOverdue(
  task: Task
) {
  if (!task.due_at) {
    return false;
  }

  if (
    task.status === "completed" ||
    task.status === "cancelled"
  ) {
    return false;
  }

  return (
    new Date(
      task.due_at
    ).getTime()
    < Date.now()
  );
}


function formatDueDate(
  value: string | null
) {
  if (!value) {
    return "No due date";
  }

  return new Intl.DateTimeFormat(
    "en-US",
    {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }
  ).format(
    new Date(value)
  );
}


export default function TasksPage() {
  const [tasks, setTasks] =
    useState<Task[]>([]);

  const [loading, setLoading] =
    useState(true);

  const [error, setError] =
    useState<string | null>(
      null
    );

  const [query, setQuery] =
    useState("");

  const [
    statusFilter,
    setStatusFilter,
  ] = useState<StatusFilter>(
    "all"
  );

  const [
    priorityFilter,
    setPriorityFilter,
  ] = useState<PriorityFilter>(
    "all"
  );

  const [
    typeFilter,
    setTypeFilter,
  ] = useState<TypeFilter>(
    "all"
  );

  const [
    overdueOnly,
    setOverdueOnly,
  ] = useState(false);

  const [
    updatingTaskId,
    setUpdatingTaskId,
  ] = useState<string | null>(
    null
  );

  const [
    deletingTaskId,
    setDeletingTaskId,
  ] = useState<string | null>(
    null
  );

  const [
    createOpen,
    setCreateOpen,
  ] = useState(false);

  const [
    editOpen,
    setEditOpen,
  ] = useState(false);

  const [
    selectedTask,
    setSelectedTask,
  ] = useState<Task | null>(
    null
  );


  useEffect(() => {
    async function loadTasks() {
      try {
        setLoading(true);
        setError(null);

        const data =
          await getTasks();

        setTasks(data);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Unable to load tasks."
        );
      } finally {
        setLoading(false);
      }
    }

    loadTasks();
  }, []);


  const filteredTasks =
    useMemo(() => {
      const normalized =
        query
          .trim()
          .toLowerCase();

      return tasks.filter(
        (task) => {
          const matchesStatus =
            statusFilter === "all" ||
            task.status
              === statusFilter;

          const matchesPriority =
            priorityFilter === "all" ||
            task.priority
              === priorityFilter;

          const matchesType =
            typeFilter === "all" ||
            task.task_type
              === typeFilter;

          const matchesOverdue =
            !overdueOnly ||
            isTaskOverdue(
              task
            );

          const searchable = [
            task.title,
            task.description,
            task.task_type,
            task.priority,
            task.source,
          ]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();

          const matchesQuery =
            !normalized ||
            searchable.includes(
              normalized
            );

          return (
            matchesStatus &&
            matchesPriority &&
            matchesType &&
            matchesOverdue &&
            matchesQuery
          );
        }
      );
    }, [
      tasks,
      query,
      statusFilter,
      priorityFilter,
      typeFilter,
      overdueOnly,
    ]);


  const openCount =
    tasks.filter(
      (task) =>
        task.status === "open" ||
        task.status
          === "in_progress"
    ).length;


  const completedCount =
    tasks.filter(
      (task) =>
        task.status
        === "completed"
    ).length;


  const overdueCount =
    tasks.filter(
      isTaskOverdue
    ).length;


  function replaceTask(
    updated: Task
  ) {
    setTasks(
      (current) =>
        current.map(
          (task) =>
            task.id
            === updated.id
              ? updated
              : task
        )
    );

    setSelectedTask(
      (current) =>
        current?.id
        === updated.id
          ? updated
          : current
    );
  }


  function addCreatedTask(
    task: Task
  ) {
    setTasks(
      (current) => [
        task,
        ...current,
      ]
    );
  }


  function openDetails(
    task: Task
  ) {
    setSelectedTask(
      task
    );
  }


  function openEdit(
    task: Task
  ) {
    setSelectedTask(
      task
    );

    setEditOpen(
      true
    );
  }


  async function toggleComplete(
    task: Task
  ) {
    try {
      setUpdatingTaskId(
        task.id
      );

      setError(null);

      const updated =
        task.status
        === "completed"
          ? await reopenTask(
              task.id
            )
          : await completeTask(
              task.id
            );

      replaceTask(
        updated
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to update task."
      );
    } finally {
      setUpdatingTaskId(
        null
      );
    }
  }


  async function removeTask(
    task: Task
  ) {
    const confirmed =
      window.confirm(
        `Delete "${task.title}"?`
      );

    if (!confirmed) {
      return;
    }

    try {
      setDeletingTaskId(
        task.id
      );

      setError(null);

      await deleteTask(
        task.id
      );

      setTasks(
        (current) =>
          current.filter(
            (item) =>
              item.id
              !== task.id
          )
      );

      if (
        selectedTask?.id
        === task.id
      ) {
        setSelectedTask(
          null
        );
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to delete task."
      );
    } finally {
      setDeletingTaskId(
        null
      );
    }
  }


  return (
    <div className="appShell">
      <Sidebar active="Tasks" />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <div className="pageHeading">
            <div>
              <p className="eyebrow">
                Engagement
              </p>

              <h1>
                Tasks
              </h1>

              <p>
                Manage follow-ups,
                actions and AI-generated
                work across your CRM.
              </p>
            </div>

            <button
              type="button"
              className="createButton taskCreateButton"
              onClick={() =>
                setCreateOpen(
                  true
                )
              }
            >
              <Plus size={16} />
              New Task
            </button>
          </div>

          <div className="statsGrid">
            <div className="statCard">
              <div className="statLabel">
                Total Tasks
              </div>

              <div className="statValue">
                {tasks.length}
              </div>

              <div className="statMeta">
                <ListTodo size={14} />
                All CRM tasks
              </div>
            </div>

            <div className="statCard">
              <div className="statLabel">
                Open
              </div>

              <div className="statValue">
                {openCount}
              </div>

              <div className="statMeta">
                <Clock3 size={14} />
                Needs attention
              </div>
            </div>

            <div className="statCard">
              <div className="statLabel">
                Overdue
              </div>

              <div className="statValue">
                {overdueCount}
              </div>

              <div className="statMeta negative">
                <AlertTriangle
                  size={14}
                />
                Past due date
              </div>
            </div>

            <div className="statCard">
              <div className="statLabel">
                Completed
              </div>

              <div className="statValue">
                {completedCount}
              </div>

              <div className="statMeta positive">
                <CheckCircle2
                  size={14}
                />
                Finished tasks
              </div>
            </div>
          </div>

          <div className="aivaBrief">
            <div className="briefIcon">
              <Sparkles
                size={18}
              />
            </div>

            <div className="briefContent">
              <span>
                AIVA TASK INTELLIGENCE
              </span>

              <strong>
                AI-ready task orchestration
                is enabled.
              </strong>

              <p>
                Review manual and
                AI-generated work,
                overdue follow-ups and
                priority actions.
              </p>
            </div>
          </div>

          <section className="accountsPanel">
            <div className="taskToolbar">
              <div className="accountSearch">
                <Search
                  size={17}
                />

                <input
                  value={query}
                  onChange={(event) =>
                    setQuery(
                      event.target.value
                    )
                  }
                  placeholder="Search tasks..."
                />
              </div>

              <div className="taskSelectFilters">
                <Filter size={15} />

                <select
                  value={
                    priorityFilter
                  }
                  onChange={(event) =>
                    setPriorityFilter(
                      event.target.value
                        as PriorityFilter
                    )
                  }
                >
                  <option value="all">
                    All priorities
                  </option>

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

                <select
                  value={typeFilter}
                  onChange={(event) =>
                    setTypeFilter(
                      event.target.value
                        as TypeFilter
                    )
                  }
                >
                  <option value="all">
                    All types
                  </option>

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

                <button
                  type="button"
                  className={
                    overdueOnly
                      ? "filterChip active"
                      : "filterChip"
                  }
                  onClick={() =>
                    setOverdueOnly(
                      (current) =>
                        !current
                    )
                  }
                >
                  Overdue
                </button>
              </div>
            </div>

            <div className="taskStatusFilters">
              {statusFilters.map(
                (item) => (
                  <button
                    key={item}
                    type="button"
                    className={
                      statusFilter
                      === item
                        ? "filterChip active"
                        : "filterChip"
                    }
                    onClick={() =>
                      setStatusFilter(
                        item
                      )
                    }
                  >
                    {label(item)}
                  </button>
                )
              )}
            </div>

            {loading && (
              <div className="tableState">
                Loading tasks...
              </div>
            )}

            {error &&
              !loading && (
                <div className="tableState errorState">
                  <strong>
                    Tasks unavailable
                  </strong>

                  <span>
                    {error}
                  </span>
                </div>
              )}

            {!loading &&
              !error &&
              filteredTasks.length
                === 0 && (
                <div className="emptyState">
                  <div className="emptyStateIcon">
                    <ListTodo
                      size={23}
                    />
                  </div>

                  <h3>
                    No tasks found
                  </h3>

                  <p>
                    Try changing your
                    filters or create
                    a new task.
                  </p>

                  <button
                    type="button"
                    className="createButton taskCreateButton"
                    onClick={() =>
                      setCreateOpen(
                        true
                      )
                    }
                  >
                    <Plus size={15} />
                    New Task
                  </button>
                </div>
              )}

            {!loading &&
              !error &&
              filteredTasks.length
                > 0 && (
                <div className="accountTableWrapper">
                  <table className="accountTable">
                    <thead>
                      <tr>
                        <th>
                          Task
                        </th>

                        <th>
                          Status
                        </th>

                        <th>
                          Priority
                        </th>

                        <th>
                          Type
                        </th>

                        <th>
                          Due
                        </th>

                        <th>
                          Source
                        </th>

                        <th>
                          Actions
                        </th>
                      </tr>
                    </thead>

                    <tbody>
                      {filteredTasks.map(
                        (task) => {
                          const overdue =
                            isTaskOverdue(
                              task
                            );

                          return (
                            <tr
                              key={task.id}
                            >
                              <td>
                                <button
                                  type="button"
                                  className="taskIdentityButton"
                                  onClick={() =>
                                    openDetails(
                                      task
                                    )
                                  }
                                >
                                  <div className="accountLogo">
                                    {task.status
                                      ===
                                    "completed" ? (
                                      <CheckCircle2
                                        size={16}
                                      />
                                    ) : (
                                      <Circle
                                        size={16}
                                      />
                                    )}
                                  </div>

                                  <div>
                                    <strong>
                                      {task.title}
                                    </strong>

                                    <span>
                                      {task.description
                                        ||
                                        "No description"}
                                    </span>
                                  </div>
                                </button>
                              </td>

                              <td>
                                <span
                                  className={
                                    `taskStatus taskStatus-${task.status}`
                                  }
                                >
                                  {label(
                                    task.status
                                  )}
                                </span>
                              </td>

                              <td>
                                <span
                                  className={
                                    `taskPriority taskPriority-${task.priority}`
                                  }
                                >
                                  {label(
                                    task.priority
                                  )}
                                </span>
                              </td>

                              <td>
                                {label(
                                  task.task_type
                                )}
                              </td>

                              <td>
                                <span
                                  className={
                                    overdue
                                      ? "negative taskDue"
                                      : "taskDue"
                                  }
                                >
                                  {overdue && (
                                    <AlertTriangle
                                      size={12}
                                    />
                                  )}

                                  {formatDueDate(
                                    task.due_at
                                  )}
                                </span>
                              </td>

                              <td>
                                {task.is_ai_generated
                                  ? (
                                    <span className="taskAiSource">
                                      <Bot size={12} />
                                      AIVA AI
                                    </span>
                                  )
                                  : label(
                                      task.source
                                    )}
                              </td>

                              <td>
                                <div className="taskActions">
                                  <button
                                    type="button"
                                    className="taskIconButton"
                                    title="Edit task"
                                    onClick={() =>
                                      openEdit(
                                        task
                                      )
                                    }
                                  >
                                    <Pencil
                                      size={14}
                                    />
                                  </button>

                                  <button
                                    type="button"
                                    className="secondaryButton"
                                    disabled={
                                      updatingTaskId
                                      === task.id
                                    }
                                    onClick={() =>
                                      toggleComplete(
                                        task
                                      )
                                    }
                                  >
                                    {task.status
                                      ===
                                    "completed" ? (
                                      <>
                                        <RotateCcw
                                          size={13}
                                        />
                                        Reopen
                                      </>
                                    ) : (
                                      <>
                                        <CheckCircle2
                                          size={13}
                                        />
                                        Complete
                                      </>
                                    )}
                                  </button>

                                  <button
                                    type="button"
                                    className="taskIconButton danger"
                                    title="Delete task"
                                    disabled={
                                      deletingTaskId
                                      === task.id
                                    }
                                    onClick={() =>
                                      removeTask(
                                        task
                                      )
                                    }
                                  >
                                    <Trash2
                                      size={14}
                                    />
                                  </button>
                                </div>
                              </td>
                            </tr>
                          );
                        }
                      )}
                    </tbody>
                  </table>
                </div>
              )}
          </section>
        </div>
      </main>

      {selectedTask && (
        <div className="taskDrawer">
          <div className="taskDrawerHeader">
            <div>
              <span>
                Task Details
              </span>

              <h2>
                {selectedTask.title}
              </h2>
            </div>

            <button
              type="button"
              className="modalClose"
              onClick={() =>
                setSelectedTask(
                  null
                )
              }
            >
              <X size={18} />
            </button>
          </div>

          <div className="taskDrawerBody">
            <div className="taskDrawerBadges">
              <span
                className={
                  `taskStatus taskStatus-${selectedTask.status}`
                }
              >
                {label(
                  selectedTask.status
                )}
              </span>

              <span
                className={
                  `taskPriority taskPriority-${selectedTask.priority}`
                }
              >
                {label(
                  selectedTask.priority
                )}
              </span>
            </div>

            <div className="taskDetailBlock">
              <span>
                Description
              </span>

              <p>
                {selectedTask.description
                  ||
                  "No description provided."}
              </p>
            </div>

            <div className="taskDetailGrid">
              <div>
                <span>
                  Type
                </span>

                <strong>
                  {label(
                    selectedTask.task_type
                  )}
                </strong>
              </div>

              <div>
                <span>
                  Due
                </span>

                <strong>
                  {formatDueDate(
                    selectedTask.due_at
                  )}
                </strong>
              </div>

              <div>
                <span>
                  Source
                </span>

                <strong>
                  {selectedTask
                    .is_ai_generated
                    ? "AIVA AI"
                    : label(
                        selectedTask.source
                      )}
                </strong>
              </div>

              <div>
                <span>
                  Recurring
                </span>

                <strong>
                  {selectedTask
                    .is_recurring
                    ? "Yes"
                    : "No"}
                </strong>
              </div>
            </div>

            {selectedTask.due_at && (
              <div className="taskDetailNotice">
                <CalendarDays
                  size={15}
                />

                {isTaskOverdue(
                  selectedTask
                )
                  ? "This task is overdue."
                  : "This task has a scheduled due date."}
              </div>
            )}

            <div className="taskDrawerActions">
              <button
                type="button"
                className="createButton taskCreateButton"
                onClick={() =>
                  openEdit(
                    selectedTask
                  )
                }
              >
                <Pencil size={14} />
                Edit Task
              </button>

              <button
                type="button"
                className="secondaryButton taskDeleteButton"
                onClick={() =>
                  removeTask(
                    selectedTask
                  )
                }
              >
                <Trash2 size={14} />
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      <CreateTaskModal
        open={createOpen}
        onClose={() =>
          setCreateOpen(false)
        }
        onCreated={
          addCreatedTask
        }
      />

      <EditTaskModal
        open={editOpen}
        task={selectedTask}
        onClose={() =>
          setEditOpen(false)
        }
        onUpdated={
          replaceTask
        }
      />
    </div>
  );
}
