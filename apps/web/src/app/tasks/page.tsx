"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Clock3,
  Filter,
  ListTodo,
  RotateCcw,
  Search,
  Sparkles,
} from "lucide-react";

import {
  Sidebar,
} from "@/components/sidebar";

import {
  Topbar,
} from "@/components/topbar";

import {
  completeTask,
  getTasks,
  reopenTask,
} from "@/lib/tasks";

import type {
  Task,
  TaskStatus,
} from "@/types/task";


type StatusFilter =
  | "all"
  | TaskStatus;


const statusFilters: StatusFilter[] = [
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
    new Date(task.due_at).getTime()
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
    useState<string | null>(null);

  const [query, setQuery] =
    useState("");

  const [
    statusFilter,
    setStatusFilter,
  ] = useState<StatusFilter>(
    "all"
  );

  const [
    updatingTaskId,
    setUpdatingTaskId,
  ] = useState<string | null>(
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
            matchesQuery
          );
        }
      );
    }, [
      tasks,
      query,
      statusFilter,
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
        task.status === "completed"
    ).length;


  const overdueCount =
    tasks.filter(
      isTaskOverdue
    ).length;


  async function toggleComplete(
    task: Task
  ) {
    try {
      setUpdatingTaskId(
        task.id
      );

      const updated =
        task.status === "completed"
          ? await reopenTask(
              task.id
            )
          : await completeTask(
              task.id
            );

      setTasks(
        (current) =>
          current.map(
            (item) =>
              item.id
                === updated.id
                ? updated
                : item
          )
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
                <ListTodo
                  size={14}
                />
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
                <Clock3
                  size={14}
                />
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
                Tasks can be associated
                with leads, contacts,
                accounts and opportunities,
                including AI-generated
                follow-ups.
              </p>
            </div>
          </div>

          <section className="accountsPanel">
            <div className="accountToolbar">
              <div className="accountSearch">
                <Search
                  size={17}
                />

                <input
                  value={query}
                  onChange={(
                    event
                  ) =>
                    setQuery(
                      event
                        .target
                        .value
                    )
                  }
                  placeholder="Search tasks..."
                />
              </div>

              <div className="accountFilters">
                <Filter
                  size={15}
                />

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
                    Tasks created by
                    your team or AIVA
                    will appear here.
                  </p>
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
                          Action
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
                                <div className="accountIdentity">
                                  <div className="accountLogo">
                                    {task
                                      .status
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
                                      {
                                        task.title
                                      }
                                    </strong>

                                    <span>
                                      {
                                        task.description
                                        ||
                                        "No description"
                                      }
                                    </span>
                                  </div>
                                </div>
                              </td>

                              <td>
                                <span className="stageBadge">
                                  {label(
                                    task.status
                                  )}
                                </span>
                              </td>

                              <td>
                                <strong>
                                  {label(
                                    task.priority
                                  )}
                                </strong>
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
                                      ? "negative"
                                      : ""
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
                                {task
                                  .is_ai_generated ? (
                                  <span>
                                    AIVA AI
                                  </span>
                                ) : (
                                  label(
                                    task.source
                                  )
                                )}
                              </td>

                              <td>
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
    </div>
  );
}
