mkdir -p src/app/meetings

cat > src/app/meetings/page.tsx <<'TSX'
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
  Building2,
  CalendarDays,
  CheckCircle2,
  Clock3,
  ExternalLink,
  Loader2,
  MapPin,
  Plus,
  RefreshCcw,
  Users,
  Video,
  X,
  XCircle,
} from "lucide-react";

import {
  Sidebar,
} from "@/components/sidebar";

import {
  Topbar,
} from "@/components/topbar";

import {
  getAccounts,
} from "@/lib/accounts";

import {
  getContacts,
} from "@/lib/contacts";

import {
  createMeeting,
  getMeetings,
  updateMeeting,
} from "@/lib/meetings";

import {
  getOpportunities,
} from "@/lib/opportunities";

import type {
  Account,
} from "@/types/account";

import type {
  Contact,
} from "@/types/contact";

import type {
  Meeting,
  MeetingStatus,
} from "@/types/meeting";

import type {
  Opportunity,
} from "@/types/opportunity";


type MeetingFilter =
  | "all"
  | "scheduled"
  | "completed"
  | "cancelled";


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


function formatDuration(
  start: string,
  end: string
) {
  const startDate =
    new Date(start);

  const endDate =
    new Date(end);

  const minutes =
    Math.max(
      0,
      Math.round(
        (
          endDate.getTime()
          - startDate.getTime()
        )
        / 60000
      )
    );

  if (minutes < 60) {
    return `${minutes} min`;
  }

  const hours =
    Math.floor(
      minutes / 60
    );

  const remaining =
    minutes % 60;

  return remaining
    ? `${hours}h ${remaining}m`
    : `${hours}h`;
}


function contactName(
  contact: Contact
) {
  return `${contact.first_name} ${contact.last_name}`.trim();
}


export default function MeetingsPage() {
  const [
    meetings,
    setMeetings,
  ] = useState<Meeting[]>([]);

  const [
    accounts,
    setAccounts,
  ] = useState<Account[]>([]);

  const [
    contacts,
    setContacts,
  ] = useState<Contact[]>([]);

  const [
    opportunities,
    setOpportunities,
  ] = useState<
    Opportunity[]
  >([]);

  const [
    filter,
    setFilter,
  ] = useState<MeetingFilter>(
    "all"
  );

  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    refreshing,
    setRefreshing,
  ] = useState(false);

  const [
    updatingId,
    setUpdatingId,
  ] = useState<string | null>(
    null
  );

  const [
    error,
    setError,
  ] = useState<string | null>(
    null
  );

  const [
    createOpen,
    setCreateOpen,
  ] = useState(false);

  const [
    creating,
    setCreating,
  ] = useState(false);

  const [
    title,
    setTitle,
  ] = useState("");

  const [
    accountId,
    setAccountId,
  ] = useState("");

  const [
    contactId,
    setContactId,
  ] = useState("");

  const [
    opportunityId,
    setOpportunityId,
  ] = useState("");

  const [
    startAt,
    setStartAt,
  ] = useState("");

  const [
    endAt,
    setEndAt,
  ] = useState("");

  const [
    location,
    setLocation,
  ] = useState("");

  const [
    meetingUrl,
    setMeetingUrl,
  ] = useState("");

  const [
    description,
    setDescription,
  ] = useState("");


  async function loadData(
    background = false
  ) {
    try {
      if (background) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }

      setError(null);

      const [
        meetingData,
        accountData,
        contactData,
        opportunityData,
      ] = await Promise.all([
        getMeetings(),
        getAccounts(),
        getContacts(),
        getOpportunities(),
      ]);

      setMeetings(
        meetingData
      );

      setAccounts(
        accountData
      );

      setContacts(
        contactData
      );

      setOpportunities(
        opportunityData
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to load meetings."
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }


  useEffect(() => {
    loadData();
  }, []);


  const accountMap =
    useMemo(() => {
      return new Map(
        accounts.map(
          (account) => [
            account.id,
            account,
          ]
        )
      );
    }, [accounts]);


  const contactMap =
    useMemo(() => {
      return new Map(
        contacts.map(
          (contact) => [
            contact.id,
            contact,
          ]
        )
      );
    }, [contacts]);


  const opportunityMap =
    useMemo(() => {
      return new Map(
        opportunities.map(
          (opportunity) => [
            opportunity.id,
            opportunity,
          ]
        )
      );
    }, [opportunities]);


  const filteredMeetings =
    useMemo(() => {
      if (filter === "all") {
        return meetings;
      }

      return meetings.filter(
        (meeting) =>
          meeting.status
          === filter
      );
    }, [
      meetings,
      filter,
    ]);


  const upcomingCount =
    useMemo(() => {
      const now =
        new Date();

      return meetings.filter(
        (meeting) =>
          meeting.status
          === "scheduled"
          &&
          new Date(
            meeting.start_at
          ) >= now
      ).length;
    }, [meetings]);


  const completedCount =
    useMemo(() => {
      return meetings.filter(
        (meeting) =>
          meeting.status
          === "completed"
      ).length;
    }, [meetings]);


  const cancelledCount =
    useMemo(() => {
      return meetings.filter(
        (meeting) =>
          meeting.status
          === "cancelled"
      ).length;
    }, [meetings]);


  const availableContacts =
    useMemo(() => {
      if (!accountId) {
        return contacts;
      }

      return contacts.filter(
        (contact) =>
          contact.account_id
          === accountId
      );
    }, [
      contacts,
      accountId,
    ]);


  const availableOpportunities =
    useMemo(() => {
      if (!accountId) {
        return opportunities;
      }

      return opportunities.filter(
        (opportunity) =>
          opportunity.account_id
          === accountId
      );
    }, [
      opportunities,
      accountId,
    ]);


  function resetForm() {
    setTitle("");
    setAccountId("");
    setContactId("");
    setOpportunityId("");
    setStartAt("");
    setEndAt("");
    setLocation("");
    setMeetingUrl("");
    setDescription("");
  }


  function handleAccountChange(
    value: string
  ) {
    setAccountId(value);

    setContactId("");

    setOpportunityId("");
  }


  async function handleCreate(
    event: FormEvent
  ) {
    event.preventDefault();

    if (
      !title.trim()
      || !startAt
      || !endAt
    ) {
      setError(
        "Title, start time and end time are required."
      );

      return;
    }

    const start =
      new Date(startAt);

    const end =
      new Date(endAt);

    if (
      Number.isNaN(
        start.getTime()
      )
      ||
      Number.isNaN(
        end.getTime()
      )
      ||
      end <= start
    ) {
      setError(
        "Meeting end time must be later than the start time."
      );

      return;
    }

    try {
      setCreating(true);
      setError(null);

      const contact =
        contactId
          ? contactMap.get(
              contactId
            )
          : null;

      const created =
        await createMeeting({
          provider:
            "manual",

          title:
            title.trim(),

          account_id:
            accountId
            || null,

          contact_id:
            contactId
            || null,

          opportunity_id:
            opportunityId
            || null,

          start_at:
            start.toISOString(),

          end_at:
            end.toISOString(),

          timezone:
            Intl.DateTimeFormat()
              .resolvedOptions()
              .timeZone,

          status:
            "scheduled",

          location:
            location.trim()
            || null,

          meeting_url:
            meetingUrl.trim()
            || null,

          description:
            description.trim()
            || null,

          is_online:
            Boolean(
              meetingUrl.trim()
            ),

          attendees:
            contact?.email
              ? [
                  {
                    name:
                      contactName(
                        contact
                      ),
                    email:
                      contact.email,
                    response_status:
                      null,
                  },
                ]
              : [],
        });

      setMeetings(
        (current) =>
          [...current, created]
            .sort(
              (a, b) =>
                new Date(
                  a.start_at
                ).getTime()
                -
                new Date(
                  b.start_at
                ).getTime()
            )
      );

      resetForm();

      setCreateOpen(false);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to schedule meeting."
      );
    } finally {
      setCreating(false);
    }
  }


  async function changeStatus(
    meeting: Meeting,
    status:
      MeetingStatus
  ) {
    if (
      meeting.status
      === status
    ) {
      return;
    }

    try {
      setUpdatingId(
        meeting.id
      );

      setError(null);

      const updated =
        await updateMeeting(
          meeting.id,
          {
            status,
          }
        );

      setMeetings(
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
          : "Unable to update meeting."
      );
    } finally {
      setUpdatingId(null);
    }
  }


  return (
    <div className="appShell">
      <Sidebar active="Meetings" />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <section className="meetingHeader">
            <div>
              <p className="eyebrow">
                Engagement
              </p>

              <h1>
                Meetings
              </h1>

              <p>
                Schedule and manage
                CRM-linked customer
                meetings.
              </p>
            </div>

            <div className="meetingHeaderActions">
              <button
                type="button"
                className="secondaryButton"
                disabled={
                  refreshing
                }
                onClick={() =>
                  loadData(true)
                }
              >
                {refreshing ? (
                  <Loader2
                    size={15}
                  />
                ) : (
                  <RefreshCcw
                    size={15}
                  />
                )}

                Refresh
              </button>

              <button
                type="button"
                className="primaryButton"
                onClick={() =>
                  setCreateOpen(
                    true
                  )
                }
              >
                <Plus size={15} />
                Schedule Meeting
              </button>
            </div>
          </section>

          <section className="meetingMetrics">
            <div>
              <CalendarDays
                size={18}
              />

              <span>
                Total Meetings
              </span>

              <strong>
                {meetings.length}
              </strong>
            </div>

            <div>
              <Clock3 size={18} />

              <span>
                Upcoming
              </span>

              <strong>
                {upcomingCount}
              </strong>
            </div>

            <div>
              <CheckCircle2
                size={18}
              />

              <span>
                Completed
              </span>

              <strong>
                {completedCount}
              </strong>
            </div>

            <div>
              <XCircle size={18} />

              <span>
                Cancelled
              </span>

              <strong>
                {cancelledCount}
              </strong>
            </div>
          </section>

          <section className="meetingToolbar">
            {(
              [
                "all",
                "scheduled",
                "completed",
                "cancelled",
              ] as MeetingFilter[]
            ).map(
              (value) => (
                <button
                  type="button"
                  key={value}
                  className={
                    filter === value
                      ? "filterButton active"
                      : "filterButton"
                  }
                  onClick={() =>
                    setFilter(value)
                  }
                >
                  {
                    value
                      .charAt(0)
                      .toUpperCase()
                    +
                    value.slice(1)
                  }
                </button>
              )
            )}
          </section>

          {error && (
            <div className="formError">
              {error}
            </div>
          )}

          {loading ? (
            <div className="meetingLoading">
              <Loader2 size={20} />
              Loading meetings...
            </div>
          ) : (
            <section className="meetingList">
              {filteredMeetings.length
                === 0 ? (
                  <div className="meetingEmpty">
                    <CalendarDays
                      size={28}
                    />

                    <strong>
                      No meetings found
                    </strong>

                    <p>
                      Schedule a CRM-linked
                      meeting to begin.
                    </p>
                  </div>
                ) : (
                  filteredMeetings.map(
                    (meeting) => {
                      const account =
                        meeting.account_id
                          ? accountMap.get(
                              meeting.account_id
                            )
                          : null;

                      const contact =
                        meeting.contact_id
                          ? contactMap.get(
                              meeting.contact_id
                            )
                          : null;

                      const opportunity =
                        meeting.opportunity_id
                          ? opportunityMap.get(
                              meeting.opportunity_id
                            )
                          : null;

                      const updating =
                        updatingId
                        === meeting.id;

                      return (
                        <article
                          key={
                            meeting.id
                          }
                          className="meetingCard"
                        >
                          <div className="meetingDateBlock">
                            <span>
                              {new Intl.DateTimeFormat(
                                "en-US",
                                {
                                  month:
                                    "short",
                                }
                              ).format(
                                new Date(
                                  meeting.start_at
                                )
                              )}
                            </span>

                            <strong>
                              {new Date(
                                meeting.start_at
                              ).getDate()}
                            </strong>
                          </div>

                          <div className="meetingContent">
                            <div className="meetingTop">
                              <div>
                                <div className="meetingTitleLine">
                                  <h2>
                                    {
                                      meeting.title
                                    }
                                  </h2>

                                  <span
                                    className={
                                      `statusBadge status-${meeting.status}`
                                    }
                                  >
                                    {
                                      meeting.status
                                    }
                                  </span>
                                </div>

                                <div className="meetingTime">
                                  <Clock3
                                    size={13}
                                  />

                                  {formatDateTime(
                                    meeting.start_at
                                  )}

                                  <span>
                                    •
                                  </span>

                                  {formatDuration(
                                    meeting.start_at,
                                    meeting.end_at
                                  )}
                                </div>
                              </div>

                              <select
                                value={
                                  meeting.status
                                }
                                disabled={
                                  updating
                                }
                                onChange={(
                                  event
                                ) =>
                                  changeStatus(
                                    meeting,
                                    event
                                      .target
                                      .value
                                    as MeetingStatus
                                  )
                                }
                              >
                                <option value="scheduled">
                                  Scheduled
                                </option>

                                <option value="completed">
                                  Completed
                                </option>

                                <option value="cancelled">
                                  Cancelled
                                </option>
                              </select>
                            </div>

                            {meeting.description && (
                              <p className="meetingDescription">
                                {
                                  meeting.description
                                }
                              </p>
                            )}

                            <div className="meetingMeta">
                              {account && (
                                <Link
                                  href={
                                    `/accounts/${account.id}`
                                  }
                                >
                                  <Building2
                                    size={13}
                                  />

                                  {
                                    account.name
                                  }
                                </Link>
                              )}

                              {contact && (
                                <span>
                                  <Users
                                    size={13}
                                  />

                                  {contactName(
                                    contact
                                  )}
                                </span>
                              )}

                              {meeting.location && (
                                <span>
                                  <MapPin
                                    size={13}
                                  />

                                  {
                                    meeting.location
                                  }
                                </span>
                              )}

                              {meeting.is_online && (
                                <span>
                                  <Video
                                    size={13}
                                  />

                                  Online
                                </span>
                              )}
                            </div>

                            <div className="meetingFooter">
                              <div>
                                {opportunity && (
                                  <Link
                                    href={
                                      `/opportunities/${opportunity.id}`
                                    }
                                  >
                                    Opportunity:
                                    {" "}
                                    {
                                      opportunity.name
                                    }
                                  </Link>
                                )}
                              </div>

                              {meeting.meeting_url && (
                                <a
                                  href={
                                    meeting.meeting_url
                                  }
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  Join Meeting
                                  <ExternalLink
                                    size={12}
                                  />
                                </a>
                              )}
                            </div>
                          </div>
                        </article>
                      );
                    }
                  )
                )}
            </section>
          )}
        </div>
      </main>

      {createOpen && (
        <div className="meetingModalBackdrop">
          <div className="meetingModal">
            <div className="meetingModalHeader">
              <div>
                <h2>
                  Schedule Meeting
                </h2>

                <p>
                  Create a CRM-linked
                  customer meeting.
                </p>
              </div>

              <button
                type="button"
                onClick={() => {
                  resetForm();
                  setCreateOpen(
                    false
                  );
                }}
              >
                <X size={18} />
              </button>
            </div>

            <form
              onSubmit={
                handleCreate
              }
            >
              <div className="meetingFormGrid">
                <label className="fullWidth">
                  <span>
                    Meeting Title *
                  </span>

                  <input
                    value={title}
                    onChange={(
                      event
                    ) =>
                      setTitle(
                        event
                          .target
                          .value
                      )
                    }
                    placeholder="Acme CRM Automation Demo"
                    required
                  />
                </label>

                <label>
                  <span>
                    Account
                  </span>

                  <select
                    value={
                      accountId
                    }
                    onChange={(
                      event
                    ) =>
                      handleAccountChange(
                        event
                          .target
                          .value
                      )
                    }
                  >
                    <option value="">
                      No account
                    </option>

                    {accounts.map(
                      (account) => (
                        <option
                          key={
                            account.id
                          }
                          value={
                            account.id
                          }
                        >
                          {
                            account.name
                          }
                        </option>
                      )
                    )}
                  </select>
                </label>

                <label>
                  <span>
                    Contact
                  </span>

                  <select
                    value={
                      contactId
                    }
                    onChange={(
                      event
                    ) =>
                      setContactId(
                        event
                          .target
                          .value
                      )
                    }
                  >
                    <option value="">
                      No contact
                    </option>

                    {availableContacts.map(
                      (contact) => (
                        <option
                          key={
                            contact.id
                          }
                          value={
                            contact.id
                          }
                        >
                          {contactName(
                            contact
                          )}
                        </option>
                      )
                    )}
                  </select>
                </label>

                <label className="fullWidth">
                  <span>
                    Opportunity
                  </span>

                  <select
                    value={
                      opportunityId
                    }
                    onChange={(
                      event
                    ) =>
                      setOpportunityId(
                        event
                          .target
                          .value
                      )
                    }
                  >
                    <option value="">
                      No opportunity
                    </option>

                    {availableOpportunities.map(
                      (
                        opportunity
                      ) => (
                        <option
                          key={
                            opportunity.id
                          }
                          value={
                            opportunity.id
                          }
                        >
                          {
                            opportunity.name
                          }
                        </option>
                      )
                    )}
                  </select>
                </label>

                <label>
                  <span>
                    Start *
                  </span>

                  <input
                    type="datetime-local"
                    value={
                      startAt
                    }
                    onChange={(
                      event
                    ) =>
                      setStartAt(
                        event
                          .target
                          .value
                      )
                    }
                    required
                  />
                </label>

                <label>
                  <span>
                    End *
                  </span>

                  <input
                    type="datetime-local"
                    value={
                      endAt
                    }
                    onChange={(
                      event
                    ) =>
                      setEndAt(
                        event
                          .target
                          .value
                      )
                    }
                    required
                  />
                </label>

                <label>
                  <span>
                    Location
                  </span>

                  <input
                    value={
                      location
                    }
                    onChange={(
                      event
                    ) =>
                      setLocation(
                        event
                          .target
                          .value
                      )
                    }
                    placeholder="Microsoft Teams"
                  />
                </label>

                <label>
                  <span>
                    Meeting URL
                  </span>

                  <input
                    value={
                      meetingUrl
                    }
                    onChange={(
                      event
                    ) =>
                      setMeetingUrl(
                        event
                          .target
                          .value
                      )
                    }
                    placeholder="https://..."
                  />
                </label>

                <label className="fullWidth">
                  <span>
                    Description
                  </span>

                  <textarea
                    rows={4}
                    value={
                      description
                    }
                    onChange={(
                      event
                    ) =>
                      setDescription(
                        event
                          .target
                          .value
                      )
                    }
                    placeholder="Meeting agenda and context..."
                  />
                </label>
              </div>

              <div className="meetingModalFooter">
                <button
                  type="button"
                  className="secondaryButton"
                  onClick={() => {
                    resetForm();
                    setCreateOpen(
                      false
                    );
                  }}
                >
                  Cancel
                </button>

                <button
                  type="submit"
                  className="primaryButton"
                  disabled={
                    creating
                  }
                >
                  {creating ? (
                    <Loader2
                      size={15}
                    />
                  ) : (
                    <CalendarDays
                      size={15}
                    />
                  )}

                  {creating
                    ? "Scheduling..."
                    : "Schedule Meeting"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <style jsx>{`
        .meetingHeader {
          display: flex;
          align-items: flex-end;
          justify-content: space-between;
          gap: 18px;
          margin-bottom: 20px;
        }

        .meetingHeader h1 {
          margin: 4px 0 5px;
          font-size: 26px;
          letter-spacing: -0.035em;
        }

        .meetingHeader p {
          margin: 0;
          color: var(--muted);
          font-size: 13px;
        }

        .meetingHeaderActions {
          display: flex;
          gap: 8px;
        }

        .primaryButton,
        .secondaryButton {
          height: 38px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 7px;
          padding: 0 13px;
          border-radius: 8px;
          font-size: 11px;
          font-weight: 800;
        }

        .primaryButton {
          border: 0;
          background: var(--primary);
          color: white;
        }

        .secondaryButton {
          border: 1px solid var(--border);
          background: white;
          color: var(--text);
        }

        .meetingMetrics {
          display: grid;
          grid-template-columns:
            repeat(
              4,
              minmax(0, 1fr)
            );
          gap: 12px;
          margin-bottom: 16px;
        }

        .meetingMetrics > div {
          display: grid;
          grid-template-columns:
            auto 1fr auto;
          align-items: center;
          gap: 9px;
          padding: 15px 16px;
          border: 1px solid var(--border);
          border-radius: 12px;
          background: white;
        }

        .meetingMetrics span {
          color: var(--muted);
          font-size: 11px;
        }

        .meetingMetrics strong {
          font-size: 16px;
        }

        .meetingToolbar {
          display: flex;
          gap: 7px;
          margin-bottom: 13px;
        }

        .filterButton {
          padding: 7px 10px;
          border: 1px solid var(--border);
          border-radius: 8px;
          background: white;
          color: var(--muted);
          font-size: 10px;
          font-weight: 700;
        }

        .filterButton.active {
          border-color: var(--primary);
          background: var(--primary-soft);
          color: var(--primary);
        }

        .meetingList {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .meetingCard {
          display: flex;
          gap: 14px;
          padding: 15px;
          border: 1px solid var(--border);
          border-radius: 13px;
          background: white;
        }

        .meetingDateBlock {
          width: 54px;
          height: 59px;
          flex: 0 0 auto;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          border-radius: 10px;
          background: var(--primary-soft);
        }

        .meetingDateBlock span {
          color: var(--primary);
          font-size: 9px;
          font-weight: 800;
          text-transform: uppercase;
        }

        .meetingDateBlock strong {
          margin-top: 2px;
          color: var(--primary);
          font-size: 21px;
        }

        .meetingContent {
          min-width: 0;
          flex: 1;
        }

        .meetingTop {
          display: flex;
          justify-content: space-between;
          gap: 14px;
        }

        .meetingTitleLine {
          display: flex;
          align-items: center;
          flex-wrap: wrap;
          gap: 8px;
        }

        .meetingTitleLine h2 {
          margin: 0;
          font-size: 13px;
        }

        .statusBadge {
          padding: 3px 7px;
          border-radius: 999px;
          font-size: 8px;
          font-weight: 800;
          text-transform: capitalize;
        }

        .status-scheduled {
          background: #eef1ff;
          color: #5c63cf;
        }

        .status-completed {
          background: #ecf9f1;
          color: #258553;
        }

        .status-cancelled {
          background: #fff0f0;
          color: #c95454;
        }

        .meetingTime {
          display: flex;
          align-items: center;
          gap: 5px;
          margin-top: 6px;
          color: var(--muted);
          font-size: 9px;
        }

        .meetingTop select {
          height: 32px;
          padding: 0 8px;
          border: 1px solid var(--border);
          border-radius: 8px;
          background: white;
          font-size: 9px;
        }

        .meetingDescription {
          margin: 11px 0 0;
          color: #555b68;
          font-size: 10px;
          line-height: 1.5;
        }

        .meetingMeta {
          display: flex;
          flex-wrap: wrap;
          gap: 12px;
          margin-top: 12px;
          color: var(--muted);
          font-size: 9px;
        }

        .meetingMeta span,
        .meetingMeta a {
          display: inline-flex;
          align-items: center;
          gap: 5px;
          color: inherit;
          text-decoration: none;
        }

        .meetingMeta a:hover {
          color: var(--primary);
        }

        .meetingFooter {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
          margin-top: 12px;
          padding-top: 10px;
          border-top: 1px solid #eef0f4;
        }

        .meetingFooter a {
          display: inline-flex;
          align-items: center;
          gap: 5px;
          color: var(--primary);
          font-size: 9px;
          font-weight: 700;
          text-decoration: none;
        }

        .meetingLoading,
        .meetingEmpty {
          min-height: 280px;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 8px;
          border: 1px solid var(--border);
          border-radius: 13px;
          background: white;
          color: var(--muted);
        }

        .meetingEmpty {
          flex-direction: column;
        }

        .meetingEmpty strong {
          color: var(--text);
          font-size: 12px;
        }

        .meetingEmpty p {
          margin: 0;
          font-size: 10px;
        }

        .meetingModalBackdrop {
          position: fixed;
          inset: 0;
          z-index: 100;
          display: grid;
          place-items: center;
          padding: 20px;
          background:
            rgba(
              19,
              21,
              29,
              0.46
            );
        }

        .meetingModal {
          width: min(
            700px,
            100%
          );
          max-height: 90vh;
          overflow-y: auto;
          border-radius: 15px;
          background: white;
          box-shadow:
            0 24px 70px
            rgba(
              18,
              22,
              35,
              0.18
            );
        }

        .meetingModalHeader {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 15px;
          padding: 18px;
          border-bottom: 1px solid #eef0f4;
        }

        .meetingModalHeader h2 {
          margin: 0;
          font-size: 15px;
        }

        .meetingModalHeader p {
          margin: 4px 0 0;
          color: var(--muted);
          font-size: 10px;
        }

        .meetingModalHeader > button {
          width: 32px;
          height: 32px;
          display: grid;
          place-items: center;
          border: 0;
          border-radius: 8px;
          background: #f4f5f8;
        }

        .meetingModal form {
          padding: 18px;
        }

        .meetingFormGrid {
          display: grid;
          grid-template-columns:
            repeat(
              2,
              minmax(0, 1fr)
            );
          gap: 13px;
        }

        .meetingFormGrid label {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .meetingFormGrid label > span {
          color: var(--muted);
          font-size: 9px;
          font-weight: 700;
          text-transform: uppercase;
        }

        .meetingFormGrid input,
        .meetingFormGrid select,
        .meetingFormGrid textarea {
          box-sizing: border-box;
          width: 100%;
          border: 1px solid var(--border);
          border-radius: 8px;
          background: white;
          padding: 9px 10px;
          outline: none;
          font: inherit;
          font-size: 10px;
        }

        .meetingFormGrid input,
        .meetingFormGrid select {
          height: 38px;
        }

        .meetingFormGrid input:focus,
        .meetingFormGrid select:focus,
        .meetingFormGrid textarea:focus {
          border-color: var(--primary);
        }

        .fullWidth {
          grid-column:
            1 / -1;
        }

        .meetingModalFooter {
          display: flex;
          justify-content: flex-end;
          gap: 8px;
          margin-top: 17px;
          padding-top: 15px;
          border-top: 1px solid #eef0f4;
        }

        @media (
          max-width: 900px
        ) {
          .meetingHeader {
            align-items: flex-start;
            flex-direction: column;
          }

          .meetingMetrics {
            grid-template-columns:
              repeat(
                2,
                1fr
              );
          }
        }

        @media (
          max-width: 650px
        ) {
          .meetingMetrics,
          .meetingFormGrid {
            grid-template-columns:
              1fr;
          }

          .fullWidth {
            grid-column: auto;
          }

          .meetingTop,
          .meetingFooter {
            align-items: flex-start;
            flex-direction: column;
          }
        }
      `}</style>
    </div>
  );
}
TSX

npx tsc --noEmit

echo "=== MEETINGS PAGE PASSED ==="
