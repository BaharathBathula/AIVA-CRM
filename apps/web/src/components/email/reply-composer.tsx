"use client";

import {
  FormEvent,
  useState,
} from "react";

import {
  Reply,
  Send,
} from "lucide-react";

import {
  appendText,
  fillSnippetWithoutRecord,
} from "@/components/email/compose-email-modal";

import {
  useSession,
} from "@/components/providers";

import {
  SnippetPicker,
} from "@/components/snippet-picker";

import {
  replyToEmailThread,
} from "@/lib/emails";

import type {
  EmailSendResult,
} from "@/types/email";


type Props = {
  threadId: string;

  fromAddress: string;
  fromName?: string;

  disabled?: boolean;

  /** The lead or contact the thread is with, so snippets fill in their details. */
  recordType?: "lead" | "contact";
  recordId?: string;

  onReplied?: (
    result: EmailSendResult
  ) => void;
};


function parseRecipients(
  value: string
): Array<Record<string, unknown>> {
  return value
    .split(",")
    .map((item) =>
      item.trim()
    )
    .filter(Boolean)
    .map((email) => ({
      email,
    }));
}


export function ReplyComposer({
  threadId,
  fromAddress,
  fromName,
  disabled = false,
  recordType,
  recordId,
  onReplied,
}: Props) {
  const {
    session,
  } = useSession();

  const [
    body,
    setBody,
  ] = useState("");

  const [
    cc,
    setCc,
  ] = useState("");

  const [
    bcc,
    setBcc,
  ] = useState("");

  const [
    showCcBcc,
    setShowCcBcc,
  ] = useState(false);

  const [
    sending,
    setSending,
  ] = useState(false);

  const [
    error,
    setError,
  ] = useState<string | null>(
    null
  );


  function insertSnippet(
    text: string
  ) {
    const filled =
      recordType && recordId
        ? text
        : fillSnippetWithoutRecord(
            text,
            {
              senderName:
                fromName
                || session?.user.full_name,

              organizationName:
                session?.organization.name,
            }
          );

    setBody((current) =>
      appendText(
        current,
        filled
      )
    );
  }


  function resetComposer() {
    setBody("");
    setCc("");
    setBcc("");
    setShowCcBcc(false);
    setError(null);
  }


  async function sendReply(
    isDraft: boolean
  ) {
    if (
      !isDraft
      &&
      !body.trim()
    ) {
      setError(
        "Reply message cannot be empty."
      );

      return;
    }

    try {
      setSending(true);
      setError(null);

      const result =
        await replyToEmailThread(
          threadId,
          {
            body_text:
              body.trim()
              || null,

            body_html: null,

            from_address:
              fromAddress,

            from_name:
              fromName
              || null,

            cc_recipients:
              parseRecipients(
                cc
              ),

            bcc_recipients:
              parseRecipients(
                bcc
              ),

            is_draft:
              isDraft,
          }
        );

      onReplied?.(
        result
      );

      resetComposer();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to send reply."
      );
    } finally {
      setSending(false);
    }
  }


  function handleSubmit(
    event: FormEvent
  ) {
    event.preventDefault();

    void sendReply(
      false
    );
  }


  return (
    <div className="emailReplyComposer">
      <div className="emailReplyComposerHeader">
        <div>
          <Reply size={16} />

          <strong>
            Reply
          </strong>
        </div>

        {!showCcBcc && (
          <button
            type="button"
            className="emailReplyLinkButton"
            disabled={
              sending
              || disabled
            }
            onClick={() =>
              setShowCcBcc(
                true
              )
            }
          >
            CC / BCC
          </button>
        )}
      </div>

      <form
        onSubmit={
          handleSubmit
        }
      >
        {showCcBcc && (
          <div className="emailReplyRecipientGrid">
            <label>
              CC

              <input
                type="text"
                value={cc}
                disabled={
                  sending
                  || disabled
                }
                onChange={(
                  event
                ) =>
                  setCc(
                    event.target.value
                  )
                }
                placeholder="cc@example.com"
              />
            </label>

            <label>
              BCC

              <input
                type="text"
                value={bcc}
                disabled={
                  sending
                  || disabled
                }
                onChange={(
                  event
                ) =>
                  setBcc(
                    event.target.value
                  )
                }
                placeholder="bcc@example.com"
              />
            </label>
          </div>
        )}

        <textarea
          rows={6}
          value={body}
          disabled={
            sending
            || disabled
          }
          onChange={(
            event
          ) =>
            setBody(
              event.target.value
            )
          }
          placeholder="Write your reply..."
        />

        <div className="composer-tools">
          <SnippetPicker
            onInsert={
              insertSnippet
            }
            recordType={recordType}
            recordId={recordId}
            disabled={
              sending
              || disabled
            }
          />
        </div>

        {error && (
          <div className="formError">
            {error}
          </div>
        )}

        <div className="emailReplyActions">
          <button
            type="button"
            className="secondaryButton"
            disabled={
              sending
              || disabled
            }
            onClick={() =>
              void sendReply(
                true
              )
            }
          >
            {sending
              ? "Saving..."
              : "Save Draft"}
          </button>

          <button
            type="submit"
            className="createButton"
            disabled={
              sending
              || disabled
              || !body.trim()
            }
          >
            <Send size={15} />

            {sending
              ? "Sending..."
              : "Send Reply"}
          </button>
        </div>
      </form>
    </div>
  );
}
