"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import Link from "next/link";

import {
  Activity as ActivityIcon,
  ArrowLeft,
  Building2,
  CalendarDays,
  CheckSquare2,
  CircleDollarSign,
  Clock3,
  ExternalLink,
  FileText,
  Handshake,
  HeartPulse,
  Mail,
  MapPin,
  MessageSquare,
  Pencil,
  Phone,
  Plus,
  Send,
  Sparkles,
  Users,
} from "lucide-react";

import {
  useParams,
} from "next/navigation";

import {
  CreateContactModal,
} from "@/components/accounts/create-contact-modal";

import {
  EditAccountModal,
} from "@/components/accounts/edit-account-modal";

import {
  LogActivityModal,
} from "@/components/accounts/log-activity-modal";

import {
  Sidebar,
} from "@/components/sidebar";

import {
  Topbar,
} from "@/components/topbar";

import {
  getAccount,
} from "@/lib/accounts";

import {
  getAccountActivities,
} from "@/lib/activities";

import {
  getContacts,
} from "@/lib/contacts";

import {
  getEmailThreads,
} from "@/lib/emails";

import {
  getOpportunities,
} from "@/lib/opportunities";

import {
  getTasks,
} from "@/lib/tasks";

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
  EmailThread,
} from "@/types/email";

import type {
  Opportunity,
} from "@/types/opportunity";

import type {
  Task,
} from "@/types/task";


type TimelineKind =
  | "activity"
  | "email"
  | "task"
  | "opportunity";


type TimelineItem = {
  id: string;
  kind: TimelineKind;
  title: string;
  subtitle: string;
  description: string | null;
  occurredAt: string;
  href?: string;
};


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


function formatMoney(
  value: string | null,
  currency = "USD"
) {
  if (!value) {
    return "—";
  }

  const numericValue =
    Number(value);

  if (
    !Number.isFinite(
      numericValue
    )
  ) {
    return "—";
  }

  return new Intl.NumberFormat(
    "en-US",
    {
      style: "currency",
      currency,
      notation: "compact",
      maximumFractionDigits: 1,
    }
  ).format(
    numericValue
  );
}


function displayLabel(
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


function daysSince(
  value: string | null
) {
  if (!value) {
    return null;
  }

  const timestamp =
    new Date(value).getTime();

  if (
    Number.isNaN(
      timestamp
    )
  ) {
    return null;
  }

  return Math.max(
    0,
    Math.floor(
      (
        Date.now()
        -
        timestamp
      )
      /
      86400000
    )
  );
}


function ActivityTypeIcon({
  type,
}: {
  type: Activity["activity_type"];
}) {
  if (type === "email") {
    return (
      <Mail size={16} />
    );
  }

  if (type === "call") {
    return (
      <Phone size={16} />
    );
  }

  if (type === "meeting") {
    return (
      <CalendarDays
        size={16}
      />
    );
  }

  if (type === "document") {
    return (
      <FileText
        size={16}
      />
    );
  }

  if (type === "note") {
    return (
      <MessageSquare
        size={16}
      />
    );
  }

  return (
    <ActivityIcon
      size={16}
    />
  );
}


function TimelineIcon({
  kind,
}: {
  kind: TimelineKind;
}) {
  if (kind === "email") {
    return (
      <Mail size={16} />
    );
  }

  if (kind === "task") {
    return (
      <CheckSquare2
        size={16}
      />
    );
  }

  if (
    kind === "opportunity"
  ) {
    return (
      <Handshake
        size={16}
      />
    );
  }

  return (
    <ActivityIcon
      size={16}
    />
  );
}


export default function AccountDetailPage() {
  const params =
    useParams<{
      accountId: string;
    }>();

  const accountId =
    params.accountId;


  const [
    account,
    setAccount,
  ] = useState<Account | null>(
    null
  );

  const [
    contacts,
    setContacts,
  ] = useState<Contact[]>([]);

  const [
    activities,
    setActivities,
  ] = useState<Activity[]>([]);

  const [
    opportunities,
    setOpportunities,
  ] = useState<
    Opportunity[]
  >([]);

  const [
    tasks,
    setTasks,
  ] = useState<Task[]>([]);

  const [
    emailThreads,
    setEmailThreads,
  ] = useState<
    EmailThread[]
  >([]);


  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    relatedLoading,
    setRelatedLoading,
  ] = useState(false);

  const [
    error,
    setError,
  ] = useState<
    string | null
  >(null);

  const [
    relationshipWarning,
    setRelationshipWarning,
  ] = useState<
    string | null
  >(null);


  const [
    contactModalOpen,
    setContactModalOpen,
  ] = useState(false);

  const [
    activityModalOpen,
    setActivityModalOpen,
  ] = useState(false);

  const [
    editModalOpen,
    setEditModalOpen,
  ] = useState(false);


  useEffect(() => {
    if (!accountId) {
      return;
    }

    let cancelled =
      false;


    async function loadRelatedData() {
      setRelatedLoading(
        true
      );

      setRelationshipWarning(
        null
      );


      const results =
        await Promise.allSettled([
          getContacts(
            accountId
          ),

          getAccountActivities(
            accountId
          ),

          getOpportunities({
            accountId,
          }),

          getTasks({
            accountId,
            limit: 50,
          }),

          getEmailThreads({
            accountId,
            limit: 50,
          }),
        ]);


      if (cancelled) {
        return;
      }


      const [
        contactsResult,
        activitiesResult,
        opportunitiesResult,
        tasksResult,
        emailResult,
      ] = results;


      if (
        contactsResult.status
        === "fulfilled"
      ) {
        setContacts(
          contactsResult.value
        );
      } else {
        setContacts([]);
      }


      if (
        activitiesResult.status
        === "fulfilled"
      ) {
        setActivities(
          activitiesResult.value
        );
      } else {
        setActivities([]);
      }


      if (
        opportunitiesResult.status
        === "fulfilled"
      ) {
        setOpportunities(
          opportunitiesResult.value
        );
      } else {
        setOpportunities([]);
      }


      if (
        tasksResult.status
        === "fulfilled"
      ) {
        setTasks(
          tasksResult.value
        );
      } else {
        setTasks([]);
      }


      if (
        emailResult.status
        === "fulfilled"
      ) {
        setEmailThreads(
          emailResult.value
        );
      } else {
        setEmailThreads([]);
      }


      if (
        results.some(
          (result) =>
            result.status
            === "rejected"
        )
      ) {
        setRelationshipWarning(
          "Some related CRM data could not be loaded."
        );
      }


      setRelatedLoading(
        false
      );
    }


    async function loadAccount() {
      try {
        setLoading(true);
        setError(null);

        const accountResult =
          await getAccount(
            accountId
          );

        if (cancelled) {
          return;
        }

        setAccount(
          accountResult
        );

        setLoading(false);

        void loadRelatedData();
      } catch (err) {
        if (cancelled) {
          return;
        }

        setError(
          err instanceof Error
            ? err.message
            : "Unable to load account."
        );

        setLoading(false);
      }
    }


    void loadAccount();


    return () => {
      cancelled = true;
    };
  }, [
    accountId,
  ]);


  const primaryContact =
    useMemo(
      () =>
        contacts.find(
          (contact) =>
            contact.is_primary
        )
        ??
        null,
      [
        contacts,
      ]
    );


  const openTasks =
    useMemo(
      () =>
        tasks.filter(
          (task) =>
            task.status
              === "open"
            ||
            task.status
              === "in_progress"
        ),
      [
        tasks,
      ]
    );


  const overdueTasks =
    useMemo(
      () =>
        openTasks.filter(
          (task) => {
            if (!task.due_at) {
              return false;
            }

            return (
              new Date(
                task.due_at
              ).getTime()
              <
              Date.now()
            );
          }
        ),
      [
        openTasks,
      ]
    );


  const pipelineValue =
    useMemo(() => {
      return opportunities.reduce(
        (
          total,
          opportunity
        ) => {
          const amount =
            Number(
              opportunity.amount
              ?? 0
            );

          return (
            total
            +
            (
              Number.isFinite(
                amount
              )
                ? amount
                : 0
            )
          );
        },
        0
      );
    }, [
      opportunities,
    ]);


  const emailActivities =
    useMemo(
      () =>
        activities.filter(
          (activity) =>
            activity.activity_type
            === "email"
        ),
      [
        activities,
      ]
    );


  const inboundEmailActivityCount =
    useMemo(
      () =>
        emailActivities.filter(
          (activity) =>
            activity.direction
            === "inbound"
        ).length,
      [
        emailActivities,
      ]
    );


  const outboundEmailActivityCount =
    useMemo(
      () =>
        emailActivities.filter(
          (activity) =>
            activity.direction
            === "outbound"
        ).length,
      [
        emailActivities,
      ]
    );


  const sortedEmailThreads =
    useMemo(() => {
      return [
        ...emailThreads,
      ].sort(
        (
          a,
          b
        ) => {
          const aDate =
            a.last_message_at
            ??
            a.updated_at;

          const bDate =
            b.last_message_at
            ??
            b.updated_at;

          return (
            new Date(
              bDate
            ).getTime()
            -
            new Date(
              aDate
            ).getTime()
          );
        }
      );
    }, [
      emailThreads,
    ]);


  const latestEmailThread =
    sortedEmailThreads[0]
    ??
    null;


  const unifiedTimeline =
    useMemo<
      TimelineItem[]
    >(() => {
      const activityItems:
        TimelineItem[] =
        activities.map(
          (activity) => ({
            id:
              `activity-${activity.id}`,

            kind:
              "activity",

            title:
              activity.subject,

            subtitle:
              [
                displayLabel(
                  activity.activity_type
                ),

                activity.direction
                  ? displayLabel(
                      activity.direction
                    )
                  : null,
              ]
                .filter(Boolean)
                .join(" · "),

            description:
              activity.body,

            occurredAt:
              activity.occurred_at,
          })
        );


      const emailItems:
        TimelineItem[] =
        emailThreads.map(
          (thread) => ({
            id:
              `email-${thread.id}`,

            kind:
              "email",

            title:
              thread.subject,

            subtitle:
              `Email Conversation · ${displayLabel(
                thread.provider
              )}`,

            description:
              thread.snippet,

            occurredAt:
              thread.last_message_at
              ??
              thread.updated_at,

            href:
              "/email",
          })
        );


      const taskItems:
        TimelineItem[] =
        tasks.map(
          (task) => ({
            id:
              `task-${task.id}`,

            kind:
              "task",

            title:
              task.title,

            subtitle:
              `${displayLabel(
                task.task_type
              )} · ${displayLabel(
                task.status
              )}`,

            description:
              task.description,

            occurredAt:
              task.updated_at,

            href:
              "/tasks",
          })
        );


      const opportunityItems:
        TimelineItem[] =
        opportunities.map(
          (
            opportunity
          ) => ({
            id:
              `opportunity-${opportunity.id}`,

            kind:
              "opportunity",

            title:
              opportunity.name,

            subtitle:
              `Opportunity · ${formatMoney(
                opportunity.amount,
                opportunity.currency
              )}`,

            description:
              opportunity.description,

            occurredAt:
              opportunity.updated_at,

            href:
              `/opportunities/${opportunity.id}`,
          })
        );


      return [
        ...activityItems,
        ...emailItems,
        ...taskItems,
        ...opportunityItems,
      ]
        .sort(
          (
            a,
            b
          ) =>
            new Date(
              b.occurredAt
            ).getTime()
            -
            new Date(
              a.occurredAt
            ).getTime()
        )
        .slice(
          0,
          20
        );
    }, [
      activities,
      emailThreads,
      tasks,
      opportunities,
    ]);


  const latestInteractionDate =
    unifiedTimeline.length > 0
      ? unifiedTimeline[0]
          .occurredAt
      : null;


  const daysSinceInteraction =
    daysSince(
      latestInteractionDate
    );


  const lifecycleStage =
  account?.lifecycle_stage
  ?? null;


const accountHealth =
  useMemo(() => {
    let score =
      10;


    if (
      contacts.length
      > 0
    ) {
      score += 15;
    }


    if (
      primaryContact
    ) {
      score += 10;
    }


    if (
      opportunities.length
      > 0
    ) {
      score += 15;
    }


    if (
      emailThreads.length
      > 0
    ) {
      score += 10;
    }


    if (
      openTasks.length
      > 0
    ) {
      score += 5;
    }


    if (
      daysSinceInteraction
      === null
    ) {
      score -= 10;
    } else if (
      daysSinceInteraction
      <= 7
    ) {
      score += 15;
    } else if (
      daysSinceInteraction
      <= 30
    ) {
      score += 8;
    } else if (
      daysSinceInteraction
      > 60
    ) {
      score -= 20;
    } else {
      score -= 10;
    }


    if (
      overdueTasks.length
      > 0
    ) {
      score -= Math.min(
        25,
        overdueTasks.length
        *
        5
      );
    }


    if (
      lifecycleStage
      === "inactive"
    ) {
      score -= 20;
    }


    if (
      lifecycleStage
      === "churned"
    ) {
      score -= 30;
    }


    score =
      Math.max(
        0,
        Math.min(
          100,
          score
        )
      );


    if (
      score >= 75
    ) {
      return {
        score,
        label:
          "Healthy",

        description:
          "Strong relationship depth, engagement and commercial activity.",
      };
    }


    if (
      score >= 50
    ) {
      return {
        score,
        label:
          "Needs Attention",

        description:
          "The relationship has useful signals but needs additional engagement or follow-up.",
      };
    }


    return {
      score,
      label:
        "At Risk",

      description:
        "Relationship depth or engagement is currently weak and requires attention.",
    };
  }, [
    lifecycleStage,
    contacts.length,
    primaryContact,
    opportunities.length,
    emailThreads.length,
    openTasks.length,
    overdueTasks.length,
    daysSinceInteraction,
  ]);


  if (loading) {
    return (
      <div className="appShell">
        <Sidebar
          active="Accounts"
        />

        <main className="mainArea">
          <Topbar />

          <div className="pageContent">
            <div className="detailLoading">
              Loading account...
            </div>
          </div>
        </main>
      </div>
    );
  }


  if (
    error
    ||
    !account
  ) {
    return (
      <div className="appShell">
        <Sidebar
          active="Accounts"
        />

        <main className="mainArea">
          <Topbar />

          <div className="pageContent">
            <Link
              href="/accounts"
              className="backLink"
            >
              <ArrowLeft
                size={15}
              />

              Accounts
            </Link>

            <div className="detailError">
              <strong>
                Account unavailable
              </strong>

              <p>
                {error
                  ??
                  "Account not found."}
              </p>
            </div>
          </div>
        </main>
      </div>
    );
  }


  const location = [
    account.billing_city,
    account.billing_state,
    account.billing_country,
  ]
    .filter(Boolean)
    .join(", ");


  const billingAddress = [
    account.billing_address_line1,
    account.billing_address_line2,
    account.billing_city,
    account.billing_state,
    account.billing_postal_code,
    account.billing_country,
  ]
    .filter(Boolean)
    .join(", ");


  return (
    <div className="appShell">
      <Sidebar
        active="Accounts"
      />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <Link
            href="/accounts"
            className="backLink"
          >
            <ArrowLeft
              size={15}
            />

            Back to Accounts
          </Link>


          <section className="accountHero">
            <div className="accountHeroIdentity">
              <div className="accountHeroLogo">
                {account.name
                  .charAt(0)
                  .toUpperCase()}
              </div>

              <div>
                <div className="accountHeroTitle">
                  <h1>
                    {
                      account.name
                    }
                  </h1>

                  <span
                    className={
                      `stageBadge stage-${account.lifecycle_stage}`
                    }
                  >
                    {displayLabel(
                      account
                        .lifecycle_stage
                    )}
                  </span>
                </div>

                <div className="accountHeroMeta">
                  {account.domain && (
                    <span>
                      <Building2
                        size={14}
                      />

                      {
                        account.domain
                      }
                    </span>
                  )}

                  {location && (
                    <span>
                      <MapPin
                        size={14}
                      />

                      {
                        location
                      }
                    </span>
                  )}
                </div>
              </div>
            </div>


            <div className="detailActions">
              <button
                type="button"
                className="secondaryButton"
                onClick={() =>
                  setEditModalOpen(
                    true
                  )
                }
              >
                <Pencil
                  size={15}
                />

                Edit
              </button>

              <button
                type="button"
                className="secondaryButton"
                onClick={() =>
                  setActivityModalOpen(
                    true
                  )
                }
              >
                <ActivityIcon
                  size={15}
                />

                Log Activity
              </button>

              <button
                type="button"
                className="createButton"
                onClick={() =>
                  setContactModalOpen(
                    true
                  )
                }
              >
                <Plus
                  size={15}
                />

                Add Contact
              </button>
            </div>
          </section>


          <div
            className="statsGrid"
            style={{
              marginBottom:
                "16px",
            }}
          >
            <div className="statCard">
              <div className="statLabel">
                Account Health
              </div>

              <div className="statValue">
                {
                  accountHealth.score
                }
              </div>

              <div className="statMeta">
                <HeartPulse
                  size={14}
                />

                {
                  accountHealth.label
                }
              </div>
            </div>


            <div className="statCard">
              <div className="statLabel">
                Pipeline Value
              </div>

              <div className="statValue">
                {formatMoney(
                  String(
                    pipelineValue
                  )
                )}
              </div>

              <div className="statMeta">
                <CircleDollarSign
                  size={14}
                />

                {
                  opportunities.length
                }{" "}
                linked deals
              </div>
            </div>


            <div className="statCard">
              <div className="statLabel">
                Open Tasks
              </div>

              <div className="statValue">
                {
                  openTasks.length
                }
              </div>

              <div className="statMeta">
                <CheckSquare2
                  size={14}
                />

                {
                  overdueTasks.length
                }{" "}
                overdue
              </div>
            </div>


            <div className="statCard">
              <div className="statLabel">
                Last Interaction
              </div>

              <div className="statValue">
                {daysSinceInteraction
                  === null
                  ? "—"
                  : daysSinceInteraction}
              </div>

              <div className="statMeta">
                <Clock3
                  size={14}
                />

                {daysSinceInteraction
                  === null
                  ? "No interaction"
                  : daysSinceInteraction
                    === 0
                    ? "Today"
                    : "Days ago"}
              </div>
            </div>
          </div>


          {relatedLoading && (
            <div
              style={{
                marginBottom:
                  "12px",

                color:
                  "var(--muted)",

                fontSize:
                  "10px",
              }}
            >
              Loading related account
              data...
            </div>
          )}


          {relationshipWarning && (
            <div
              className="formError"
              style={{
                marginBottom:
                  "16px",
              }}
            >
              {
                relationshipWarning
              }
            </div>
          )}


          <div className="accountDetailGrid">
            <div className="accountMainColumn">

              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Account Health
                    </h2>

                    <p>
                      Relationship health
                      derived from CRM
                      engagement signals.
                    </p>
                  </div>
                </div>


                <div
                  style={{
                    display:
                      "grid",

                    gridTemplateColumns:
                      "110px 1fr",

                    gap:
                      "18px",

                    alignItems:
                      "center",
                  }}
                >
                  <div
                    style={{
                      width:
                        "92px",

                      height:
                        "92px",

                      borderRadius:
                        "50%",

                      border:
                        "8px solid var(--primary-soft)",

                      display:
                        "grid",

                      placeItems:
                        "center",

                      fontWeight:
                        800,

                      fontSize:
                        "22px",
                    }}
                  >
                    {
                      accountHealth.score
                    }
                  </div>


                  <div>
                    <strong
                      style={{
                        display:
                          "block",

                        marginBottom:
                          "5px",
                      }}
                    >
                      {
                        accountHealth.label
                      }
                    </strong>

                    <p
                      style={{
                        margin:
                          "0 0 10px",

                        color:
                          "var(--muted)",

                        fontSize:
                          "10px",

                        lineHeight:
                          1.6,
                      }}
                    >
                      {
                        accountHealth.description
                      }
                    </p>


                    <div
                      style={{
                        display:
                          "flex",

                        gap:
                          "8px",

                        flexWrap:
                          "wrap",
                      }}
                    >
                      <span className="filterChip">
                        {
                          contacts.length
                        }{" "}
                        contacts
                      </span>

                      <span className="filterChip">
                        {primaryContact
                          ? "Primary contact set"
                          : "No primary contact"}
                      </span>

                      <span className="filterChip">
                        {
                          opportunities.length
                        }{" "}
                        opportunities
                      </span>

                      <span className="filterChip">
                        {
                          emailThreads.length
                        }{" "}
                        email threads
                      </span>

                      <span className="filterChip">
                        {
                          overdueTasks.length
                        }{" "}
                        overdue tasks
                      </span>
                    </div>
                  </div>
                </div>
              </section>


              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Email Communications
                    </h2>

                    <p>
                      Conversations and email
                      activity linked to
                      this account.
                    </p>
                  </div>

                  <Link
                    href="/email"
                    className="textButton"
                  >
                    Open Email
                  </Link>
                </div>


                <div
                  className="statsGrid"
                  style={{
                    marginBottom:
                      "14px",
                  }}
                >
                  <div className="statCard">
                    <div className="statLabel">
                      Conversations
                    </div>

                    <div className="statValue">
                      {
                        emailThreads.length
                      }
                    </div>

                    <div className="statMeta">
                      <Mail
                        size={14}
                      />

                      Actual email threads
                    </div>
                  </div>


                  <div className="statCard">
                    <div className="statLabel">
                      Inbound Activities
                    </div>

                    <div className="statValue">
                      {
                        inboundEmailActivityCount
                      }
                    </div>

                    <div className="statMeta">
                      <Mail
                        size={14}
                      />

                      Logged inbound email
                    </div>
                  </div>


                  <div className="statCard">
                    <div className="statLabel">
                      Outbound Activities
                    </div>

                    <div className="statValue">
                      {
                        outboundEmailActivityCount
                      }
                    </div>

                    <div className="statMeta">
                      <Send
                        size={14}
                      />

                      Logged outbound email
                    </div>
                  </div>
                </div>


                {latestEmailThread && (
                  <div
                    style={{
                      marginBottom:
                        "14px",

                      padding:
                        "12px",

                      border:
                        "1px solid var(--border)",

                      borderRadius:
                        "10px",
                    }}
                  >
                    <span
                      style={{
                        display:
                          "block",

                        marginBottom:
                          "5px",

                        color:
                          "var(--muted)",

                        fontSize:
                          "9px",

                        textTransform:
                          "uppercase",

                        letterSpacing:
                          ".08em",
                      }}
                    >
                      Latest conversation
                    </span>

                    <strong>
                      {
                        latestEmailThread
                          .subject
                      }
                    </strong>

                    {latestEmailThread
                      .snippet && (
                      <p
                        style={{
                          margin:
                            "5px 0 0",

                          color:
                            "var(--muted)",

                          fontSize:
                            "10px",

                          lineHeight:
                            1.5,
                        }}
                      >
                        {
                          latestEmailThread
                            .snippet
                        }
                      </p>
                    )}
                  </div>
                )}


                {sortedEmailThreads.length
                  === 0 ? (
                  <div className="miniEmptyState">
                    <Mail
                      size={21}
                    />

                    <strong>
                      No email conversations
                    </strong>

                    <span>
                      No actual email
                      threads are linked
                      to this account yet.
                    </span>
                  </div>
                ) : (
                  <div className="contactList">
                    {sortedEmailThreads
                      .slice(
                        0,
                        5
                      )
                      .map(
                        (
                          thread
                        ) => (
                          <Link
                            key={
                              thread.id
                            }
                            href="/email"
                            className="contactCard"
                            style={{
                              textDecoration:
                                "none",

                              color:
                                "inherit",
                            }}
                          >
                            <div className="contactAvatar">
                              <Mail
                                size={15}
                              />
                            </div>

                            <div className="contactCardBody">
                              <div className="contactName">
                                <strong>
                                  {
                                    thread.subject
                                  }
                                </strong>

                                <span>
                                  {
                                    displayLabel(
                                      thread.provider
                                    )
                                  }
                                </span>
                              </div>

                              <p>
                                {
                                  thread.snippet
                                  ||
                                  "Email conversation"
                                }
                              </p>

                              <span
                                style={{
                                  color:
                                    "var(--muted)",

                                  fontSize:
                                    "9px",
                                }}
                              >
                                Last message:{" "}
                                {formatDateTime(
                                  thread.last_message_at
                                  ??
                                  thread.updated_at
                                )}
                              </span>
                            </div>
                          </Link>
                        )
                      )}
                  </div>
                )}
              </section>


              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Opportunities
                    </h2>

                    <p>
                      Deals connected to
                      this account.
                    </p>
                  </div>

                  <Link
                    href="/opportunities"
                    className="textButton"
                  >
                    View all
                  </Link>
                </div>


                {opportunities.length
                  === 0 ? (
                  <div className="miniEmptyState">
                    <Handshake
                      size={21}
                    />

                    <strong>
                      No opportunities
                    </strong>

                    <span>
                      No deals are linked
                      to this account yet.
                    </span>
                  </div>
                ) : (
                  <div className="contactList">
                    {opportunities
                      .slice(
                        0,
                        5
                      )
                      .map(
                        (
                          opportunity
                        ) => (
                          <Link
                            key={
                              opportunity.id
                            }
                            href={
                              `/opportunities/${opportunity.id}`
                            }
                            className="contactCard"
                            style={{
                              textDecoration:
                                "none",

                              color:
                                "inherit",
                            }}
                          >
                            <div className="contactAvatar">
                              <Handshake
                                size={15}
                              />
                            </div>

                            <div className="contactCardBody">
                              <div className="contactName">
                                <strong>
                                  {
                                    opportunity.name
                                  }
                                </strong>
                              </div>

                              <p>
                                {formatMoney(
                                  opportunity.amount,
                                  opportunity.currency
                                )}
                                {" · "}
                                {
                                  opportunity.probability
                                }
                                % probability
                              </p>

                              <span
                                style={{
                                  color:
                                    "var(--muted)",

                                  fontSize:
                                    "9px",
                                }}
                              >
                                Expected close:{" "}
                                {formatDate(
                                  opportunity
                                    .expected_close_date
                                )}
                              </span>
                            </div>
                          </Link>
                        )
                      )}
                  </div>
                )}
              </section>


              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Tasks
                    </h2>

                    <p>
                      Open work associated
                      with this account.
                    </p>
                  </div>

                  <Link
                    href="/tasks"
                    className="textButton"
                  >
                    View all
                  </Link>
                </div>


                {openTasks.length
                  === 0 ? (
                  <div className="miniEmptyState">
                    <CheckSquare2
                      size={21}
                    />

                    <strong>
                      No open tasks
                    </strong>

                    <span>
                      Nothing is currently
                      pending for this
                      account.
                    </span>
                  </div>
                ) : (
                  <div className="contactList">
                    {openTasks
                      .slice(
                        0,
                        5
                      )
                      .map(
                        (
                          task
                        ) => (
                          <div
                            key={
                              task.id
                            }
                            className="contactCard"
                          >
                            <div className="contactAvatar">
                              <CheckSquare2
                                size={15}
                              />
                            </div>

                            <div className="contactCardBody">
                              <div className="contactName">
                                <strong>
                                  {
                                    task.title
                                  }
                                </strong>

                                <span>
                                  {
                                    displayLabel(
                                      task.priority
                                    )
                                  }
                                </span>
                              </div>

                              <p>
                                {displayLabel(
                                  task.task_type
                                )}
                                {" · "}
                                {displayLabel(
                                  task.status
                                )}
                              </p>

                              <span
                                style={{
                                  color:
                                    "var(--muted)",

                                  fontSize:
                                    "9px",
                                }}
                              >
                                Due:{" "}
                                {formatDate(
                                  task.due_at
                                )}
                              </span>
                            </div>
                          </div>
                        )
                      )}
                  </div>
                )}
              </section>


              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Unified Relationship Timeline
                    </h2>

                    <p>
                      Activities, email
                      conversations, tasks
                      and opportunity changes
                      in one view.
                    </p>
                  </div>

                  <button
                    type="button"
                    className="textButton"
                    onClick={() =>
                      setActivityModalOpen(
                        true
                      )
                    }
                  >
                    + Log activity
                  </button>
                </div>


                {unifiedTimeline.length
                  === 0 ? (
                  <div className="miniEmptyState">
                    <ActivityIcon
                      size={21}
                    />

                    <strong>
                      No relationship history
                    </strong>

                    <span>
                      Account interactions
                      will appear here.
                    </span>
                  </div>
                ) : (
                  <div className="timeline">
                    {unifiedTimeline.map(
                      (
                        item
                      ) => (
                        <div
                          className="timelineItem"
                          key={
                            item.id
                          }
                        >
                          <div className="timelineMarker">
                            <TimelineIcon
                              kind={
                                item.kind
                              }
                            />
                          </div>

                          <div className="timelineContent">
                            <div className="timelineTop">
                              <div>
                                {item.href ? (
                                  <Link
                                    href={
                                      item.href
                                    }
                                  >
                                    <strong>
                                      {
                                        item.title
                                      }
                                    </strong>
                                  </Link>
                                ) : (
                                  <strong>
                                    {
                                      item.title
                                    }
                                  </strong>
                                )}

                                <span className="activityTypeLabel">
                                  {
                                    item.subtitle
                                  }
                                </span>
                              </div>

                              <time>
                                {formatDateTime(
                                  item.occurredAt
                                )}
                              </time>
                            </div>

                            {item.description && (
                              <p>
                                {
                                  item.description
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


              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Account Description
                    </h2>

                    <p>
                      Company background
                      and relationship notes.
                    </p>
                  </div>
                </div>


                {account.description ? (
                  <p
                    style={{
                      margin:
                        0,

                      color:
                        "var(--muted)",

                      fontSize:
                        "11px",

                      lineHeight:
                        1.7,

                      whiteSpace:
                        "pre-wrap",
                    }}
                  >
                    {
                      account.description
                    }
                  </p>
                ) : (
                  <div className="miniEmptyState compact">
                    <Building2
                      size={20}
                    />

                    <strong>
                      No description
                    </strong>

                    <span>
                      Edit the account
                      to add company
                      context.
                    </span>
                  </div>
                )}
              </section>
            </div>


            <aside className="accountSideColumn">
              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Company Details
                    </h2>

                    <p>
                      Core account
                      information.
                    </p>
                  </div>

                  <button
                    type="button"
                    className="textButton"
                    onClick={() =>
                      setEditModalOpen(
                        true
                      )
                    }
                  >
                    Edit
                  </button>
                </div>


                <div className="detailFields">
                  <div>
                    <span>
                      Industry
                    </span>

                    <strong>
                      {account.industry
                        || "—"}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Lifecycle
                    </span>

                    <strong>
                      {displayLabel(
                        account
                          .lifecycle_stage
                      )}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Annual Revenue
                    </span>

                    <strong>
                      {formatMoney(
                        account
                          .annual_revenue
                      )}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Employees
                    </span>

                    <strong>
                      {account.employee_count
                        ?? "—"}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Phone
                    </span>

                    <strong>
                      {account.phone
                        || "—"}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Owner
                    </span>

                    <strong>
                      {account.owner_user_id
                        ? "Assigned"
                        : "Unassigned"}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Website
                    </span>

                    {account.website ? (
                      <a
                        href={
                          account.website
                        }
                        target="_blank"
                        rel="noreferrer"
                      >
                        {
                          account.website
                        }

                        <ExternalLink
                          size={12}
                        />
                      </a>
                    ) : (
                      <strong>
                        —
                      </strong>
                    )}
                  </div>
                </div>
              </section>


              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Billing Address
                    </h2>

                    <p>
                      Primary billing
                      location.
                    </p>
                  </div>
                </div>


                {billingAddress ? (
                  <div
                    style={{
                      display:
                        "flex",

                      gap:
                        "9px",

                      alignItems:
                        "flex-start",

                      color:
                        "var(--muted)",

                      fontSize:
                        "10px",

                      lineHeight:
                        1.6,
                    }}
                  >
                    <MapPin
                      size={16}
                    />

                    <span>
                      {
                        billingAddress
                      }
                    </span>
                  </div>
                ) : (
                  <div className="miniEmptyState compact">
                    <MapPin
                      size={20}
                    />

                    <strong>
                      No billing address
                    </strong>

                    <span>
                      Add it from
                      Edit Account.
                    </span>
                  </div>
                )}
              </section>


              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Contacts
                    </h2>

                    <p>
                      People related to
                      this account.
                    </p>
                  </div>

                  <button
                    type="button"
                    className="textButton"
                    onClick={() =>
                      setContactModalOpen(
                        true
                      )
                    }
                  >
                    + Add
                  </button>
                </div>


                {contacts.length
                  === 0 ? (
                  <div className="miniEmptyState compact">
                    <Users
                      size={20}
                    />

                    <strong>
                      No contacts
                    </strong>

                    <span>
                      Add the first
                      contact.
                    </span>
                  </div>
                ) : (
                  <div className="contactList">
                    {contacts.map(
                      (
                        contact
                      ) => (
                        <div
                          className="contactCard"
                          key={
                            contact.id
                          }
                        >
                          <div className="contactAvatar">
                            {contact
                              .first_name
                              .charAt(0)
                              .toUpperCase()}

                            {contact
                              .last_name
                              .charAt(0)
                              .toUpperCase()}
                          </div>

                          <div className="contactCardBody">
                            <div className="contactName">
                              <strong>
                                {
                                  contact.first_name
                                }{" "}
                                {
                                  contact.last_name
                                }
                              </strong>

                              {contact.is_primary && (
                                <span>
                                  Primary
                                </span>
                              )}
                            </div>

                            <p>
                              {contact.job_title
                                || "Contact"}
                            </p>

                            {contact.email && (
                              <a
                                href={
                                  `mailto:${contact.email}`
                                }
                              >
                                {
                                  contact.email
                                }
                              </a>
                            )}
                          </div>
                        </div>
                      )
                    )}
                  </div>
                )}
              </section>


              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Health Signals
                    </h2>

                    <p>
                      Factors affecting
                      account health.
                    </p>
                  </div>
                </div>


                <div className="detailFields">
                  <div>
                    <span>
                      Relationship
                    </span>

                    <strong>
                      {
                        accountHealth.label
                      }
                    </strong>
                  </div>

                  <div>
                    <span>
                      Primary contact
                    </span>

                    <strong>
                      {primaryContact
                        ? `${primaryContact.first_name} ${primaryContact.last_name}`
                        : "Not set"}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Last interaction
                    </span>

                    <strong>
                      {daysSinceInteraction
                        === null
                        ? "No activity"
                        : daysSinceInteraction
                          === 0
                          ? "Today"
                          : `${daysSinceInteraction} days ago`}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Overdue tasks
                    </span>

                    <strong>
                      {
                        overdueTasks.length
                      }
                    </strong>
                  </div>

                  <div>
                    <span>
                      Email conversations
                    </span>

                    <strong>
                      {
                        emailThreads.length
                      }
                    </strong>
                  </div>
                </div>
              </section>


              <section className="detailPanel aiAccountCard">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      AIVA Account Summary
                    </h2>

                    <p>
                      AI relationship
                      intelligence.
                    </p>
                  </div>
                </div>

                <div className="aivaPlaceholder">
                  <span>
                    <Sparkles
                      size={12}
                    />

                    AI COMING NEXT
                  </span>

                  <strong>
                    Customer intelligence
                    will appear here.
                  </strong>

                  <p>
                    The Account 360 data
                    foundation is ready for
                    AI summarization, risk
                    detection and next-best-
                    action recommendations.
                  </p>
                </div>
              </section>
            </aside>
          </div>
        </div>
      </main>


      <EditAccountModal
        account={
          account
        }
        open={
          editModalOpen
        }
        onClose={() =>
          setEditModalOpen(
            false
          )
        }
        onUpdated={(
          updatedAccount
        ) =>
          setAccount(
            updatedAccount
          )
        }
      />


      <CreateContactModal
        accountId={
          account.id
        }
        open={
          contactModalOpen
        }
        onClose={() =>
          setContactModalOpen(
            false
          )
        }
        onCreated={(
          contact
        ) =>
          setContacts(
            (current) => [
              ...current,
              contact,
            ]
          )
        }
      />


      <LogActivityModal
        accountId={
          account.id
        }
        open={
          activityModalOpen
        }
        onClose={() =>
          setActivityModalOpen(
            false
          )
        }
        onCreated={(
          activity
        ) =>
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