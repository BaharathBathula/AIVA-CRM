"use client";

import {
  useEffect,
  useState,
} from "react";

import Link from "next/link";

import {
  Activity as ActivityIcon,
  ArrowLeft,
  Building2,
  BriefcaseBusiness,
  CalendarDays,
  ExternalLink,
  FileText,
  Linkedin,
  Mail,
  MessageSquare,
  Phone,
  Pencil,
  Smartphone,
  Star,
  UserCheck,
  UserRound,
} from "lucide-react";

import {
  EditContactModal,
} from "@/components/contacts/edit-contact-modal";

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
  getContactActivities,
} from "@/lib/activities";

import {
  getContact,
} from "@/lib/contacts";

import {
  getContactEmailThreads,
} from "@/lib/emails";

import {
  getTasks,
} from "@/lib/tasks";

import {
  getOpportunities,
} from "@/lib/opportunities";

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
  Task,
} from "@/types/task";

import type {
  Opportunity,
} from "@/types/opportunity";

import {
  useParams,
} from "next/navigation";


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
    new Date(
      value
    )
  );
}


function initials(
  contact: Contact
) {
  return (
    `${
      contact.first_name[0]
      ?? ""
    }${
      contact.last_name[0]
      ?? ""
    }`
      .toUpperCase()
  );
}


function ActivityTypeIcon({
  type,
}: {
  type:
    Activity["activity_type"];
}) {
  if (
    type === "email"
  ) {
    return (
      <Mail
        size={16}
      />
    );
  }

  if (
    type === "call"
  ) {
    return (
      <Phone
        size={16}
      />
    );
  }

  if (
    type === "meeting"
  ) {
    return (
      <CalendarDays
        size={16}
      />
    );
  }

  if (
    type === "document"
  ) {
    return (
      <FileText
        size={16}
      />
    );
  }

  if (
    type === "note"
  ) {
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


export default function ContactDetailPage() {
  const params =
    useParams<{
      contactId: string;
    }>();

  const contactId =
    params.contactId;


  const [
    contact,
    setContact,
  ] = useState<Contact | null>(
    null
  );

  const [
    account,
    setAccount,
  ] = useState<Account | null>(
    null
  );

  const [
    activities,
    setActivities,
  ] = useState<Activity[]>(
    []
  );


  const [
    emailThreads,
    setEmailThreads,
  ] = useState<EmailThread[]>(
    []
  );


  const [
    tasks,
    setTasks,
  ] = useState<Task[]>(
    []
  );


  const [
    opportunities,
    setOpportunities,
  ] = useState<Opportunity[]>(
    []
  );

  const [
    editOpen,
    setEditOpen,
  ] = useState(false);


  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    error,
    setError,
  ] = useState<
    string | null
  >(null);


  useEffect(() => {
    if (!contactId) {
      return;
    }

    let cancelled =
      false;

    async function load() {
      try {
        setLoading(true);
        setError(null);

        const contactResult =
          await getContact(
            contactId
          );

        if (cancelled) {
          return;
        }

        setContact(
          contactResult
        );

        const [
          activityResult,
          accountResult,
          emailThreadsResult,
          taskResult,
          opportunityResult,
        ] = await Promise.all([
          getContactActivities(
            contactId,
            0,
            100
          ),

          contactResult.account_id
            ? getAccount(
                contactResult.account_id
              )
            : Promise.resolve(
                null
              ),

          getContactEmailThreads(
            contactId,
            100
          ),

          getTasks({
            contactId,
            limit: 100,
          }),

          getOpportunities({
            primaryContactId:
              contactId,
          }),
        ]);

        if (cancelled) {
          return;
        }

        setActivities(
          activityResult
        );

        setAccount(
          accountResult
        );

        setEmailThreads(
          emailThreadsResult
        );


        setTasks(
          taskResult
        );


        setOpportunities(
          opportunityResult
        );
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "Unable to load contact."
          );
        }
      } finally {
        if (!cancelled) {
          setLoading(
            false
          );
        }
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, [
    contactId,
  ]);


  async function handleContactUpdated(
    updatedContact: Contact
  ) {
    setContact(
      updatedContact
    );

    if (
      updatedContact.account_id
    ) {
      try {
        const updatedAccount =
          await getAccount(
            updatedContact.account_id
          );

        setAccount(
          updatedAccount
        );
      } catch {
        setAccount(
          null
        );
      }
    } else {
      setAccount(
        null
      );
    }
  }


  if (loading) {
    return (
      <div className="appShell">
        <Sidebar
          active="Contacts"
        />

        <main className="mainArea">
          <Topbar />

          <div className="pageContent">
            <div className="detailLoading">
              Loading contact...
            </div>
          </div>
        </main>
      </div>
    );
  }


  if (
    error
    ||
    !contact
  ) {
    return (
      <div className="appShell">
        <Sidebar
          active="Contacts"
        />

        <main className="mainArea">
          <Topbar />

          <div className="pageContent">
            <Link
              href="/contacts"
              className="backLink"
            >
              <ArrowLeft
                size={15}
              />

              Contacts
            </Link>

            <div className="detailError">
              <strong>
                Contact unavailable
              </strong>

              <p>
                {
                  error
                  ?? "Contact not found."
                }
              </p>
            </div>
          </div>
        </main>
      </div>
    );
  }


  const opportunityValue =
    opportunities.reduce(
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


  const averageOpportunityProbability =
    opportunities.length
      ? Math.round(
          opportunities.reduce(
            (
              total,
              opportunity
            ) =>
              total
              +
              opportunity.probability,
            0
          )
          /
          opportunities.length
        )
      : 0;


  const openTaskCount =
    tasks.filter(
      (
        task
      ) =>
        task.status === "open"
        ||
        task.status === "in_progress"
    ).length;


  const completedTaskCount =
    tasks.filter(
      (
        task
      ) =>
        task.status === "completed"
    ).length;


  const overdueTaskCount =
    tasks.filter(
      (
        task
      ) => {
        if (
          !task.due_at
          ||
          task.status === "completed"
          ||
          task.status === "cancelled"
        ) {
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
    ).length;


  const interactionDates =
    [
      ...activities.map(
        (
          activity
        ) =>
          activity.occurred_at
      ),

      ...emailThreads
        .map(
          (
            thread
          ) =>
            thread.last_message_at
        )
        .filter(
          (
            value
          ): value is string =>
            Boolean(value)
        ),
    ]
      .map(
        (
          value
        ) =>
          new Date(
            value
          ).getTime()
      )
      .filter(
        (
          value
        ) =>
          Number.isFinite(
            value
          )
      );


  const latestInteractionTime =
    interactionDates.length
      ? Math.max(
          ...interactionDates
        )
      : null;


  const lastInteraction =
    latestInteractionTime
      ? new Date(
          latestInteractionTime
        )
      : null;


  const thirtyDaysAgo =
    Date.now()
    -
    30
    *
    24
    *
    60
    *
    60
    *
    1000;


  const recentTouchpoints =
    activities.filter(
      (
        activity
      ) =>
        new Date(
          activity.occurred_at
        ).getTime()
        >=
        thirtyDaysAgo
    ).length
    +
    emailThreads.filter(
      (
        thread
      ) =>
        Boolean(
          thread.last_message_at
        )
        &&
        new Date(
          thread.last_message_at!
        ).getTime()
        >=
        thirtyDaysAgo
    ).length;


  const daysSinceLastInteraction =
    lastInteraction
      ? Math.max(
          0,
          Math.floor(
            (
              Date.now()
              -
              lastInteraction.getTime()
            )
            /
            (
              24
              *
              60
              *
              60
              *
              1000
            )
          )
        )
      : null;


  let engagementScore = 0;

  if (
    daysSinceLastInteraction
    !== null
  ) {
    if (
      daysSinceLastInteraction
      <= 7
    ) {
      engagementScore += 40;
    } else if (
      daysSinceLastInteraction
      <= 30
    ) {
      engagementScore += 30;
    } else if (
      daysSinceLastInteraction
      <= 60
    ) {
      engagementScore += 20;
    } else if (
      daysSinceLastInteraction
      <= 90
    ) {
      engagementScore += 10;
    }
  }

  engagementScore += Math.min(
    recentTouchpoints * 5,
    30
  );

  engagementScore += Math.min(
    emailThreads.length * 5,
    15
  );

  if (
    opportunities.length > 0
  ) {
    engagementScore += 10;
  }

  if (
    contact.is_active
  ) {
    engagementScore += 5;
  }

  engagementScore = Math.min(
    engagementScore,
    100
  );


  const engagementLabel =
    engagementScore >= 70
      ? "Engaged"
      : engagementScore >= 40
        ? "Moderate"
        : "Low";


  const relationshipTimeline = [
    ...activities.map(
      (
        activity
      ) => ({
        id:
          `activity-${activity.id}`,

        kind:
          "activity",

        timestamp:
          activity.occurred_at,

        title:
          activity.subject,

        body:
          activity.body,

        meta:
          `${activity.activity_type}${
            activity.direction
              ? ` · ${activity.direction}`
              : ""
          }`,
      })
    ),

    ...emailThreads
      .filter(
        (
          thread
        ) =>
          Boolean(
            thread.last_message_at
          )
      )
      .map(
        (
          thread
        ) => ({
          id:
            `email-${thread.id}`,

          kind:
            "email",

          timestamp:
            thread.last_message_at!,

          title:
            thread.subject,

          body:
            thread.snippet,

          meta:
            "email conversation",
        })
      ),

    ...tasks
      .filter(
        (
          task
        ) =>
          task.status
            === "completed"
          &&
          Boolean(
            task.completed_at
          )
      )
      .map(
        (
          task
        ) => ({
          id:
            `task-${task.id}`,

          kind:
            "task",

          timestamp:
            task.completed_at!,

          title:
            `Task completed: ${task.title}`,

          body:
            task.description,

          meta:
            `${task.task_type} · ${task.priority}`,
        })
      ),

    ...opportunities.map(
      (
        opportunity
      ) => ({
        id:
          `opportunity-${opportunity.id}`,

        kind:
          "opportunity",

        timestamp:
          opportunity.created_at,

        title:
          `Opportunity: ${opportunity.name}`,

        body:
          opportunity.description,

        meta:
          `${opportunity.probability}% probability`,
      })
    ),
  ].sort(
    (
      a,
      b
    ) =>
      new Date(
        b.timestamp
      ).getTime()
      -
      new Date(
        a.timestamp
      ).getTime()
  );


  const fullName =
    `${
      contact.first_name
    } ${
      contact.last_name
    }`;


  return (
    <div className="appShell">
      <Sidebar
        active="Contacts"
      />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <Link
            href="/contacts"
            className="backLink"
          >
            <ArrowLeft
              size={15}
            />

            Back to Contacts
          </Link>


          <section className="accountHero">
            <div className="accountHeroIdentity">
              <div className="accountHeroLogo">
                {
                  initials(
                    contact
                  )
                }
              </div>

              <div>
                <div className="accountHeroTitle">
                  <h1>
                    {
                      fullName
                    }
                  </h1>

                  {contact.is_primary && (
                    <span className="stageBadge stage-customer">
                      Primary
                    </span>
                  )}

                  <span
                    className={
                      contact.is_active
                        ? "stageBadge stage-customer"
                        : "stageBadge stage-inactive"
                    }
                  >
                    {
                      contact.is_active
                        ? "Active"
                        : "Inactive"
                    }
                  </span>
                </div>

                <div className="accountHeroMeta">
                  {contact.job_title && (
                    <span>
                      <BriefcaseBusiness
                        size={14}
                      />

                      {
                        contact.job_title
                      }
                    </span>
                  )}

                  {contact.email && (
                    <span>
                      <Mail
                        size={14}
                      />

                      {
                        contact.email
                      }
                    </span>
                  )}

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
                </div>
              </div>
            </div>

            <div className="detailActions">
              <button
                type="button"
                className="secondaryButton"
                onClick={() =>
                  setEditOpen(
                    true
                  )
                }
              >
                <Pencil
                  size={15}
                />

                Edit Contact
              </button>
            </div>
          </section>


          <section
            style={{
              display: "grid",
              gridTemplateColumns:
                "repeat(auto-fit, minmax(170px, 1fr))",
              gap: "12px",
              marginBottom: "15px",
            }}
          >
            <div
              className="detailPanel"
              style={{
                padding: "16px",
              }}
            >
              <span
                style={{
                  display: "block",
                  color: "var(--muted)",
                  fontSize: "9px",
                  marginBottom: "6px",
                }}
              >
                Last Interaction
              </span>

              <strong
                style={{
                  display: "block",
                  fontSize: "12px",
                }}
              >
                {
                  lastInteraction
                    ? formatDate(
                        lastInteraction.toISOString()
                      )
                    : "No interactions"
                }
              </strong>

              {daysSinceLastInteraction
                !== null && (
                <span
                  style={{
                    display: "block",
                    marginTop: "4px",
                    color: "var(--muted)",
                    fontSize: "8px",
                  }}
                >
                  {
                    daysSinceLastInteraction
                  }{" "}
                  day{
                    daysSinceLastInteraction
                    === 1
                      ? ""
                      : "s"
                  } ago
                </span>
              )}
            </div>


            <div
              className="detailPanel"
              style={{
                padding: "16px",
              }}
            >
              <span
                style={{
                  display: "block",
                  color: "var(--muted)",
                  fontSize: "9px",
                  marginBottom: "6px",
                }}
              >
                Recent Touchpoints
              </span>

              <strong
                style={{
                  display: "block",
                  fontSize: "20px",
                }}
              >
                {
                  recentTouchpoints
                }
              </strong>

              <span
                style={{
                  display: "block",
                  marginTop: "4px",
                  color: "var(--muted)",
                  fontSize: "8px",
                }}
              >
                Last 30 days
              </span>
            </div>


            <div
              className="detailPanel"
              style={{
                padding: "16px",
              }}
            >
              <span
                style={{
                  display: "block",
                  color: "var(--muted)",
                  fontSize: "9px",
                  marginBottom: "6px",
                }}
              >
                Email Conversations
              </span>

              <strong
                style={{
                  display: "block",
                  fontSize: "20px",
                }}
              >
                {
                  emailThreads.length
                }
              </strong>

              <span
                style={{
                  display: "block",
                  marginTop: "4px",
                  color: "var(--muted)",
                  fontSize: "8px",
                }}
              >
                Contact-linked threads
              </span>
            </div>


            <div
              className="detailPanel"
              style={{
                padding: "16px",
              }}
            >
              <span
                style={{
                  display: "block",
                  color: "var(--muted)",
                  fontSize: "9px",
                  marginBottom: "6px",
                }}
              >
                Engagement Score
              </span>

              <strong
                style={{
                  display: "block",
                  fontSize: "20px",
                }}
              >
                {
                  engagementScore
                }/100
              </strong>

              <span
                style={{
                  display: "inline-flex",
                  marginTop: "5px",
                  padding: "3px 7px",
                  borderRadius: "999px",
                  background:
                    engagementScore >= 70
                      ? "#e6f7ef"
                      : engagementScore >= 40
                        ? "#fff6dd"
                        : "#f7ecee",
                  color:
                    engagementScore >= 70
                      ? "#197757"
                      : engagementScore >= 40
                        ? "#8a6700"
                        : "#a94d57",
                  fontSize: "8px",
                  fontWeight: 700,
                }}
              >
                {
                  engagementLabel
                }
              </span>
            </div>
          </section>


          <div className="accountDetailGrid">
            <div className="accountMainColumn">

              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Email Communications
                    </h2>

                    <p>
                      Email conversations linked
                      directly to this contact.
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
                  style={{
                    display: "grid",
                    gridTemplateColumns:
                      "repeat(2, minmax(0, 1fr))",
                    gap: "10px",
                    marginBottom: "14px",
                  }}
                >
                  <div
                    style={{
                      padding: "12px",
                      border:
                        "1px solid var(--border)",
                      borderRadius: "10px",
                    }}
                  >
                    <span
                      style={{
                        display: "block",
                        color: "var(--muted)",
                        fontSize: "9px",
                        marginBottom: "4px",
                      }}
                    >
                      Conversations
                    </span>

                    <strong>
                      {
                        emailThreads.length
                      }
                    </strong>
                  </div>

                  <div
                    style={{
                      padding: "12px",
                      border:
                        "1px solid var(--border)",
                      borderRadius: "10px",
                    }}
                  >
                    <span
                      style={{
                        display: "block",
                        color: "var(--muted)",
                        fontSize: "9px",
                        marginBottom: "4px",
                      }}
                    >
                      Latest Email
                    </span>

                    <strong
                      style={{
                        fontSize: "10px",
                      }}
                    >
                      {
                        emailThreads.length > 0
                        &&
                        emailThreads[0]
                          .last_message_at
                          ? formatDate(
                              emailThreads[0]
                                .last_message_at
                            )
                          : "—"
                      }
                    </strong>
                  </div>
                </div>


                {emailThreads.length
                  === 0 ? (
                  <div className="miniEmptyState compact">
                    <Mail
                      size={20}
                    />

                    <strong>
                      No email conversations
                    </strong>

                    <span>
                      Emails linked to this
                      contact will appear here.
                    </span>
                  </div>
                ) : (
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: "8px",
                    }}
                  >
                    {emailThreads.map(
                      (
                        thread
                      ) => (
                        <div
                          key={
                            thread.id
                          }
                          style={{
                            padding:
                              "12px",
                            border:
                              "1px solid #eceef3",
                            borderRadius:
                              "10px",
                            background:
                              "#fdfdfe",
                          }}
                        >
                          <div
                            style={{
                              display:
                                "flex",
                              justifyContent:
                                "space-between",
                              alignItems:
                                "flex-start",
                              gap:
                                "12px",
                            }}
                          >
                            <div
                              style={{
                                minWidth:
                                  0,
                              }}
                            >
                              <strong
                                style={{
                                  display:
                                    "block",
                                  fontSize:
                                    "10px",
                                }}
                              >
                                {
                                  thread.subject
                                }
                              </strong>

                              {thread.snippet && (
                                <p
                                  style={{
                                    margin:
                                      "5px 0 0",
                                    color:
                                      "var(--muted)",
                                    fontSize:
                                      "9px",
                                    lineHeight:
                                      1.5,
                                  }}
                                >
                                  {
                                    thread.snippet
                                  }
                                </p>
                              )}
                            </div>

                            <time
                              style={{
                                flex:
                                  "0 0 auto",
                                color:
                                  "var(--muted)",
                                fontSize:
                                  "8px",
                              }}
                            >
                              {
                                thread.last_message_at
                                  ? formatDate(
                                      thread.last_message_at
                                    )
                                  : "—"
                              }
                            </time>
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
                      Contact Opportunities
                    </h2>

                    <p>
                      Sales opportunities
                      where this person is
                      the primary contact.
                    </p>
                  </div>

                  <Link
                    href="/opportunities"
                    className="textButton"
                  >
                    Open Opportunities
                  </Link>
                </div>


                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns:
                      "repeat(3, minmax(0, 1fr))",
                    gap: "10px",
                    marginBottom: "14px",
                  }}
                >
                  <div
                    style={{
                      padding: "12px",
                      border:
                        "1px solid var(--border)",
                      borderRadius: "10px",
                    }}
                  >
                    <span
                      style={{
                        display: "block",
                        color: "var(--muted)",
                        fontSize: "9px",
                        marginBottom: "4px",
                      }}
                    >
                      Opportunities
                    </span>

                    <strong>
                      {
                        opportunities.length
                      }
                    </strong>
                  </div>


                  <div
                    style={{
                      padding: "12px",
                      border:
                        "1px solid var(--border)",
                      borderRadius: "10px",
                    }}
                  >
                    <span
                      style={{
                        display: "block",
                        color: "var(--muted)",
                        fontSize: "9px",
                        marginBottom: "4px",
                      }}
                    >
                      Pipeline Value
                    </span>

                    <strong>
                      {
                        new Intl.NumberFormat(
                          "en-US",
                          {
                            style:
                              "currency",
                            currency:
                              "USD",
                            notation:
                              "compact",
                            maximumFractionDigits:
                              1,
                          }
                        ).format(
                          opportunityValue
                        )
                      }
                    </strong>
                  </div>


                  <div
                    style={{
                      padding: "12px",
                      border:
                        "1px solid var(--border)",
                      borderRadius: "10px",
                    }}
                  >
                    <span
                      style={{
                        display: "block",
                        color: "var(--muted)",
                        fontSize: "9px",
                        marginBottom: "4px",
                      }}
                    >
                      Avg Probability
                    </span>

                    <strong>
                      {
                        averageOpportunityProbability
                      }%
                    </strong>
                  </div>
                </div>


                {opportunities.length
                  === 0 ? (
                  <div className="miniEmptyState compact">
                    <BriefcaseBusiness
                      size={20}
                    />

                    <strong>
                      No opportunities
                    </strong>

                    <span>
                      Opportunities linked
                      to this contact will
                      appear here.
                    </span>
                  </div>
                ) : (
                  <div
                    style={{
                      display: "flex",
                      flexDirection:
                        "column",
                      gap: "8px",
                    }}
                  >
                    {opportunities.map(
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
                          style={{
                            display:
                              "flex",
                            justifyContent:
                              "space-between",
                            alignItems:
                              "flex-start",
                            gap:
                              "14px",
                            padding:
                              "12px",
                            border:
                              "1px solid #eceef3",
                            borderRadius:
                              "10px",
                            background:
                              "#fdfdfe",
                            color:
                              "inherit",
                            textDecoration:
                              "none",
                          }}
                        >
                          <div>
                            <strong
                              style={{
                                display:
                                  "block",
                                fontSize:
                                  "10px",
                              }}
                            >
                              {
                                opportunity.name
                              }
                            </strong>

                            <span
                              style={{
                                display:
                                  "block",
                                marginTop:
                                  "5px",
                                color:
                                  "var(--muted)",
                                fontSize:
                                  "9px",
                              }}
                            >
                              Probability:{" "}
                              {
                                opportunity.probability
                              }%
                            </span>

                            {opportunity.expected_close_date && (
                              <span
                                style={{
                                  display:
                                    "block",
                                  marginTop:
                                    "3px",
                                  color:
                                    "var(--muted)",
                                  fontSize:
                                    "9px",
                                }}
                              >
                                Expected close:{" "}
                                {
                                  new Intl.DateTimeFormat(
                                    "en-US",
                                    {
                                      month:
                                        "short",
                                      day:
                                        "numeric",
                                      year:
                                        "numeric",
                                    }
                                  ).format(
                                    new Date(
                                      opportunity.expected_close_date
                                    )
                                  )
                                }
                              </span>
                            )}
                          </div>


                          <strong
                            style={{
                              flex:
                                "0 0 auto",
                              fontSize:
                                "10px",
                            }}
                          >
                            {
                              opportunity.amount
                                ? new Intl.NumberFormat(
                                    "en-US",
                                    {
                                      style:
                                        "currency",
                                      currency:
                                        opportunity.currency
                                        || "USD",
                                      notation:
                                        "compact",
                                      maximumFractionDigits:
                                        1,
                                    }
                                  ).format(
                                    Number(
                                      opportunity.amount
                                    )
                                  )
                                : "—"
                            }
                          </strong>
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
                      Contact Tasks
                    </h2>

                    <p>
                      Follow-ups and work
                      linked directly to
                      this contact.
                    </p>
                  </div>

                  <Link
                    href="/tasks"
                    className="textButton"
                  >
                    Open Tasks
                  </Link>
                </div>


                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns:
                      "repeat(3, minmax(0, 1fr))",
                    gap: "10px",
                    marginBottom: "14px",
                  }}
                >
                  <div
                    style={{
                      padding: "12px",
                      border:
                        "1px solid var(--border)",
                      borderRadius: "10px",
                    }}
                  >
                    <span
                      style={{
                        display: "block",
                        color: "var(--muted)",
                        fontSize: "9px",
                        marginBottom: "4px",
                      }}
                    >
                      Open
                    </span>

                    <strong>
                      {
                        openTaskCount
                      }
                    </strong>
                  </div>


                  <div
                    style={{
                      padding: "12px",
                      border:
                        "1px solid var(--border)",
                      borderRadius: "10px",
                    }}
                  >
                    <span
                      style={{
                        display: "block",
                        color: "var(--muted)",
                        fontSize: "9px",
                        marginBottom: "4px",
                      }}
                    >
                      Overdue
                    </span>

                    <strong>
                      {
                        overdueTaskCount
                      }
                    </strong>
                  </div>


                  <div
                    style={{
                      padding: "12px",
                      border:
                        "1px solid var(--border)",
                      borderRadius: "10px",
                    }}
                  >
                    <span
                      style={{
                        display: "block",
                        color: "var(--muted)",
                        fontSize: "9px",
                        marginBottom: "4px",
                      }}
                    >
                      Completed
                    </span>

                    <strong>
                      {
                        completedTaskCount
                      }
                    </strong>
                  </div>
                </div>


                {tasks.length === 0 ? (
                  <div className="miniEmptyState compact">
                    <UserCheck
                      size={20}
                    />

                    <strong>
                      No tasks
                    </strong>

                    <span>
                      Tasks linked to this
                      contact will appear here.
                    </span>
                  </div>
                ) : (
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: "8px",
                    }}
                  >
                    {tasks.map(
                      (
                        task
                      ) => {
                        const overdue =
                          task.due_at !== null
                          &&
                          task.status
                            !== "completed"
                          &&
                          task.status
                            !== "cancelled"
                          &&
                          new Date(
                            task.due_at
                          ).getTime()
                            <
                            Date.now();

                        return (
                          <div
                            key={
                              task.id
                            }
                            style={{
                              display: "flex",
                              alignItems: "flex-start",
                              justifyContent:
                                "space-between",
                              gap: "14px",
                              padding: "12px",
                              border:
                                "1px solid #eceef3",
                              borderRadius:
                                "10px",
                              background:
                                "#fdfdfe",
                            }}
                          >
                            <div
                              style={{
                                minWidth: 0,
                              }}
                            >
                              <strong
                                style={{
                                  display: "block",
                                  fontSize: "10px",
                                }}
                              >
                                {
                                  task.title
                                }
                              </strong>

                              <div
                                style={{
                                  display: "flex",
                                  flexWrap: "wrap",
                                  gap: "6px",
                                  marginTop: "6px",
                                }}
                              >
                                <span
                                  className={
                                    task.status
                                      === "completed"
                                      ? "stageBadge stage-customer"
                                      : task.status
                                          === "cancelled"
                                        ? "stageBadge stage-inactive"
                                        : "stageBadge stage-prospect"
                                  }
                                >
                                  {
                                    task.status
                                      .replaceAll(
                                        "_",
                                        " "
                                      )
                                  }
                                </span>

                                <span
                                  className={
                                    task.priority
                                      === "urgent"
                                      ||
                                      task.priority
                                        === "high"
                                      ? "stageBadge stage-inactive"
                                      : "stageBadge stage-prospect"
                                  }
                                >
                                  {
                                    task.priority
                                  }
                                </span>

                                {overdue && (
                                  <span className="stageBadge stage-inactive">
                                    Overdue
                                  </span>
                                )}
                              </div>

                              {task.description && (
                                <p
                                  style={{
                                    margin:
                                      "7px 0 0",
                                    color:
                                      "var(--muted)",
                                    fontSize:
                                      "9px",
                                    lineHeight:
                                      1.5,
                                  }}
                                >
                                  {
                                    task.description
                                  }
                                </p>
                              )}
                            </div>


                            <div
                              style={{
                                flex:
                                  "0 0 auto",
                                textAlign:
                                  "right",
                              }}
                            >
                              <span
                                style={{
                                  display:
                                    "block",
                                  color:
                                    "var(--muted)",
                                  fontSize:
                                    "8px",
                                }}
                              >
                                Due
                              </span>

                              <strong
                                style={{
                                  display:
                                    "block",
                                  marginTop:
                                    "3px",
                                  fontSize:
                                    "9px",
                                }}
                              >
                                {
                                  task.due_at
                                    ? formatDate(
                                        task.due_at
                                      )
                                    : "No due date"
                                }
                              </strong>
                            </div>
                          </div>
                        );
                      }
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
                      Communications, CRM activity,
                      completed tasks and sales
                      events in one chronological
                      view.
                    </p>
                  </div>
                </div>


                {relationshipTimeline.length
                  === 0 ? (
                  <div className="miniEmptyState">
                    <ActivityIcon
                      size={21}
                    />

                    <strong>
                      No relationship activity yet
                    </strong>

                    <span>
                      Contact interactions and CRM
                      events will appear here.
                    </span>
                  </div>
                ) : (
                  <div className="timeline">
                    {relationshipTimeline.map(
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
                            {
                              item.kind
                              === "email"
                                ? (
                                  <Mail
                                    size={16}
                                  />
                                )
                                : item.kind
                                  === "task"
                                  ? (
                                    <UserCheck
                                      size={16}
                                    />
                                  )
                                  : item.kind
                                    === "opportunity"
                                    ? (
                                      <BriefcaseBusiness
                                        size={16}
                                      />
                                    )
                                    : (
                                      <ActivityIcon
                                        size={16}
                                      />
                                    )
                            }
                          </div>

                          <div className="timelineContent">
                            <div className="timelineTop">
                              <div>
                                <strong>
                                  {
                                    item.title
                                  }
                                </strong>

                                <span className="activityTypeLabel">
                                  {
                                    item.meta
                                  }
                                </span>
                              </div>

                              <time>
                                {
                                  formatDate(
                                    item.timestamp
                                  )
                                }
                              </time>
                            </div>

                            {item.body && (
                              <p>
                                {
                                  item.body
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
            </div>


            <aside className="accountSideColumn">

              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Contact Details
                    </h2>

                    <p>
                      Personal and
                      professional information.
                    </p>
                  </div>
                </div>

                <div className="detailFields">
                  <div>
                    <span>
                      Full Name
                    </span>

                    <strong>
                      {
                        fullName
                      }
                    </strong>
                  </div>


                  <div>
                    <span>
                      Job Title
                    </span>

                    <strong>
                      {
                        contact.job_title
                        || "—"
                      }
                    </strong>
                  </div>


                  <div>
                    <span>
                      Department
                    </span>

                    <strong>
                      {
                        contact.department
                        || "—"
                      }
                    </strong>
                  </div>


                  <div>
                    <span>
                      Email
                    </span>

                    {contact.email ? (
                      <a
                        href={
                          `mailto:${contact.email}`
                        }
                      >
                        <Mail
                          size={12}
                        />

                        {
                          contact.email
                        }
                      </a>
                    ) : (
                      <strong>
                        —
                      </strong>
                    )}
                  </div>


                  <div>
                    <span>
                      Phone
                    </span>

                    {contact.phone ? (
                      <a
                        href={
                          `tel:${contact.phone}`
                        }
                      >
                        <Phone
                          size={12}
                        />

                        {
                          contact.phone
                        }
                      </a>
                    ) : (
                      <strong>
                        —
                      </strong>
                    )}
                  </div>


                  <div>
                    <span>
                      Mobile
                    </span>

                    {contact.mobile ? (
                      <a
                        href={
                          `tel:${contact.mobile}`
                        }
                      >
                        <Smartphone
                          size={12}
                        />

                        {
                          contact.mobile
                        }
                      </a>
                    ) : (
                      <strong>
                        —
                      </strong>
                    )}
                  </div>


                  <div>
                    <span>
                      LinkedIn
                    </span>

                    {contact.linkedin_url ? (
                      <a
                        href={
                          contact.linkedin_url
                        }
                        target="_blank"
                        rel="noreferrer"
                      >
                        <Linkedin
                          size={12}
                        />

                        Profile

                        <ExternalLink
                          size={11}
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
                      Account Relationship
                    </h2>

                    <p>
                      Company relationship
                      and contact role.
                    </p>
                  </div>
                </div>


                {account ? (
                  <div className="detailFields">
                    <div>
                      <span>
                        Account
                      </span>

                      <Link
                        href={
                          `/accounts/${account.id}`
                        }
                      >
                        <Building2
                          size={12}
                        />

                        {
                          account.name
                        }
                      </Link>
                    </div>


                    <div>
                      <span>
                        Relationship
                      </span>

                      <strong>
                        {
                          contact.is_primary
                            ? "Primary Contact"
                            : "Standard Contact"
                        }
                      </strong>
                    </div>


                    <div>
                      <span>
                        Contact Status
                      </span>

                      <strong>
                        {
                          contact.is_active
                            ? "Active"
                            : "Inactive"
                        }
                      </strong>
                    </div>
                  </div>
                ) : (
                  <div className="miniEmptyState compact">
                    <Building2
                      size={20}
                    />

                    <strong>
                      No account assigned
                    </strong>

                    <span>
                      This contact is not
                      currently linked to
                      an account.
                    </span>
                  </div>
                )}
              </section>


              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Relationship Summary
                    </h2>

                    <p>
                      Current CRM relationship
                      status.
                    </p>
                  </div>
                </div>

                <div className="detailFields">
                  <div>
                    <span>
                      Primary Contact
                    </span>

                    <strong>
                      {
                        contact.is_primary
                          ? "Yes"
                          : "No"
                      }
                    </strong>
                  </div>


                  <div>
                    <span>
                      Status
                    </span>

                    <strong>
                      {
                        contact.is_active
                          ? "Active"
                          : "Inactive"
                      }
                    </strong>
                  </div>


                  <div>
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
                    <span>
                      Last Interaction
                    </span>

                    <strong>
                      {
                        lastInteraction
                          ? formatDate(
                              lastInteraction
                                .toISOString()
                            )
                          : "—"
                      }
                    </strong>
                  </div>


                  <div>
                    <span>
                      Engagement
                    </span>

                    <strong>
                      {
                        engagementScore
                      }/100 · {
                        engagementLabel
                      }
                    </strong>
                  </div>


                  <div>
                    <span>
                      Account Linked
                    </span>

                    <strong>
                      {
                        contact.account_id
                          ? "Yes"
                          : "No"
                      }
                    </strong>
                  </div>
                </div>
              </section>


              <section className="detailPanel aiAccountCard">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      AIVA Contact Intelligence
                    </h2>

                    <p>
                      Relationship intelligence
                      and engagement signals.
                    </p>
                  </div>
                </div>

                <div className="aivaPlaceholder">
                  <span>
                    AI COMING LATER
                  </span>

                  <strong>
                    Contact intelligence
                    will appear here.
                  </strong>

                  <p>
                    AIVA will analyze
                    engagement, communication
                    patterns, relationship
                    strength and recommended
                    next actions.
                  </p>
                </div>
              </section>
            </aside>
          </div>
        </div>
      </main>
      <EditContactModal
        contact={
          contact
        }
        open={
          editOpen
        }
        onClose={() =>
          setEditOpen(
            false
          )
        }
        onUpdated={
          handleContactUpdated
        }
      />


    </div>
  );
}