cat > 'src/app/opportunities/[opportunityId]/page.tsx' <<'TSX'
"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import type {
  FormEvent,
} from "react";

import Link from "next/link";

import {
  ArrowLeft,
  Bot,
  Building2,
  CalendarDays,
  CheckCircle2,
  CircleDollarSign,
  Clock3,
  Contact as ContactIcon,
  FileText,
  Handshake,
  Loader2,
  Mail,
  MessageSquare,
  Phone,
  Plus,
  RefreshCcw,
  Send,
  Sparkles,
  Target,
} from "lucide-react";

import {
  useParams,
} from "next/navigation";

import {
  Sidebar,
} from "@/components/sidebar";

import {
  Topbar,
} from "@/components/topbar";

import {
  createActivity,
  getOpportunityActivities,
} from "@/lib/activities";

import {
  getAccount,
} from "@/lib/accounts";

import {
  getContact,
} from "@/lib/contacts";

import {
  getOpportunity,
  moveOpportunity,
} from "@/lib/opportunities";

import {
  getPipeline,
  getPipelineStages,
} from "@/lib/pipelines";

import type {
  Account,
} from "@/types/account";

import type {
  Activity,
} from "@/types/activity";

import type {
  Contact,
} from "@/types/contact";

import type {
  Opportunity,
} from "@/types/opportunity";

import type {
  Pipeline,
  PipelineStage,
} from "@/types/pipeline";


type TimelineFilter =
  | "all"
  | "email"
  | "meeting"
  | "note"
  | "other";


const TIMELINE_FILTERS: {
  value: TimelineFilter;
  label: string;
}[] = [
  {
    value: "all",
    label: "All",
  },
  {
    value: "email",
    label: "Emails",
  },
  {
    value: "meeting",
    label: "Meetings",
  },
  {
    value: "note",
    label: "Notes",
  },
  {
    value: "other",
    label: "Other",
  },
];


function formatMoney(
  value: string | null,
  currency: string
) {
  if (!value) {
    return "—";
  }

  return new Intl.NumberFormat(
    "en-US",
    {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }
  ).format(
    Number(value)
  );
}


function formatDate(
  value: string | null
) {
  if (!value) {
    return "—";
  }

  return new Intl.DateTimeFormat(
    "en-US",
    {
      month: "short",
      day: "numeric",
      year: "numeric",
    }
  ).format(
    new Date(
      value.length === 10
        ? `${value}T00:00:00`
        : value
    )
  );
}


function formatDateTime(
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


function getMetadataString(
  activity: Activity,
  key: string
) {
  const value =
    activity.activity_metadata[
      key
    ];

  return typeof value
    === "string"
    ? value
    : null;
}


function activityIcon(
  activityType: string
) {
  switch (activityType) {
    case "email":
      return Mail;

    case "meeting":
      return CalendarDays;

    case "call":
      return Phone;

    case "note":
      return MessageSquare;

    case "document":
      return FileText;

    case "task_completed":
      return CheckCircle2;

    case "ai_action":
      return Bot;

    default:
      return Sparkles;
  }
}


function activityLabel(
  activityType: string
) {
  switch (activityType) {
    case "email":
      return "Email";

    case "meeting":
      return "Meeting";

    case "call":
      return "Call";

    case "note":
      return "Note";

    case "sms":
      return "SMS";

    case "task_completed":
      return "Task";

    case "document":
      return "Document";

    case "ai_action":
      return "AIVA Action";

    case "system":
      return "System";

    default:
      return activityType;
  }
}


function matchesFilter(
  activity: Activity,
  filter: TimelineFilter
) {
  if (filter === "all") {
    return true;
  }

  if (
    filter === "email"
    || filter === "meeting"
    || filter === "note"
  ) {
    return (
      activity.activity_type
      === filter
    );
  }

  return ![
    "email",
    "meeting",
    "note",
  ].includes(
    activity.activity_type
  );
}


export default function OpportunityDetailPage() {
  const params =
    useParams<{
      opportunityId: string;
    }>();

  const opportunityId =
    params.opportunityId;

  const [
    opportunity,
    setOpportunity,
  ] = useState<
    Opportunity | null
  >(null);

  const [
    account,
    setAccount,
  ] = useState<
    Account | null
  >(null);

  const [
    contact,
    setContact,
  ] = useState<
    Contact | null
  >(null);

  const [
    pipeline,
    setPipeline,
  ] = useState<
    Pipeline | null
  >(null);

  const [
    stages,
    setStages,
  ] = useState<
    PipelineStage[]
  >([]);

  const [
    activities,
    setActivities,
  ] = useState<
    Activity[]
  >([]);

  const [
    timelineFilter,
    setTimelineFilter,
  ] = useState<
    TimelineFilter
  >("all");

  const [
    noteBody,
    setNoteBody,
  ] = useState("");

  const [
    noteOpen,
    setNoteOpen,
  ] = useState(false);

  const [
    savingNote,
    setSavingNote,
  ] = useState(false);

  const [
    refreshingTimeline,
    setRefreshingTimeline,
  ] = useState(false);

  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    moving,
    setMoving,
  ] = useState(false);

  const [
    error,
    setError,
  ] = useState<
    string | null
  >(null);


  async function loadTimeline() {
    if (!opportunityId) {
      return;
    }

    try {
      setRefreshingTimeline(
        true
      );

      const data =
        await getOpportunityActivities(
          opportunityId
        );

      setActivities(
        data
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to load activity timeline."
      );
    } finally {
      setRefreshingTimeline(
        false
      );
    }
  }


  useEffect(() => {
    if (!opportunityId) {
      return;
    }

    async function load() {
      try {
        setLoading(true);
        setError(null);

        const opportunityData =
          await getOpportunity(
            opportunityId
          );

        setOpportunity(
          opportunityData
        );

        const [
          accountData,
          pipelineData,
          stageData,
          activityData,
        ] = await Promise.all([
          getAccount(
            opportunityData
              .account_id
          ),

          getPipeline(
            opportunityData
              .pipeline_id
          ),

          getPipelineStages(
            opportunityData
              .pipeline_id
          ),

          getOpportunityActivities(
            opportunityData.id
          ),
        ]);

        setAccount(
          accountData
        );

        setPipeline(
          pipelineData
        );

        setStages(
          stageData
        );

        setActivities(
          activityData
        );

        if (
          opportunityData
            .primary_contact_id
        ) {
          try {
            const contactData =
              await getContact(
                opportunityData
                  .primary_contact_id
              );

            setContact(
              contactData
            );
          } catch {
            setContact(
              null
            );
          }
        }
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Unable to load opportunity."
        );
      } finally {
        setLoading(false);
      }
    }

    void load();
  }, [opportunityId]);


  const currentStage =
    useMemo(() => {
      if (!opportunity) {
        return null;
      }

      return (
        stages.find(
          (stage) =>
            stage.id
            === opportunity.stage_id
        )
        ?? null
      );
    }, [
      opportunity,
      stages,
    ]);


  const filteredActivities =
    useMemo(() => {
      return activities.filter(
        (activity) =>
          matchesFilter(
            activity,
            timelineFilter
          )
      );
    }, [
      activities,
      timelineFilter,
    ]);


  const emailCount =
    useMemo(() => {
      return activities.filter(
        (activity) =>
          activity.activity_type
          === "email"
      ).length;
    }, [activities]);


  const meetingCount =
    useMemo(() => {
      return activities.filter(
        (activity) =>
          activity.activity_type
          === "meeting"
      ).length;
    }, [activities]);


  const lastActivity =
    activities[0] ?? null;


  async function changeStage(
    stageId: string
  ) {
    if (
      !opportunity
      ||
      stageId
      === opportunity.stage_id
    ) {
      return;
    }

    try {
      setMoving(true);
      setError(null);

      const updated =
        await moveOpportunity(
          opportunity.id,
          stageId
        );

      setOpportunity(
        updated
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to move opportunity."
      );
    } finally {
      setMoving(false);
    }
  }


  async function handleAddNote(
    event: FormEvent
  ) {
    event.preventDefault();

    if (
      !opportunity
      ||
      !noteBody.trim()
    ) {
      return;
    }

    try {
      setSavingNote(true);
      setError(null);

      const created =
        await createActivity({
          account_id:
            opportunity.account_id,

          contact_id:
            opportunity
              .primary_contact_id,

          opportunity_id:
            opportunity.id,

          activity_type:
            "note",

          subject:
            "Opportunity Note",

          body:
            noteBody.trim(),

          occurred_at:
            new Date()
              .toISOString(),

          activity_metadata: {
            source:
              "opportunity_detail",
          },
        });

      setActivities(
        (current) =>
          [
            created,
            ...current,
          ].sort(
            (
              first,
              second
            ) =>
              new Date(
                second.occurred_at
              ).getTime()
              -
              new Date(
                first.occurred_at
              ).getTime()
          )
      );

      setNoteBody("");
      setNoteOpen(false);
      setTimelineFilter(
        "all"
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to save note."
      );
    } finally {
      setSavingNote(false);
    }
  }


  if (loading) {
    return (
      <div className="appShell">
        <Sidebar
          active="Opportunities"
        />

        <main className="mainArea">
          <Topbar />

          <div className="pageContent">
            <div className="detailLoading">
              <Loader2
                size={18}
              />

              Loading opportunity...
            </div>
          </div>
        </main>
      </div>
    );
  }


  if (
    error
    && !opportunity
  ) {
    return (
      <div className="appShell">
        <Sidebar
          active="Opportunities"
        />

        <main className="mainArea">
          <Topbar />

          <div className="pageContent">
            <Link
              href="/opportunities"
              className="backLink"
            >
              <ArrowLeft
                size={15}
              />

              Opportunities
            </Link>

            <div className="detailError">
              <strong>
                Opportunity unavailable
              </strong>

              <p>
                {error}
              </p>
            </div>
          </div>
        </main>
      </div>
    );
  }


  if (!opportunity) {
    return null;
  }


  return (
    <div className="appShell">
      <Sidebar
        active="Opportunities"
      />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <Link
            href="/opportunities"
            className="backLink"
          >
            <ArrowLeft
              size={15}
            />

            Back to Opportunities
          </Link>

          <section className="accountHero">
            <div className="accountHeroIdentity">
              <div className="accountHeroLogo">
                {opportunity
                  .name[0]
                  .toUpperCase()}
              </div>

              <div>
                <div className="accountHeroTitle">
                  <h1>
                    {
                      opportunity.name
                    }
                  </h1>

                  <span className="stageBadge">
                    {currentStage?.name
                      ?? "Unknown Stage"}
                  </span>
                </div>

                <div className="accountHeroMeta">
                  {account && (
                    <span>
                      <Building2
                        size={14}
                      />

                      {
                        account.name
                      }
                    </span>
                  )}

                  <span>
                    <CircleDollarSign
                      size={14}
                    />

                    {formatMoney(
                      opportunity.amount,
                      opportunity.currency
                    )}
                  </span>

                  <span>
                    <Target
                      size={14}
                    />

                    {
                      opportunity.probability
                    }
                    %
                  </span>
                </div>
              </div>
            </div>

            <div className="detailActions">
              <select
                value={
                  opportunity.stage_id
                }
                disabled={
                  moving
                }
                onChange={(
                  event
                ) => {
                  void changeStage(
                    event
                      .target
                      .value
                  );
                }}
              >
                {stages.map(
                  (stage) => (
                    <option
                      key={
                        stage.id
                      }
                      value={
                        stage.id
                      }
                    >
                      {
                        stage.name
                      }
                      {" — "}
                      {
                        stage.probability
                      }
                      %
                    </option>
                  )
                )}
              </select>
            </div>
          </section>

          {error && (
            <div className="formError">
              {error}
            </div>
          )}

          <section className="engagementSummary">
            <div>
              <Mail
                size={17}
              />

              <span>
                Emails
              </span>

              <strong>
                {emailCount}
              </strong>
            </div>

            <div>
              <CalendarDays
                size={17}
              />

              <span>
                Meetings
              </span>

              <strong>
                {meetingCount}
              </strong>
            </div>

            <div>
              <MessageSquare
                size={17}
              />

              <span>
                Activities
              </span>

              <strong>
                {
                  activities.length
                }
              </strong>
            </div>

            <div>
              <Clock3
                size={17}
              />

              <span>
                Last Engagement
              </span>

              <strong>
                {lastActivity
                  ? formatDate(
                      lastActivity
                        .occurred_at
                    )
                  : "—"}
              </strong>
            </div>
          </section>

          <div className="accountDetailGrid">
            <div className="accountMainColumn">
              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Deal Overview
                    </h2>

                    <p>
                      Commercial and
                      pipeline information
                      for this opportunity.
                    </p>
                  </div>
                </div>

                <div className="detailFields">
                  <div>
                    <span>
                      Amount
                    </span>

                    <strong>
                      {formatMoney(
                        opportunity.amount,
                        opportunity.currency
                      )}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Probability
                    </span>

                    <strong>
                      {
                        opportunity.probability
                      }
                      %
                    </strong>
                  </div>

                  <div>
                    <span>
                      Expected Close
                    </span>

                    <strong>
                      {formatDate(
                        opportunity
                          .expected_close_date
                      )}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Currency
                    </span>

                    <strong>
                      {
                        opportunity.currency
                      }
                    </strong>
                  </div>

                  <div>
                    <span>
                      Pipeline
                    </span>

                    <strong>
                      {pipeline?.name
                        ?? "—"}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Stage
                    </span>

                    <strong>
                      {currentStage?.name
                        ?? "—"}
                    </strong>
                  </div>
                </div>

                <div className="aivaPlaceholder">
                  <span>
                    DEAL DESCRIPTION
                  </span>

                  <strong>
                    {opportunity.description
                      || "No deal description yet."}
                  </strong>
                </div>
              </section>

              <section className="detailPanel engagementPanel">
                <div className="engagementHeader">
                  <div>
                    <h2>
                      Engagement Timeline
                    </h2>

                    <p>
                      Unified customer
                      communication and
                      activity history.
                    </p>
                  </div>

                  <div className="engagementActions">
                    <button
                      type="button"
                      className="timelineSecondaryButton"
                      disabled={
                        refreshingTimeline
                      }
                      onClick={() => {
                        void loadTimeline();
                      }}
                    >
                      {refreshingTimeline ? (
                        <Loader2
                          size={14}
                        />
                      ) : (
                        <RefreshCcw
                          size={14}
                        />
                      )}

                      Refresh
                    </button>

                    <button
                      type="button"
                      className="timelinePrimaryButton"
                      onClick={() =>
                        setNoteOpen(
                          (value) =>
                            !value
                        )
                      }
                    >
                      <Plus
                        size={14}
                      />

                      Log Note
                    </button>
                  </div>
                </div>

                {noteOpen && (
                  <form
                    className="noteComposer"
                    onSubmit={
                      handleAddNote
                    }
                  >
                    <div className="noteComposerTitle">
                      <MessageSquare
                        size={16}
                      />

                      Add opportunity note
                    </div>

                    <textarea
                      rows={4}
                      value={
                        noteBody
                      }
                      onChange={(
                        event
                      ) =>
                        setNoteBody(
                          event
                            .target
                            .value
                        )
                      }
                      placeholder="Add customer context, follow-up notes, objections, next steps..."
                    />

                    <div className="noteComposerFooter">
                      <button
                        type="button"
                        className="timelineSecondaryButton"
                        onClick={() => {
                          setNoteBody("");
                          setNoteOpen(
                            false
                          );
                        }}
                      >
                        Cancel
                      </button>

                      <button
                        type="submit"
                        className="timelinePrimaryButton"
                        disabled={
                          savingNote
                          ||
                          !noteBody.trim()
                        }
                      >
                        {savingNote ? (
                          <Loader2
                            size={14}
                          />
                        ) : (
                          <Send
                            size={14}
                          />
                        )}

                        {savingNote
                          ? "Saving..."
                          : "Save Note"}
                      </button>
                    </div>
                  </form>
                )}

                <div className="timelineFilters">
                  {TIMELINE_FILTERS.map(
                    (item) => (
                      <button
                        key={
                          item.value
                        }
                        type="button"
                        className={
                          timelineFilter
                          === item.value
                            ? "timelineFilter active"
                            : "timelineFilter"
                        }
                        onClick={() =>
                          setTimelineFilter(
                            item.value
                          )
                        }
                      >
                        {
                          item.label
                        }
                      </button>
                    )
                  )}
                </div>

                <div className="timeline">
                  {filteredActivities.length
                  === 0 ? (
                    <div className="timelineEmpty">
                      <Sparkles
                        size={26}
                      />

                      <strong>
                        No activities yet
                      </strong>

                      <p>
                        Emails, meetings,
                        calls, notes, tasks
                        and AIVA actions will
                        appear here.
                      </p>
                    </div>
                  ) : (
                    filteredActivities.map(
                      (activity) => {
                        const Icon =
                          activityIcon(
                            activity
                              .activity_type
                          );

                        const emailThreadId =
                          getMetadataString(
                            activity,
                            "email_thread_id"
                          );

                        const provider =
                          getMetadataString(
                            activity,
                            "provider"
                          );

                        return (
                          <article
                            key={
                              activity.id
                            }
                            className="timelineItem"
                          >
                            <div className="timelineRail">
                              <div
                                className={
                                  `timelineIcon activity-${activity.activity_type}`
                                }
                              >
                                <Icon
                                  size={15}
                                />
                              </div>

                              <div className="timelineLine" />
                            </div>

                            <div className="timelineContent">
                              <div className="timelineTop">
                                <div>
                                  <div className="timelineType">
                                    {activityLabel(
                                      activity
                                        .activity_type
                                    )}

                                    {activity.direction && (
                                      <span>
                                        {
                                          activity.direction
                                        }
                                      </span>
                                    )}

                                    {provider && (
                                      <span>
                                        {
                                          provider
                                        }
                                      </span>
                                    )}
                                  </div>

                                  <h3>
                                    {
                                      activity.subject
                                    }
                                  </h3>
                                </div>

                                <time>
                                  {formatDateTime(
                                    activity
                                      .occurred_at
                                  )}
                                </time>
                              </div>

                              {activity.body && (
                                <p className="timelineBody">
                                  {
                                    activity.body
                                  }
                                </p>
                              )}

                              {emailThreadId && (
                                <Link
                                  className="timelineLink"
                                  href={
                                    `/email/${emailThreadId}`
                                  }
                                >
                                  <Mail
                                    size={12}
                                  />

                                  Open email thread
                                </Link>
                              )}
                            </div>
                          </article>
                        );
                      }
                    )
                  )}
                </div>
              </section>

              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Pipeline Progress
                    </h2>

                    <p>
                      Move the opportunity
                      through the sales
                      process.
                    </p>
                  </div>
                </div>

                <div className="detailFields">
                  {stages.map(
                    (stage) => (
                      <div
                        key={
                          stage.id
                        }
                      >
                        <span>
                          {
                            stage.position
                          }
                          .
                          {" "}
                          {
                            stage.name
                          }
                        </span>

                        <strong>
                          {stage.id
                          === opportunity.stage_id
                            ? "Current"
                            : `${stage.probability}%`}
                        </strong>
                      </div>
                    )
                  )}
                </div>
              </section>
            </div>

            <aside className="accountSideColumn">
              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Account
                    </h2>

                    <p>
                      Customer account
                      attached to this
                      opportunity.
                    </p>
                  </div>
                </div>

                <div className="detailFields">
                  <div>
                    <span>
                      Account Name
                    </span>

                    {account ? (
                      <Link
                        href={
                          `/accounts/${account.id}`
                        }
                      >
                        {
                          account.name
                        }
                      </Link>
                    ) : (
                      <strong>
                        —
                      </strong>
                    )}
                  </div>

                  <div>
                    <span>
                      Lifecycle
                    </span>

                    <strong>
                      {account
                        ?.lifecycle_stage
                        ?? "—"}
                    </strong>
                  </div>
                </div>
              </section>

              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Primary Contact
                    </h2>

                    <p>
                      Main contact associated
                      with this deal.
                    </p>
                  </div>
                </div>

                <div className="detailFields">
                  <div>
                    <span>
                      Contact
                    </span>

                    <strong>
                      {contact
                        ? `${contact.first_name} ${contact.last_name}`
                        : "—"}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Job Title
                    </span>

                    <strong>
                      {contact
                        ?.job_title
                        ?? "—"}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Email
                    </span>

                    <strong>
                      {contact
                        ?.email
                        ?? "—"}
                    </strong>
                  </div>
                </div>
              </section>

              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Deal Status
                    </h2>

                    <p>
                      Current lifecycle
                      status.
                    </p>
                  </div>
                </div>

                <div className="detailFields">
                  <div>
                    <span>
                      Category
                    </span>

                    <strong>
                      {currentStage?.category
                        ?? "—"}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Closed At
                    </span>

                    <strong>
                      {formatDate(
                        opportunity
                          .closed_at
                      )}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Created
                    </span>

                    <strong>
                      {formatDate(
                        opportunity
                          .created_at
                      )}
                    </strong>
                  </div>
                </div>
              </section>

              <section className="detailPanel aiAccountCard">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      AIVA Deal Intelligence
                    </h2>

                    <p>
                      AI-assisted revenue
                      intelligence.
                    </p>
                  </div>
                </div>

                <div className="aivaPlaceholder">
                  <Handshake
                    size={20}
                  />

                  <span>
                    AI COMING NEXT
                  </span>

                  <strong>
                    Deal risk,
                    next-best action and
                    forecast intelligence.
                  </strong>

                  <p>
                    AIVA will analyze
                    activities, engagement
                    and pipeline velocity
                    to recommend the next
                    action.
                  </p>
                </div>
              </section>
            </aside>
          </div>
        </div>
      </main>

      <style jsx>{`
        .detailLoading {
          min-height: 300px;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 8px;
          color: var(--muted);
        }

        .engagementSummary {
          display: grid;
          grid-template-columns:
            repeat(
              4,
              minmax(0, 1fr)
            );
          gap: 12px;
          margin: 16px 0;
        }

        .engagementSummary > div {
          display: grid;
          grid-template-columns:
            auto 1fr auto;
          align-items: center;
          gap: 9px;
          padding: 14px 15px;
          border: 1px solid var(--border);
          border-radius: 11px;
          background: white;
        }

        .engagementSummary span {
          color: var(--muted);
          font-size: 10px;
        }

        .engagementSummary strong {
          font-size: 12px;
        }

        .engagementPanel {
          overflow: hidden;
        }

        .engagementHeader {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 14px;
          padding: 16px;
          border-bottom: 1px solid #eef0f4;
        }

        .engagementHeader h2 {
          margin: 0;
          font-size: 13px;
        }

        .engagementHeader p {
          margin: 4px 0 0;
          color: var(--muted);
          font-size: 10px;
        }

        .engagementActions {
          display: flex;
          gap: 7px;
        }

        .timelinePrimaryButton,
        .timelineSecondaryButton {
          height: 34px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 6px;
          padding: 0 10px;
          border-radius: 8px;
          font-size: 9px;
          font-weight: 800;
        }

        .timelinePrimaryButton {
          border: 0;
          background: var(--primary);
          color: white;
        }

        .timelineSecondaryButton {
          border: 1px solid var(--border);
          background: white;
          color: var(--text);
        }

        .timelinePrimaryButton:disabled,
        .timelineSecondaryButton:disabled {
          opacity: 0.55;
        }

        .noteComposer {
          margin: 14px 14px 0;
          padding: 13px;
          border: 1px solid #ddd9ff;
          border-radius: 10px;
          background: #faf9ff;
        }

        .noteComposerTitle {
          display: flex;
          align-items: center;
          gap: 7px;
          margin-bottom: 9px;
          font-size: 10px;
          font-weight: 800;
        }

        .noteComposer textarea {
          box-sizing: border-box;
          width: 100%;
          resize: vertical;
          padding: 10px;
          border: 1px solid var(--border);
          border-radius: 8px;
          outline: none;
          background: white;
          color: var(--text);
          font: inherit;
          font-size: 10px;
          line-height: 1.6;
        }

        .noteComposer textarea:focus {
          border-color: var(--primary);
        }

        .noteComposerFooter {
          display: flex;
          justify-content: flex-end;
          gap: 7px;
          margin-top: 9px;
        }

        .timelineFilters {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          padding: 13px 14px;
          border-bottom: 1px solid #eef0f4;
        }

        .timelineFilter {
          padding: 6px 9px;
          border: 1px solid var(--border);
          border-radius: 999px;
          background: white;
          color: var(--muted);
          font-size: 8px;
          font-weight: 800;
        }

        .timelineFilter.active {
          border-color: var(--primary);
          background: var(--primary-soft);
          color: var(--primary);
        }

        .timeline {
          padding: 4px 14px 15px;
        }

        .timelineItem {
          display: grid;
          grid-template-columns:
            34px minmax(0, 1fr);
          gap: 10px;
        }

        .timelineRail {
          display: flex;
          flex-direction: column;
          align-items: center;
        }

        .timelineIcon {
          width: 30px;
          height: 30px;
          flex: 0 0 auto;
          display: grid;
          place-items: center;
          border-radius: 9px;
          background: #f2f3f7;
          color: #656a77;
        }

        .activity-email {
          background: #eef4ff;
          color: #4671c9;
        }

        .activity-meeting {
          background: #f2efff;
          color: #7057d9;
        }

        .activity-note {
          background: #fff7df;
          color: #a67714;
        }

        .activity-call {
          background: #edf9f1;
          color: #298858;
        }

        .activity-ai_action {
          background: #f6eeff;
          color: #8a4ac7;
        }

        .timelineLine {
          width: 1px;
          flex: 1;
          min-height: 24px;
          background: #e8eaf0;
        }

        .timelineItem:last-child
        .timelineLine {
          display: none;
        }

        .timelineContent {
          min-width: 0;
          padding: 5px 0 17px;
        }

        .timelineTop {
          display: flex;
          justify-content: space-between;
          gap: 12px;
        }

        .timelineType {
          display: flex;
          align-items: center;
          flex-wrap: wrap;
          gap: 5px;
          color: var(--primary);
          font-size: 8px;
          font-weight: 800;
          text-transform: uppercase;
        }

        .timelineType span {
          padding: 2px 5px;
          border-radius: 999px;
          background: #f2f3f6;
          color: var(--muted);
          font-size: 7px;
        }

        .timelineContent h3 {
          margin: 5px 0 0;
          font-size: 11px;
        }

        .timelineTop time {
          flex: 0 0 auto;
          color: var(--muted);
          font-size: 8px;
        }

        .timelineBody {
          margin: 7px 0 0;
          color: #555b68;
          font-size: 10px;
          line-height: 1.55;
          white-space: pre-wrap;
        }

        .timelineLink {
          display: inline-flex;
          align-items: center;
          gap: 5px;
          margin-top: 9px;
          color: var(--primary);
          font-size: 8px;
          font-weight: 800;
          text-decoration: none;
        }

        .timelineEmpty {
          min-height: 220px;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 7px;
          color: var(--muted);
          text-align: center;
        }

        .timelineEmpty strong {
          color: var(--text);
          font-size: 11px;
        }

        .timelineEmpty p {
          max-width: 310px;
          margin: 0;
          font-size: 9px;
          line-height: 1.5;
        }

        @media (
          max-width: 900px
        ) {
          .engagementSummary {
            grid-template-columns:
              repeat(
                2,
                1fr
              );
          }

          .engagementHeader {
            flex-direction: column;
          }
        }

        @media (
          max-width: 600px
        ) {
          .engagementSummary {
            grid-template-columns:
              1fr;
          }

          .engagementActions {
            width: 100%;
          }

          .timelinePrimaryButton,
          .timelineSecondaryButton {
            flex: 1;
          }

          .timelineTop {
            flex-direction: column;
          }
        }
      `}</style>
    </div>
  );
}
TSX
