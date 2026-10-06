"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  Building2,
  Inbox,
  Mail,
  Plus,
  Search,
  Send,
  UserRound,
} from "lucide-react";

import {
  ComposeEmailModal,
} from "@/components/email/compose-email-modal";

import {
  ReplyComposer,
} from "@/components/email/reply-composer";

import {
  Sidebar,
} from "@/components/sidebar";

import {
  Topbar,
} from "@/components/topbar";

import {
  getEmailThreads,
  getThreadMessages,
} from "@/lib/emails";

import type {
  EmailMessage,
  EmailSendResult,
  EmailThread,
} from "@/types/email";


const AIVA_FROM_ADDRESS =
  "sales@aiva.example";

const AIVA_FROM_NAME =
  "AIVA Sales";


type UnknownRecord =
  Record<string, unknown>;


/* ============================================================
   Generic value helpers
   ============================================================ */


function asRecord(
  value: unknown
): UnknownRecord | null {
  if (
    typeof value === "object"
    &&
    value !== null
    &&
    !Array.isArray(value)
  ) {
    return value as UnknownRecord;
  }

  return null;
}


function stringValue(
  value: unknown
): string | null {
  if (
    typeof value === "string"
    &&
    value.trim()
  ) {
    return value.trim();
  }

  return null;
}


function recordString(
  record: UnknownRecord,
  key: string
): string | null {
  return stringValue(
    record[key]
  );
}


function threadRecord(
  thread: EmailThread
): UnknownRecord {
  return thread as unknown as UnknownRecord;
}


function messageRecord(
  message: EmailMessage
): UnknownRecord {
  return message as unknown as UnknownRecord;
}


/* ============================================================
   Thread helpers
   ============================================================ */


function threadId(
  thread: EmailThread
): string {
  const record =
    threadRecord(thread);

  return (
    recordString(
      record,
      "id"
    )
    || ""
  );
}


function threadSubject(
  thread: EmailThread
): string {
  const record =
    threadRecord(thread);

  return (
    recordString(
      record,
      "subject"
    )
    || "No subject"
  );
}


function threadSnippet(
  thread: EmailThread
): string {
  const record =
    threadRecord(thread);

  return (
    recordString(
      record,
      "snippet"
    )
    || ""
  );
}


function threadProvider(
  thread: EmailThread
): string {
  const record =
    threadRecord(thread);

  return (
    recordString(
      record,
      "provider"
    )
    || "Internal"
  );
}


function threadDateValue(
  thread: EmailThread
): string | null {
  const record =
    threadRecord(thread);

  return (
    recordString(
      record,
      "last_message_at"
    )
    ||
    recordString(
      record,
      "updated_at"
    )
    ||
    recordString(
      record,
      "created_at"
    )
  );
}


function threadIsCrmLinked(
  thread: EmailThread
): boolean {
  const record =
    threadRecord(thread);

  return Boolean(
    record.account_id
    ||
    record.contact_id
    ||
    record.lead_id
    ||
    record.opportunity_id
  );
}


function threadHasAccount(
  thread: EmailThread
): boolean {
  return Boolean(
    threadRecord(thread)
      .account_id
  );
}


function threadHasContact(
  thread: EmailThread
): boolean {
  return Boolean(
    threadRecord(thread)
      .contact_id
  );
}


function threadHasLead(
  thread: EmailThread
): boolean {
  return Boolean(
    threadRecord(thread)
      .lead_id
  );
}


function threadHasOpportunity(
  thread: EmailThread
): boolean {
  return Boolean(
    threadRecord(thread)
      .opportunity_id
  );
}


function threadParticipant(
  thread: EmailThread
): string {
  const record =
    threadRecord(thread);

  const participants =
    record.participants;

  if (
    Array.isArray(
      participants
    )
  ) {
    for (
      const participant
      of participants
    ) {
      const item =
        asRecord(
          participant
        );

      if (!item) {
        continue;
      }

      const email =
        recordString(
          item,
          "email"
        )
        ||
        recordString(
          item,
          "address"
        );

      const name =
        recordString(
          item,
          "name"
        );

      if (
        email
        &&
        email.toLowerCase()
        ===
        AIVA_FROM_ADDRESS
          .toLowerCase()
      ) {
        continue;
      }

      if (name) {
        return name;
      }

      if (email) {
        return email;
      }
    }
  }

  return (
    recordString(
      record,
      "participant_name"
    )
    ||
    recordString(
      record,
      "from_name"
    )
    ||
    AIVA_FROM_NAME
  );
}


/* ============================================================
   Recipient helpers
   ============================================================ */


function recipientEmail(
  recipient: unknown
): string | null {
  const record =
    asRecord(
      recipient
    );

  if (!record) {
    return null;
  }

  return (
    recordString(
      record,
      "email"
    )
    ||
    recordString(
      record,
      "address"
    )
    ||
    recordString(
      record,
      "email_address"
    )
  );
}


function messageRecipientSummary(
  message: EmailMessage
): string {
  const record =
    messageRecord(message);

  const recipients =
    record.to_recipients;

  if (
    !Array.isArray(
      recipients
    )
  ) {
    return "No recipients";
  }

  const emails =
    recipients
      .map(
        recipientEmail
      )
      .filter(
        (
          email
        ): email is string =>
          Boolean(email)
      );

  if (
    emails.length === 0
  ) {
    return "No recipients";
  }

  return emails.join(", ");
}


/* ============================================================
   Message helpers
   ============================================================ */


function messageDirection(
  message: EmailMessage
): string {
  const record =
    messageRecord(message);

  return String(
    record.direction
    || ""
  )
    .toLowerCase();
}


function isInbound(
  message: EmailMessage
): boolean {
  return messageDirection(
    message
  ).includes(
    "inbound"
  );
}


function isOutbound(
  message: EmailMessage
): boolean {
  return messageDirection(
    message
  ).includes(
    "outbound"
  );
}


function messageSender(
  message: EmailMessage
): string {
  const record =
    messageRecord(message);

  return (
    recordString(
      record,
      "from_name"
    )
    ||
    recordString(
      record,
      "from_address"
    )
    ||
    "Unknown sender"
  );
}


function messageFromAddress(
  message: EmailMessage
): string {
  const record =
    messageRecord(message);

  return (
    recordString(
      record,
      "from_address"
    )
    ||
    "Unknown"
  );
}


function messageSubject(
  message: EmailMessage
): string {
  const record =
    messageRecord(message);

  return (
    recordString(
      record,
      "subject"
    )
    ||
    "No subject"
  );
}


function messageBody(
  message: EmailMessage
): string {
  const record =
    messageRecord(message);

  return (
    recordString(
      record,
      "body_text"
    )
    ||
    recordString(
      record,
      "snippet"
    )
    ||
    "No message content."
  );
}


function messageDateValue(
  message: EmailMessage
): string | null {
  const record =
    messageRecord(message);

  return (
    recordString(
      record,
      "occurred_at"
    )
    ||
    recordString(
      record,
      "created_at"
    )
  );
}


function messageId(
  message: EmailMessage
): string {
  const record =
    messageRecord(message);

  return (
    recordString(
      record,
      "id"
    )
    ||
    `${messageSubject(
      message
    )}-${messageDateValue(
      message
    )}`
  );
}


/* ============================================================
   Formatting
   ============================================================ */


function formatDate(
  value: string | null
): string {
  if (!value) {
    return "";
  }

  const date =
    new Date(value);

  if (
    Number.isNaN(
      date.getTime()
    )
  ) {
    return value;
  }

  return date.toLocaleString(
    undefined,
    {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }
  );
}


/* ============================================================
   Page
   ============================================================ */


export default function EmailPage() {
  const [
    threads,
    setThreads,
  ] = useState<
    EmailThread[]
  >([]);

  const [
    selectedThreadId,
    setSelectedThreadId,
  ] = useState<
    string | null
  >(null);

  const [
    messages,
    setMessages,
  ] = useState<
    EmailMessage[]
  >([]);

  const [
    search,
    setSearch,
  ] = useState("");

  const [
    composeOpen,
    setComposeOpen,
  ] = useState(false);

  const [
    loadingThreads,
    setLoadingThreads,
  ] = useState(true);

  const [
    loadingMessages,
    setLoadingMessages,
  ] = useState(false);

  const [
    threadError,
    setThreadError,
  ] = useState<
    string | null
  >(null);

  const [
    messageError,
    setMessageError,
  ] = useState<
    string | null
  >(null);


  /* ==========================================================
     Load inbox
     ========================================================== */


  const loadThreads =
    useCallback(
      async (
        preferredThreadId?: string
      ) => {
        try {
          setLoadingThreads(
            true
          );

          setThreadError(
            null
          );

          const result =
            await getEmailThreads({
              limit: 100,
            });

          setThreads(
            result
          );

          const availableIds =
            result
              .map(
                threadId
              )
              .filter(
                Boolean
              );

          if (
            preferredThreadId
            &&
            availableIds.includes(
              preferredThreadId
            )
          ) {
            setSelectedThreadId(
              preferredThreadId
            );

            return;
          }

          setSelectedThreadId(
            (
              current
            ) => {
              if (
                current
                &&
                availableIds.includes(
                  current
                )
              ) {
                return current;
              }

              return (
                availableIds[0]
                || null
              );
            }
          );
        } catch (error) {
          setThreadError(
            error instanceof Error
              ? error.message
              : "Unable to load email conversations."
          );
        } finally {
          setLoadingThreads(
            false
          );
        }
      },
      []
    );


  const loadMessages =
    useCallback(
      async (
        id: string
      ) => {
        try {
          setLoadingMessages(
            true
          );

          setMessageError(
            null
          );

          const result =
            await getThreadMessages(
              id
            );

          const sorted = [
            ...result,
          ].sort(
            (
              first,
              second
            ) => {
              const firstValue =
                messageDateValue(
                  first
                );

              const secondValue =
                messageDateValue(
                  second
                );

              const firstTime =
                firstValue
                  ? new Date(
                      firstValue
                    ).getTime()
                  : 0;

              const secondTime =
                secondValue
                  ? new Date(
                      secondValue
                    ).getTime()
                  : 0;

              return (
                firstTime
                -
                secondTime
              );
            }
          );

          setMessages(
            sorted
          );
        } catch (error) {
          setMessageError(
            error instanceof Error
              ? error.message
              : "Unable to load conversation messages."
          );

          setMessages([]);
        } finally {
          setLoadingMessages(
            false
          );
        }
      },
      []
    );


  useEffect(
    () => {
      void loadThreads();
    },
    [
      loadThreads,
    ]
  );


  useEffect(
    () => {
      if (
        !selectedThreadId
      ) {
        setMessages([]);

        return;
      }

      void loadMessages(
        selectedThreadId
      );
    },
    [
      selectedThreadId,
      loadMessages,
    ]
  );


  /* ==========================================================
     Derived state
     ========================================================== */


  const selectedThread =
    useMemo(
      () =>
        threads.find(
          (
            thread
          ) =>
            threadId(
              thread
            )
            ===
            selectedThreadId
        )
        || null,
      [
        threads,
        selectedThreadId,
      ]
    );


  const filteredThreads =
    useMemo(
      () => {
        const normalized =
          search
            .trim()
            .toLowerCase();

        if (!normalized) {
          return threads;
        }

        return threads.filter(
          (
            thread
          ) => {
            const searchable = [
              threadSubject(
                thread
              ),
              threadSnippet(
                thread
              ),
              threadProvider(
                thread
              ),
              threadParticipant(
                thread
              ),
            ]
              .filter(Boolean)
              .join(" ")
              .toLowerCase();

            return (
              searchable.includes(
                normalized
              )
            );
          }
        );
      },
      [
        threads,
        search,
      ]
    );


  const linkedCount =
    useMemo(
      () =>
        threads.filter(
          threadIsCrmLinked
        ).length,
      [
        threads,
      ]
    );


  const inboundCount =
    useMemo(
      () =>
        messages.filter(
          isInbound
        ).length,
      [
        messages,
      ]
    );


  const outboundCount =
    useMemo(
      () =>
        messages.filter(
          isOutbound
        ).length,
      [
        messages,
      ]
    );


  /* ==========================================================
     Compose / Reply callbacks
     ========================================================== */


  async function handleComposed(
    result: EmailSendResult
  ) {
    const newThreadId =
      threadId(
        result.thread
      );

    setComposeOpen(
      false
    );

    await loadThreads(
      newThreadId
    );

    if (newThreadId) {
      setSelectedThreadId(
        newThreadId
      );

      await loadMessages(
        newThreadId
      );
    }
  }


  async function handleReplied(
    result: EmailSendResult
  ) {
    const id =
      threadId(
        result.thread
      )
      ||
      selectedThreadId;

    if (!id) {
      return;
    }

    await loadMessages(
      id
    );

    await loadThreads(
      id
    );

    setSelectedThreadId(
      id
    );
  }


  /* ==========================================================
     Render
     ========================================================== */


  return (
    <div className="appShell">
      <Sidebar active="Email" />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <div className="pageHeading">
            <div>
              <p className="eyebrow">
                Engagement
              </p>

              <h1>
                Email
              </h1>

              <p>
                Manage customer email
                conversations directly
                inside AIVA CRM.
              </p>
            </div>

            <button
              type="button"
              className="createButton"
              onClick={() =>
                setComposeOpen(
                  true
                )
              }
            >
              <Plus size={16} />

              Compose
            </button>
          </div>


          {/* ===============================================
              Statistics
              =============================================== */}

          <div className="statsGrid">
            <div className="statCard">
              <div className="statLabel">
                Conversations
              </div>

              <div className="statValue">
                {threads.length}
              </div>

              <div className="statMeta">
                <Inbox
                  size={14}
                />

                CRM email threads
              </div>
            </div>

            <div className="statCard">
              <div className="statLabel">
                Linked to CRM
              </div>

              <div className="statValue">
                {linkedCount}
              </div>

              <div className="statMeta">
                <Building2
                  size={14}
                />

                Customer context
              </div>
            </div>

            <div className="statCard">
              <div className="statLabel">
                Selected Inbound
              </div>

              <div className="statValue">
                {inboundCount}
              </div>

              <div className="statMeta">
                <Mail
                  size={14}
                />

                Received messages
              </div>
            </div>

            <div className="statCard">
              <div className="statLabel">
                Selected Outbound
              </div>

              <div className="statValue">
                {outboundCount}
              </div>

              <div className="statMeta">
                <Send
                  size={14}
                />

                Sent messages
              </div>
            </div>
          </div>


          {/* ===============================================
              Email workspace
              =============================================== */}

          <section className="emailWorkspace">
            {/* ===========================================
                Inbox
                =========================================== */}

            <aside className="emailInboxPanel">
              <div className="emailInboxHeader">
                <div>
                  <h2>
                    Inbox
                  </h2>

                  <p>
                    {threads.length}{" "}
                    {threads.length === 1
                      ? "conversation"
                      : "conversations"}
                  </p>
                </div>
              </div>

              <div className="emailSearch">
                <Search
                  size={16}
                />

                <input
                  type="text"
                  value={search}
                  onChange={(
                    event
                  ) =>
                    setSearch(
                      event.target.value
                    )
                  }
                  placeholder="Search email..."
                />
              </div>

              {loadingThreads && (
                <div className="tableState">
                  Loading conversations...
                </div>
              )}

              {threadError && (
                <div className="tableState errorState">
                  <strong>
                    Email unavailable
                  </strong>

                  <span>
                    {threadError}
                  </span>
                </div>
              )}

              {!loadingThreads
                &&
                !threadError
                &&
                filteredThreads.length
                  === 0 && (
                  <div className="miniEmptyState">
                    <Mail
                      size={21}
                    />

                    <strong>
                      No conversations found
                    </strong>

                    <span>
                      Try changing your
                      search or compose
                      a new email.
                    </span>
                  </div>
                )}

              {!loadingThreads
                &&
                !threadError
                &&
                filteredThreads.length
                  > 0 && (
                  <div className="emailThreadList">
                    {filteredThreads.map(
                      (
                        thread
                      ) => {
                        const id =
                          threadId(
                            thread
                          );

                        const selected =
                          id
                          ===
                          selectedThreadId;

                        return (
                          <button
                            key={id}
                            type="button"
                            className={
                              `emailThreadItem${
                                selected
                                  ? " selected"
                                  : ""
                              }`
                            }
                            onClick={() =>
                              setSelectedThreadId(
                                id
                              )
                            }
                          >
                            <div className="emailThreadAvatar">
                              <UserRound
                                size={17}
                              />
                            </div>

                            <div className="emailThreadBody">
                              <div className="emailThreadTop">
                                <strong>
                                  {threadParticipant(
                                    thread
                                  )}
                                </strong>

                                <time>
                                  {formatDate(
                                    threadDateValue(
                                      thread
                                    )
                                  )}
                                </time>
                              </div>

                              <div className="emailThreadSubject">
                                {threadSubject(
                                  thread
                                )}
                              </div>

                              <p>
                                {threadSnippet(
                                  thread
                                )
                                  ||
                                  "No preview available."}
                              </p>

                              <div className="emailThreadBadges">
                                <span>
                                  {threadProvider(
                                    thread
                                  )}
                                </span>

                                {threadIsCrmLinked(
                                  thread
                                ) && (
                                  <span>
                                    CRM Linked
                                  </span>
                                )}
                              </div>
                            </div>
                          </button>
                        );
                      }
                    )}
                  </div>
                )}
            </aside>


            {/* ===========================================
                Conversation panel
                =========================================== */}

            <div className="emailThreadPanel">
              {!selectedThread && (
                <div className="miniEmptyState">
                  <Mail
                    size={24}
                  />

                  <strong>
                    Select a conversation
                  </strong>

                  <span>
                    Choose an email thread
                    from the inbox to view
                    its messages.
                  </span>
                </div>
              )}

              {selectedThread && (
                <>
                  <div className="emailConversationHeader">
                    <div>
                      <span className="eyebrow">
                        Conversation
                      </span>

                      <h2>
                        {threadSubject(
                          selectedThread
                        )}
                      </h2>

                      <p>
                        {threadParticipant(
                          selectedThread
                        )}
                      </p>
                    </div>

                    <div className="emailCrmBadges">
                      {threadHasAccount(
                        selectedThread
                      ) && (
                        <span>
                          Account Linked
                        </span>
                      )}

                      {threadHasContact(
                        selectedThread
                      ) && (
                        <span>
                          Contact Linked
                        </span>
                      )}

                      {threadHasLead(
                        selectedThread
                      ) && (
                        <span>
                          Lead Linked
                        </span>
                      )}

                      {threadHasOpportunity(
                        selectedThread
                      ) && (
                        <span>
                          Opportunity Linked
                        </span>
                      )}
                    </div>
                  </div>


                  {loadingMessages && (
                    <div className="tableState">
                      Loading messages...
                    </div>
                  )}

                  {messageError && (
                    <div className="tableState errorState">
                      <strong>
                        Conversation unavailable
                      </strong>

                      <span>
                        {messageError}
                      </span>
                    </div>
                  )}

                  {!loadingMessages
                    &&
                    !messageError
                    &&
                    messages.length
                      === 0 && (
                      <div className="miniEmptyState">
                        <Mail
                          size={21}
                        />

                        <strong>
                          No messages yet
                        </strong>

                        <span>
                          This email thread
                          does not contain
                          any messages.
                        </span>
                      </div>
                    )}

                  {!loadingMessages
                    &&
                    !messageError
                    &&
                    messages.length
                      > 0 && (
                      <div className="emailMessageList">
                        {messages.map(
                          (
                            message
                          ) => {
                            const inbound =
                              isInbound(
                                message
                              );

                            const outbound =
                              isOutbound(
                                message
                              );

                            return (
                              <article
                                key={messageId(
                                  message
                                )}
                                className={
                                  `emailMessageCard ${
                                    inbound
                                      ? "inbound"
                                      : outbound
                                        ? "outbound"
                                        : ""
                                  }`
                                }
                              >
                                <div className="emailMessageHeader">
                                  <div>
                                    <strong>
                                      {messageSender(
                                        message
                                      )}
                                    </strong>

                                    <span>
                                      From:{" "}
                                      {messageFromAddress(
                                        message
                                      )}
                                    </span>

                                    <span>
                                      To:{" "}
                                      {messageRecipientSummary(
                                        message
                                      )}
                                    </span>
                                  </div>

                                  <div className="emailMessageHeaderRight">
                                    <span
                                      className={
                                        inbound
                                          ? "activityDirectionPill"
                                          : "activityTypePill"
                                      }
                                    >
                                      {inbound
                                        ? "Inbound"
                                        : outbound
                                          ? "Outbound"
                                          : "Email"}
                                    </span>

                                    <time>
                                      {formatDate(
                                        messageDateValue(
                                          message
                                        )
                                      )}
                                    </time>
                                  </div>
                                </div>

                                <div className="emailMessageContent">
                                  <strong>
                                    {messageSubject(
                                      message
                                    )}
                                  </strong>

                                  <p>
                                    {messageBody(
                                      message
                                    )}
                                  </p>
                                </div>
                              </article>
                            );
                          }
                        )}
                      </div>
                    )}


                  {/* ===================================
                      Reply composer
                      =================================== */}

                  <ReplyComposer
                    key={
                      selectedThreadId
                      || "no-thread"
                    }
                    threadId={
                      selectedThreadId
                      || ""
                    }
                    fromAddress={
                      AIVA_FROM_ADDRESS
                    }
                    fromName={
                      AIVA_FROM_NAME
                    }
                    disabled={
                      loadingMessages
                      ||
                      !selectedThreadId
                    }
                    onReplied={
                      handleReplied
                    }
                  />
                </>
              )}
            </div>
          </section>
        </div>
      </main>


      {/* ===============================================
          Compose modal
          =============================================== */}

      <ComposeEmailModal
        open={
          composeOpen
        }
        fromAddress={
          AIVA_FROM_ADDRESS
        }
        fromName={
          AIVA_FROM_NAME
        }
        onClose={() =>
          setComposeOpen(
            false
          )
        }
        onSent={
          handleComposed
        }
      />
    </div>
  );
}