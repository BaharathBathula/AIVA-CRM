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
  Mail,
  Plus,
  Search,
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


export default function ActivitiesPage() {
  const [
    activities,
    setActivities,
  ] = useState<Activity[]>([]);

  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    error,
    setError,
  ] = useState<string | null>(
    null
  );

  const [
    modalOpen,
    setModalOpen,
  ] = useState(false);

  const [
    search,
    setSearch,
  ] = useState("");

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


  useEffect(() => {
    async function loadActivities() {
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

    loadActivities();
  }, []);


  const filteredActivities =
    useMemo(() => {
      const normalized =
        search
          .trim()
          .toLowerCase();

      return activities.filter(
        (activity) => {
          const matchesType =
            typeFilter === "all"
            ||
            activity.activity_type
              === typeFilter;

          const matchesDirection =
            directionFilter === "all"
            ||
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

          const matchesSearch =
            !normalized
            ||
            searchable.includes(
              normalized
            );

          return (
            matchesType
            &&
            matchesDirection
            &&
            matchesSearch
          );
        }
      );
    }, [
      activities,
      search,
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

        const today =
          new Date();

        return (
          occurred.getFullYear()
            === today.getFullYear()
          &&
          occurred.getMonth()
            === today.getMonth()
          &&
          occurred.getDate()
            === today.getDate()
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


  function handleTypeFilter(
    value: string
  ) {
    setTypeFilter(
      value as TypeFilter
    );
  }


  function handleDirectionFilter(
    value: string
  ) {
    setDirectionFilter(
      value as DirectionFilter
    );
  }


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
              className="createButton"
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

          <section className="activitiesPanel">
            <div className="activitiesToolbar">
              <div className="accountSearch">
                <Search size={17} />

                <input
                  value={search}
                  onChange={(event) =>
                    setSearch(
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
                    handleTypeFilter(
                      event.target.value
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
                    handleDirectionFilter(
                      event.target.value
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

            {error && (
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
                <div className="miniEmptyState">
                  <ActivityIcon
                    size={22}
                  />

                  <strong>
                    No activities found
                  </strong>

                  <span>
                    Try changing the
                    search or filters.
                  </span>
                </div>
              )}

            {!loading &&
              !error &&
              filteredActivities.length
                > 0 && (
                <div className="timeline">
                  {filteredActivities.map(
                    (activity) => (
                      <button
                        type="button"
                        className="timelineItem activityTimelineButton"
                        key={activity.id}
                        onClick={() =>
                          setSelectedActivity(
                            activity
                          )
                        }
                      >
                        <div className="timelineMarker">
                          <ActivityIcon
                            size={15}
                          />
                        </div>

                        <div className="timelineContent">
                          <div className="timelineTop">
                            <div>
                              <strong>
                                {
                                  activity.subject
                                }
                              </strong>

                              <span className="activityTypeLabel">
                                {
                                  activity.activity_type
                                }

                                {activity.direction
                                  ? ` · ${activity.direction}`
                                  : ""}
                              </span>
                            </div>

                            <time>
                              {new Date(
                                activity.occurred_at
                              ).toLocaleString()}
                            </time>
                          </div>

                          {activity.body && (
                            <p>
                              {
                                activity.body
                              }
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
              <ActivityIcon
                size={18}
              />
            </div>

            <div className="activityDrawerBadges">
              <span className="activityTypePill">
                {selectedActivity.activity_type
                  .replaceAll("_", " ")}
              </span>

              {selectedActivity.direction && (
                <span className="activityDirectionPill">
                  {
                    selectedActivity.direction
                  }
                </span>
              )}
            </div>

            <div className="activityDetailBlock">
              <span>
                Details
              </span>

              <p>
                {selectedActivity.body
                  || "No additional details provided."}
              </p>
            </div>

            <div className="activityDetailGrid">
              <div>
                <span>
                  Occurred
                </span>

                <strong>
                  {new Date(
                    selectedActivity.occurred_at
                  ).toLocaleString()}
                </strong>
              </div>

              <div>
                <span>
                  Account
                </span>

                <strong>
                  {selectedActivity.account_id
                    || "—"}
                </strong>
              </div>

              <div>
                <span>
                  Contact
                </span>

                <strong>
                  {selectedActivity.contact_id
                    || "—"}
                </strong>
              </div>

              <div>
                <span>
                  Lead
                </span>

                <strong>
                  {selectedActivity.lead_id
                    || "—"}
                </strong>
              </div>

              <div>
                <span>
                  Opportunity
                </span>

                <strong>
                  {selectedActivity.opportunity_id
                    || "—"}
                </strong>
              </div>

              <div>
                <span>
                  External ID
                </span>

                <strong>
                  {selectedActivity.external_id
                    || "—"}
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