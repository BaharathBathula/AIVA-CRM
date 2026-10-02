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
  CalendarDays,
  ExternalLink,
  FileText,
  Mail,
  MapPin,
  MessageSquare,
  Phone,
  Plus,
  Users,
} from "lucide-react";

import {
  useParams,
} from "next/navigation";

import {
  CreateContactModal,
} from "@/components/accounts/create-contact-modal";

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

import type {
  Account,
} from "@/types/account";

import type {
  Activity,
} from "@/types/activity";

import type {
  Contact,
} from "@/types/contact";


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


function formatRevenue(
  value: string | null
) {
  if (!value) {
    return "—";
  }

  return new Intl.NumberFormat(
    "en-US",
    {
      style: "currency",
      currency: "USD",
      notation: "compact",
      maximumFractionDigits: 1,
    }
  ).format(
    Number(value)
  );
}


function ActivityTypeIcon({
  type,
}: {
  type: Activity["activity_type"];
}) {
  if (type === "email") {
    return <Mail size={16} />;
  }

  if (type === "call") {
    return <Phone size={16} />;
  }

  if (type === "meeting") {
    return <CalendarDays size={16} />;
  }

  if (type === "document") {
    return <FileText size={16} />;
  }

  if (type === "note") {
    return <MessageSquare size={16} />;
  }

  return <ActivityIcon size={16} />;
}


export default function AccountDetailPage() {
  const params = useParams<{
    accountId: string;
  }>();

  const accountId =
    params.accountId;

  const [account, setAccount] =
    useState<Account | null>(null);

  const [contacts, setContacts] =
    useState<Contact[]>([]);

  const [activities, setActivities] =
    useState<Activity[]>([]);

  const [loading, setLoading] =
    useState(true);

  const [error, setError] =
    useState<string | null>(null);

  const [
    contactModalOpen,
    setContactModalOpen,
  ] = useState(false);

  const [
    activityModalOpen,
    setActivityModalOpen,
  ] = useState(false);


  useEffect(() => {
    if (!accountId) {
      return;
    }

    async function load() {
      try {
        setLoading(true);
        setError(null);

        const [
          accountResult,
          contactsResult,
          activitiesResult,
        ] = await Promise.all([
          getAccount(accountId),
          getContacts(accountId),
          getAccountActivities(
            accountId
          ),
        ]);

        setAccount(
          accountResult
        );

        setContacts(
          contactsResult
        );

        setActivities(
          activitiesResult
        );
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Unable to load account."
        );
      } finally {
        setLoading(false);
      }
    }

    load();
  }, [accountId]);


  if (loading) {
    return (
      <div className="appShell">
        <Sidebar active="Accounts" />

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
    error ||
    !account
  ) {
    return (
      <div className="appShell">
        <Sidebar active="Accounts" />

        <main className="mainArea">
          <Topbar />

          <div className="pageContent">
            <Link
              href="/accounts"
              className="backLink"
            >
              <ArrowLeft size={15} />
              Accounts
            </Link>

            <div className="detailError">
              <strong>
                Account unavailable
              </strong>

              <p>
                {error ??
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


  return (
    <div className="appShell">
      <Sidebar active="Accounts" />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <Link
            href="/accounts"
            className="backLink"
          >
            <ArrowLeft size={15} />
            Back to Accounts
          </Link>

          <section className="accountHero">
            <div className="accountHeroIdentity">
              <div className="accountHeroLogo">
                {account.name[0]
                  .toUpperCase()}
              </div>

              <div>
                <div className="accountHeroTitle">
                  <h1>
                    {account.name}
                  </h1>

                  <span
                    className={
                      `stageBadge stage-${account.lifecycle_stage}`
                    }
                  >
                    {
                      account.lifecycle_stage
                    }
                  </span>
                </div>

                <div className="accountHeroMeta">
                  {account.domain && (
                    <span>
                      <Building2
                        size={14}
                      />

                      {account.domain}
                    </span>
                  )}

                  {location && (
                    <span>
                      <MapPin
                        size={14}
                      />

                      {location}
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
                <Plus size={15} />
                Add Contact
              </button>
            </div>
          </section>

          <div className="accountDetailGrid">
            <div className="accountMainColumn">
              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Activity Timeline
                    </h2>

                    <p>
                      Customer interactions
                      and relationship history.
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

                {activities.length === 0 ? (
                  <div className="miniEmptyState">
                    <ActivityIcon
                      size={21}
                    />

                    <strong>
                      No activity yet
                    </strong>

                    <span>
                      Log the first customer
                      interaction.
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
                            <ActivityTypeIcon
                              type={
                                activity.activity_type
                              }
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
                                {formatDate(
                                  activity.occurred_at
                                )}
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
            </div>

            <aside className="accountSideColumn">
              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Company Details
                    </h2>

                    <p>
                      Core account information.
                    </p>
                  </div>
                </div>

                <div className="detailFields">
                  <div>
                    <span>
                      Industry
                    </span>

                    <strong>
                      {
                        account.industry
                        || "—"
                      }
                    </strong>
                  </div>

                  <div>
                    <span>
                      Annual Revenue
                    </span>

                    <strong>
                      {formatRevenue(
                        account.annual_revenue
                      )}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Employees
                    </span>

                    <strong>
                      {
                        account.employee_count
                        ?? "—"
                      }
                    </strong>
                  </div>

                  <div>
                    <span>
                      Phone
                    </span>

                    <strong>
                      {
                        account.phone
                        || "—"
                      }
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

                {contacts.length === 0 ? (
                  <div className="miniEmptyState compact">
                    <Users size={20} />

                    <strong>
                      No contacts
                    </strong>

                    <span>
                      Add the first contact.
                    </span>
                  </div>
                ) : (
                  <div className="contactList">
                    {contacts.map(
                      (contact) => (
                        <div
                          className="contactCard"
                          key={
                            contact.id
                          }
                        >
                          <div className="contactAvatar">
                            {
                              contact
                                .first_name[0]
                                .toUpperCase()
                            }
                            {
                              contact
                                .last_name[0]
                                .toUpperCase()
                            }
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
                              {
                                contact.job_title
                                || "Contact"
                              }
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

              <section className="detailPanel aiAccountCard">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      AIVA Account Summary
                    </h2>

                    <p>
                      AI relationship intelligence.
                    </p>
                  </div>
                </div>

                <div className="aivaPlaceholder">
                  <span>
                    AI COMING NEXT
                  </span>

                  <strong>
                    Customer intelligence
                    will appear here.
                  </strong>

                  <p>
                    AIVA will summarize
                    communications, identify
                    relationship risks and
                    recommend the next best
                    action.
                  </p>
                </div>
              </section>
            </aside>
          </div>
        </div>
      </main>

      <CreateContactModal
        accountId={account.id}
        open={contactModalOpen}
        onClose={() =>
          setContactModalOpen(false)
        }
        onCreated={(contact) =>
          setContacts(
            (current) => [
              ...current,
              contact,
            ]
          )
        }
      />

      <LogActivityModal
        accountId={account.id}
        open={activityModalOpen}
        onClose={() =>
          setActivityModalOpen(false)
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
