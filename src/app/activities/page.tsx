"use client";

import {
  useEffect,
  useState,
} from "react";

import {
  Activity as ActivityIcon,
  Bot,
  CalendarDays,
  Mail,
} from "lucide-react";

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
} from "@/types/activity";


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
                {
                  communicationCount
                }
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
            !error && (
              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Activity Feed
                    </h2>

                    <p>
                      Unified CRM engagement
                      history.
                    </p>
                  </div>
                </div>

                {activities.length === 0 ? (
                  <div className="miniEmptyState">
                    <ActivityIcon
                      size={22}
                    />

                    <strong>
                      No activities yet
                    </strong>

                    <span>
                      Customer interactions
                      will appear here.
                    </span>
                  </div>
                ) : (
                  <div className="timeline">
                    {activities.map(
                      (activity) => (
                        <div
                          className="timelineItem"
                          key={
                            activity.id
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
                        </div>
                      )
                    )}
                  </div>
                )}
              </section>
            )}
        </div>
      </main>
    </div>
  );
}
