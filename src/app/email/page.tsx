mkdir -p src/app/email

cat > src/app/email/page.tsx <<'TSX'
"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import Link from "next/link";

import {
  Building2,
  Inbox,
  Loader2,
  Mail,
  RefreshCcw,
  Search,
  Sparkles,
  Users,
} from "lucide-react";

import {
  Sidebar,
} from "@/components/sidebar";

import {
  Topbar,
} from "@/components/topbar";

import {
  getEmailThreads,
  getThreadMessages,
} from "@/lib/email";

import type {
  EmailThread,
} from "@/types/email";


function formatDate(
  value: string | null
) {
  if (!value) {
    return "No messages yet";
  }

  const date =
    new Date(value);

  const now =
    new Date();

  const sameDay =
    date.toDateString()
    === now.toDateString();

  if (sameDay) {
    return new Intl.DateTimeFormat(
      "en-US",
      {
        hour: "numeric",
        minute: "2-digit",
      }
    ).format(date);
  }

  return new Intl.DateTimeFormat(
    "en-US",
    {
      month: "short",
      day: "numeric",
    }
  ).format(date);
}


function participantLabel(
  thread: EmailThread
) {
  if (
    thread.participants.length
    === 0
  ) {
    return "Unknown participants";
  }

  return thread.participants
    .map(
      (participant) =>
        participant.name
        || participant.email
    )
    .join(", ");
}


export default function EmailInboxPage() {
  const [
    threads,
    setThreads,
  ] = useState<EmailThread[]>([]);

  const [
    unreadThreadIds,
    setUnreadThreadIds,
  ] = useState<Set<string>>(
    new Set()
  );

  const [search, setSearch] =
    useState("");

  const [loading, setLoading] =
    useState(true);

  const [refreshing, setRefreshing] =
    useState(false);

  const [error, setError] =
    useState<string | null>(null);


  async function loadInbox(
    background = false
  ) {
    try {
      if (background) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }

      setError(null);

      const data =
        await getEmailThreads();

      setThreads(data);

      const unreadResults =
        await Promise.all(
          data.map(
            async (thread) => {
              try {
                const messages =
                  await getThreadMessages(
                    thread.id
                  );

                const hasUnread =
                  messages.some(
                    (message) =>
                      message.direction
                      === "inbound"
                      && !message.is_read
                      && !message.is_draft
                  );

                return {
                  threadId:
                    thread.id,
                  hasUnread,
                };
              } catch {
                return {
                  threadId:
                    thread.id,
                  hasUnread:
                    false,
                };
              }
            }
          )
        );

      setUnreadThreadIds(
        new Set(
          unreadResults
            .filter(
              (item) =>
                item.hasUnread
            )
            .map(
              (item) =>
                item.threadId
            )
        )
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to load email inbox."
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }


  useEffect(() => {
    loadInbox();
  }, []);


  const filteredThreads =
    useMemo(() => {
      const value =
        search
          .trim()
          .toLowerCase();

      if (!value) {
        return threads;
      }

      return threads.filter(
        (thread) => {
          const participants =
            thread.participants
              .map(
                (participant) =>
                  `${
                    participant.name
                    ?? ""
                  } ${
                    participant.email
                  }`
              )
              .join(" ")
              .toLowerCase();

          return (
            thread.subject
              .toLowerCase()
              .includes(value)
            ||
            (
              thread.snippet
              ?.toLowerCase()
              .includes(value)
              ?? false
            )
            ||
            participants.includes(
              value
            )
            ||
            thread.provider
              .toLowerCase()
              .includes(value)
          );
        }
      );
    }, [
      threads,
      search,
    ]);


  const providerCount =
    useMemo(() => {
      return new Set(
        threads.map(
          (thread) =>
            thread.provider
        )
      ).size;
    }, [threads]);


  const crmLinkedCount =
    useMemo(() => {
      return threads.filter(
        (thread) =>
          Boolean(
            thread.account_id
            || thread.contact_id
            || thread.opportunity_id
          )
      ).length;
    }, [threads]);


  return (
    <div className="appShell">
      <Sidebar active="Email" />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <section className="emailHeader">
            <div>
              <p className="eyebrow">
                Engagement
              </p>

              <h1>
                Email Inbox
              </h1>

              <p>
                CRM-linked customer
                conversations across
                connected email providers.
              </p>
            </div>

            <button
              type="button"
              className="emailRefresh"
              disabled={refreshing}
              onClick={() =>
                loadInbox(true)
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
          </section>

          <section className="emailMetrics">
            <div>
              <Inbox size={18} />

              <span>
                Threads
              </span>

              <strong>
                {threads.length}
              </strong>
            </div>

            <div>
              <Mail size={18} />

              <span>
                Unread
              </span>

              <strong>
                {
                  unreadThreadIds.size
                }
              </strong>
            </div>

            <div>
              <Users size={18} />

              <span>
                CRM Linked
              </span>

              <strong>
                {crmLinkedCount}
              </strong>
            </div>

            <div>
              <Sparkles
                size={18}
              />

              <span>
                Providers
              </span>

              <strong>
                {providerCount}
              </strong>
            </div>
          </section>

          <section className="emailToolbar">
            <div className="emailSearch">
              <Search size={16} />

              <input
                value={search}
                onChange={(event) =>
                  setSearch(
                    event.target.value
                  )
                }
                placeholder={
                  "Search subject, sender, provider..."
                }
              />
            </div>

            <div className="emailResults">
              {
                filteredThreads.length
              }
              {" "}
              conversation
              {
                filteredThreads.length
                === 1
                  ? ""
                  : "s"
              }
            </div>
          </section>

          {error && (
            <div className="formError">
              {error}
            </div>
          )}

          {loading ? (
            <div className="emailLoading">
              <Loader2 size={20} />

              Loading inbox...
            </div>
          ) : (
            <section className="emailInbox">
              {filteredThreads.length
                === 0 ? (
                <div className="emailEmpty">
                  <Mail size={28} />

                  <strong>
                    No email conversations
                  </strong>

                  <p>
                    Synced and CRM-linked
                    email threads will
                    appear here.
                  </p>
                </div>
              ) : (
                filteredThreads.map(
                  (thread) => {
                    const unread =
                      unreadThreadIds.has(
                        thread.id
                      );

                    return (
                      <Link
                        key={thread.id}
                        href={
                          `/email/${thread.id}`
                        }
                        className={
                          unread
                            ? "emailRow unread"
                            : "emailRow"
                        }
                      >
                        <div className="emailAvatar">
                          {thread
                            .participants[0]
                            ?.name?.[0]
                            ?.toUpperCase()
                            ??
                            thread.subject[0]
                              ?.toUpperCase()
                            ??
                            "E"}
                        </div>

                        <div className="emailMain">
                          <div className="emailRowTop">
                            <div className="emailParticipants">
                              {participantLabel(
                                thread
                              )}

                              {unread && (
                                <span className="unreadDot" />
                              )}
                            </div>

                            <time>
                              {formatDate(
                                thread.last_message_at
                              )}
                            </time>
                          </div>

                          <div className="emailSubject">
                            {thread.subject}
                          </div>

                          <div className="emailSnippet">
                            {thread.snippet
                              || "No message preview available."}
                          </div>

                          <div className="emailMeta">
                            <span className="providerBadge">
                              {
                                thread.provider
                              }
                            </span>

                            {thread.account_id && (
                              <span>
                                <Building2
                                  size={12}
                                />
                                Account linked
                              </span>
                            )}

                            {thread.opportunity_id && (
                              <span>
                                <Sparkles
                                  size={12}
                                />
                                Opportunity linked
                              </span>
                            )}
                          </div>
                        </div>
                      </Link>
                    );
                  }
                )
              )}
            </section>
          )}
        </div>
      </main>

      <style jsx>{`
        .emailHeader {
          display: flex;
          align-items: flex-end;
          justify-content: space-between;
          gap: 20px;
          margin-bottom: 20px;
        }

        .emailHeader h1 {
          margin: 4px 0 5px;
          font-size: 26px;
          letter-spacing: -0.035em;
        }

        .emailHeader p {
          margin: 0;
          color: var(--muted);
          font-size: 13px;
        }

        .emailRefresh {
          height: 38px;
          display: inline-flex;
          align-items: center;
          gap: 7px;
          padding: 0 13px;
          border: 1px solid var(--border);
          border-radius: 9px;
          background: white;
          color: var(--text);
          font-size: 12px;
          font-weight: 700;
        }

        .emailRefresh:disabled {
          opacity: 0.6;
        }

        .emailMetrics {
          display: grid;
          grid-template-columns:
            repeat(4, minmax(0, 1fr));
          gap: 12px;
          margin-bottom: 16px;
        }

        .emailMetrics > div {
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

        .emailMetrics span {
          color: var(--muted);
          font-size: 11px;
        }

        .emailMetrics strong {
          font-size: 16px;
        }

        .emailToolbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 15px;
          margin-bottom: 12px;
        }

        .emailSearch {
          width: min(520px, 100%);
          height: 40px;
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 0 12px;
          border: 1px solid var(--border);
          border-radius: 10px;
          background: white;
          color: var(--muted);
        }

        .emailSearch input {
          width: 100%;
          border: 0;
          outline: 0;
          background: transparent;
          color: var(--text);
          font-size: 12px;
        }

        .emailResults {
          color: var(--muted);
          font-size: 11px;
        }

        .emailInbox {
          overflow: hidden;
          border: 1px solid var(--border);
          border-radius: 14px;
          background: white;
        }

        .emailRow {
          display: flex;
          gap: 13px;
          padding: 16px 17px;
          border-bottom: 1px solid #eef0f4;
          color: inherit;
          text-decoration: none;
          transition: background 0.15s ease;
        }

        .emailRow:last-child {
          border-bottom: 0;
        }

        .emailRow:hover {
          background: #fafafe;
        }

        .emailRow.unread {
          background: #faf9ff;
        }

        .emailAvatar {
          width: 40px;
          height: 40px;
          flex: 0 0 auto;
          display: grid;
          place-items: center;
          border-radius: 11px;
          background: var(--primary-soft);
          color: var(--primary);
          font-size: 13px;
          font-weight: 800;
        }

        .emailMain {
          min-width: 0;
          flex: 1;
        }

        .emailRowTop {
          display: flex;
          justify-content: space-between;
          gap: 12px;
        }

        .emailParticipants {
          display: flex;
          align-items: center;
          gap: 7px;
          overflow: hidden;
          font-size: 11px;
          font-weight: 700;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .emailRow.unread .emailParticipants,
        .emailRow.unread .emailSubject {
          font-weight: 800;
        }

        .unreadDot {
          width: 7px;
          height: 7px;
          flex: 0 0 auto;
          border-radius: 50%;
          background: var(--primary);
        }

        .emailRow time {
          flex: 0 0 auto;
          color: var(--muted);
          font-size: 9px;
        }

        .emailSubject {
          margin-top: 4px;
          font-size: 12px;
          font-weight: 700;
        }

        .emailSnippet {
          overflow: hidden;
          margin-top: 4px;
          color: var(--muted);
          font-size: 10px;
          line-height: 1.5;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .emailMeta {
          display: flex;
          align-items: center;
          flex-wrap: wrap;
          gap: 8px;
          margin-top: 10px;
          color: var(--muted);
          font-size: 9px;
        }

        .emailMeta > span {
          display: inline-flex;
          align-items: center;
          gap: 4px;
        }

        .providerBadge {
          padding: 3px 7px;
          border-radius: 999px;
          background: #f0eefc;
          color: #6754d9;
          font-weight: 800;
          text-transform: capitalize;
        }

        .emailLoading,
        .emailEmpty {
          min-height: 280px;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 8px;
          border: 1px solid var(--border);
          border-radius: 14px;
          background: white;
          color: var(--muted);
        }

        .emailLoading {
          flex-direction: row;
        }

        .emailEmpty strong {
          color: var(--text);
          font-size: 13px;
        }

        .emailEmpty p {
          margin: 0;
          font-size: 11px;
        }

        @media (max-width: 900px) {
          .emailHeader {
            align-items: flex-start;
            flex-direction: column;
          }

          .emailMetrics {
            grid-template-columns:
              repeat(2, 1fr);
          }

          .emailToolbar {
            align-items: stretch;
            flex-direction: column;
          }
        }

        @media (max-width: 600px) {
          .emailMetrics {
            grid-template-columns: 1fr;
          }

          .emailMeta {
            display: none;
          }
        }
      `}</style>
    </div>
  );
}
TSX

npx tsc --noEmit

echo "=== EMAIL INBOX PAGE PASSED ==="
