"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  Activity as ActivityIcon,
  Bot,
  CalendarDays,
  FileText,
  Mail,
  MessageSquare,
  Phone,
  Plus,
  Search,
  Smartphone,
  Sparkles,
  X,
} from "lucide-react";

import {
  LogActivityModal,
} from "@/components/activities/log-activity-modal";

import {
  Sidebar,
} from "@/components/sidebar";

import {
  Topbar,
} from "@/components/topbar";

import {
  getActivities,
} from "@/lib/activities";

import type {
  Activity,
  ActivityDirection,
  ActivityType,
} from "@/types/activity";


type TypeFilter =
  | "all"
  | ActivityType;

type DirectionFilter =
  | "all"
  | ActivityDirection;


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


function formatDate(
  value: string
) {
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


function activityIcon(
  type: ActivityType
) {
  if (type === "email") {
    return <Mail size={16} />;
  }

  if (type === "call") {
    return <Phone size={16} />;
  }

  if (type === "meeting") {
    return <CalendarDays size={16} />;
  }

  if (type === "note") {
    return <MessageSquare size={16} />;
  }

  if (type === "sms") {
    return <Smartphone size={16} />;
  }

  if (type === "document") {
    return <FileText size={16} />;
  }

  if (type === "ai_action") {
    return <Bot size={16} />;
  }

  return <ActivityIcon size={16} />;
}


function relationshipLabel(
  activity: Activity
) {
  if (activity.opportunity_id) {
    return "Opportunity";
  }

  if (activity.lead_id) {
    return "Lead";
  }

  if (activity.contact_id) {
    return "Contact";
  }

  if (activity.account_id) {
    return "Account";
  }

  return "General";
}


export default function ActivitiesPage() {
  const [
    activities,
    setActivities,
  ] = useState<Activity[]>([]);

  const [loading, setLoading] =
    useState(true);

  const [error, setError] =
    useState<string | null>(
      null
    );

  const [query, setQuery] =
    useState("");

  const [
    typeFilter,
    setTypeFilter,
  ] = useState<TypeFilter>(
    "all"
  );

  const [
    directionFilter,
    setDirectionFilter,
  ] = useState<DirectionFilter>(
    "all"
  );

  const [
    selectedActivity,
    setSelectedActivity,
  ] = useState<Activity | null>(
    null
  );

  const [
    modalOpen,
    setModalOpen,
  ] = useState(false);


  useEffect(() => {
    async function load() {
      try {
        setLoading(true);
        setError(null);

        const result =
          await getActivities({
            limit: 100,
          });

        setActivities(result);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Unable to load activities."
        );
      } finally {
        setLoading(false);
      }
    }

    load();
  }, []);


  const filteredActivities =
    useMemo(() => {
      const normalized =
        query
          .trim()
          .toLowerCase();

      return activities.filter(
        (activity) => {
          const matchesType =
            typeFilter === "all" ||
            activity.activity_type
              === typeFilter;

          const matchesDirection =
            directionFilter === "all" ||
            activity.direction
              === directionFilter;

          const searchable = [
            activity.subject,
            activity.body,
            activity.activity_type,
            activity.direction,
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
            matchesType &&
            matchesDirection &&
            matchesQuery
          );
        }
      );
    }, [
      activities,
      query,
      typeFilter,
      directionFilter,
    ]);


  const todayCount =
    activities.filter(
      (activity) => {
        const occurred =
          new Date(
            activity.occurred_at
          );

        const now =
          new Date();

        return (
          occurred.getFullYear()
            === now.getFullYear() &&
          occurred.getMonth()
            === now.getMonth() &&
          occurred.getDate()
            === now.getDate()
        );
      }
    ).length;


  const communicationCount =
    activities.filter(
      (activity) =>
        [
          "email",
          "call",
          "meeting",
          "sms",
        ].includes(
          activity.activity_type
        )
    ).length;


  const aiCount =
    activities.filter(
      (activity) =>
        activity.activity_type
        === "ai_action"
    ).length;


  return (
    <div className="appShell">
      <Sidebar active="Activities" />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <div className="pageHeading">
            <div>
              <p className="eyebrow">
                Engagement
              </p>

              <h1>
                Activities
              </h1>

              <p>
                Unified customer interaction
                timeline across your CRM.
              </p>
            </div>

            <button
              type="button"
              className="createButton activityCreateButton"
              onClick={() =>
                setModalOpen(true)
              }
            >
              <Plus size={16} />
              Log Activity
            </button>
          </div>

          <div className="statsGrid">
            <div className="statCard">
              <div className="statLabel">
                Total Activities
              </div>

              <div className="statValue">
                {activities.length}
              </div>

              <div className="statMeta">
                <ActivityIcon
                  size={14}
                />
                CRM interactions
              </div>
            </div>

            <div className="statCard">
              <div className="statLabel">
                Today
              </div>

              <div className="statValue">
                {todayCount}
              </div>

              <div className="statMeta">
                <CalendarDays
                  size={14}
                />
                Recorded today
              </div>
            </div>

            <div className="statCard">
              <div className="statLabel">
                Communications
              </div>

              <div className="statValue">
                {communicationCount}
              </div>

              <div className="statMeta">
                <Mail size={14} />
                Email, call & meeting
              </div>
            </div>

            <div className="statCard">
              <div className="statLabel">
                AI Actions
              </div>

              <div className="statValue">
                {aiCount}
              </div>

              <div className="statMeta">
                <Bot size={14} />
                AIVA-generated actions
              </div>
            </div>
          </div>

          <div className="aivaBrief">
            <div className="briefIcon">
              <Sparkles size={18} />
            </div>

            <div className="briefContent">
              <span>
                AIVA ENGAGEMENT INTELLIGENCE
              </span>

              <strong>
                One timeline for every
                customer interaction.
              </strong>

              <p>
                Calls, emails, meetings,
                notes, system events and
                AI actions can be analyzed
                as a single relationship
                history.
              </p>
            </div>
          </div>

          <section className="activitiesPanel">
            <div className="activitiesToolbar">
              <div className="accountSearch">
                <Search size={17} />

                <input
                  value={query}
                  onChange={(event) =>
                    setQuery(
                      event.target.value
                    )
                  }
                  placeholder="Search activities..."
                />
              </div>

              <div className="activitySelectFilters">
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
                    All activity types
                  </option>

                  <option value="email">
                    Email
                  </option>

                  <option value="call">
                    Call
                  </option>

                  <option value="meeting">
                    Meeting
                  </option>

                  <option value="note">
                    Note
                  </option>

                  <option value="sms">
                    SMS
                  </option>

                  <option value="document">
                    Document
                  </option>

                  <option value="task_completed">
                    Task Completed
                  </option>

                  <option value="system">
                    System
                  </option>

                  <option value="ai_action">
                    AI Action
                  </option>
                </select>

                <select
                  value={
                    directionFilter
                  }
                  onChange={(event) =>
                    setDirectionFilter(
                      event.target.value
                        as DirectionFilter
                    )
                  }
                >
                  <option value="all">
                    All directions
                  </option>

                  <option value="inbound">
                    Inbound
                  </option>

                  <option value="outbound">
                    Outbound
                  </option>
                </select>
              </div>
            </div>

            {loading && (
              <div className="tableState">
                Loading activities...
              </div>
            )}

            {error &&
              !loading && (
                <div className="tableState errorState">
                  <strong>
                    Activities unavailable
                  </strong>

                  <span>
                    {error}
                  </span>
                </div>
              )}

            {!loading &&
              !error &&
              filteredActivities.length
                === 0 && (
                <div className="emptyState">
                  <div className="emptyStateIcon">
                    <ActivityIcon
                      size={23}
                    />
                  </div>

                  <h3>
                    No activities found
                  </h3>

                  <p>
                    Log an interaction or
                    change your filters.
                  </p>

                  <button
                    type="button"
                    className="createButton activityCreateButton"
                    onClick={() =>
                      setModalOpen(true)
                    }
                  >
                    <Plus size={15} />
                    Log Activity
                  </button>
                </div>
              )}

            {!loading &&
              !error &&
              filteredActivities.length
                > 0 && (
                <div className="activityFeed">
                  {filteredActivities.map(
                    (activity) => (
                      <button
                        type="button"
                        className="activityFeedItem"
                        key={activity.id}
                        onClick={() =>
                          setSelectedActivity(
                            activity
                          )
                        }
                      >
                        <div
                          className={
                            `activityFeedIcon activity-${activity.activity_type}`
                          }
                        >
                          {activityIcon(
                            activity.activity_type
                          )}
                        </div>

                        <div className="activityFeedBody">
                          <div className="activityFeedTop">
                            <div>
                              <strong>
                                {
                                  activity.subject
                                }
                              </strong>

                              <div className="activityMetaRow">
                                <span className="activityTypePill">
                                  {label(
                                    activity.activity_type
                                  )}
                                </span>

                                {activity.direction && (
                                  <span>
                                    {label(
                                      activity.direction
                                    )}
                                  </span>
                                )}

                                <span>
                                  {relationshipLabel(
                                    activity
                                  )}
                                </span>
                              </div>
                            </div>

                            <time>
                              {formatDate(
                                activity.occurred_at
                              )}
                            </time>
                          </div>

                          {activity.body && (
                            <p>
                              {activity.body}
                            </p>
                          )}
                        </div>
                      </button>
                    )
                  )}
                </div>
              )}
          </section>
        </div>
      </main>

      {selectedActivity && (
        <aside className="activityDrawer">
          <div className="activityDrawerHeader">
            <div>
              <span>
                Activity Details
              </span>

              <h2>
                {selectedActivity.subject}
              </h2>
            </div>

            <button
              type="button"
              className="modalClose"
              onClick={() =>
                setSelectedActivity(
                  null
                )
              }
            >
              <X size={18} />
            </button>
          </div>

          <div className="activityDrawerBody">
            <div className="activityDrawerIcon">
              {activityIcon(
                selectedActivity
                  .activity_type
              )}
            </div>

            <div className="activityDrawerBadges">
              <span className="activityTypePill">
                {label(
                  selectedActivity
                    .activity_type
                )}
              </span>

              {selectedActivity.direction && (
                <span className="activityDirectionPill">
                  {label(
                    selectedActivity
                      .direction
                  )}
                </span>
              )}
            </div>

            <div className="activityDetailBlock">
              <span>
                Details
              </span>

              <p>
                {selectedActivity.body
                  || "No details provided."}
              </p>
            </div>

            <div className="activityDetailGrid">
              <div>
                <span>
                  Occurred
                </span>

                <strong>
                  {formatDate(
                    selectedActivity
                      .occurred_at
                  )}
                </strong>
              </div>

              <div>
                <span>
                  Relationship
                </span>

                <strong>
                  {relationshipLabel(
                    selectedActivity
                  )}
                </strong>
              </div>

              <div>
                <span>
                  External ID
                </span>

                <strong>
                  {selectedActivity
                    .external_id
                    || "—"}
                </strong>
              </div>

              <div>
                <span>
                  Source
                </span>

                <strong>
                  {selectedActivity
                    .activity_type
                    === "ai_action"
                    ? "AIVA AI"
                    : "CRM"}
                </strong>
              </div>
            </div>
          </div>
        </aside>
      )}

      <LogActivityModal
        open={modalOpen}
        onClose={() =>
          setModalOpen(false)
        }
        onCreated={(activity) =>
          setActivities(
            (current) => [
              activity,
              ...current,
            ]
          )
        }
      />
    </div>
  );
}
