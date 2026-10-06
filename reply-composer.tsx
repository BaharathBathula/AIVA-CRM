cat > src/components/email/reply-composer.tsx <<'EOF'
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
  onReplied?: (
    result: EmailSendResult
  ) => void;
};


export function ReplyComposer({
  threadId,
  fromAddress,
  fromName,
  disabled = false,
  onReplied,
}: Props) {
  const [
    body,
    setBody,
  ] = useState("");

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


  async function sendReply() {
    if (!body.trim()) {
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
              body.trim(),

            body_html: null,

            from_address:
              fromAddress,

            from_name:
              fromName || null,

            cc_recipients: [],
            bcc_recipients: [],

            is_draft: false,
          }
        );

      setBody("");

      onReplied?.(result);
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

    void sendReply();
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
      </div>

      <form
        onSubmit={handleSubmit}
      >
        <textarea
          rows={6}
          value={body}
          disabled={
            disabled || sending
          }
          onChange={(event) =>
            setBody(
              event.target.value
            )
          }
          placeholder="Write your reply..."
        />

        {error && (
          <div className="formError">
            {error}
          </div>
        )}

        <div className="emailReplyActions">
          <button
            type="submit"
            className="createButton"
            disabled={
              disabled
              ||
              sending
              ||
              !body.trim()
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
EOF
