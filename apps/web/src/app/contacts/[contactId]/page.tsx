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

import type {
  Account,
} from "@/types/account";

import type {
  Activity,
} from "@/types/activity";

import type {
  Contact,
} from "@/types/contact";

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


          <div className="accountDetailGrid">
            <div className="accountMainColumn">

              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Activity Timeline
                    </h2>

                    <p>
                      Calls, emails,
                      meetings, notes and
                      other interactions
                      with this contact.
                    </p>
                  </div>
                </div>


                {activities.length
                  === 0 ? (
                  <div className="miniEmptyState">
                    <ActivityIcon
                      size={21}
                    />

                    <strong>
                      No activity yet
                    </strong>

                    <span>
                      Contact interactions
                      will appear here.
                    </span>
                  </div>
                ) : (
                  <div className="timeline">
                    {activities.map(
                      (
                        activity
                      ) => (
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

                                  {
                                    activity.direction
                                      ? ` · ${activity.direction}`
                                      : ""
                                  }
                                </span>
                              </div>

                              <time>
                                {
                                  formatDate(
                                    activity.occurred_at
                                  )
                                }
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