"use client";

import {
  FormEvent,
  useState,
} from "react";

import {
  Mail,
  X,
} from "lucide-react";

import {
  composeEmail,
} from "@/lib/emails";

import type {
  EmailSendResult,
} from "@/types/email";


type Props = {
  open: boolean;

  fromAddress: string;
  fromName?: string;

  onClose: () => void;

  onSent?: (
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


export function ComposeEmailModal({
  open,
  fromAddress,
  fromName,
  onClose,
  onSent,
}: Props) {
  const [
    to,
    setTo,
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
    subject,
    setSubject,
  ] = useState("");

  const [
    body,
    setBody,
  ] = useState("");

  const [
    showCcBcc,
    setShowCcBcc,
  ] = useState(false);

  const [
    saving,
    setSaving,
  ] = useState(false);

  const [
    error,
    setError,
  ] = useState<string | null>(
    null
  );


  if (!open) {
    return null;
  }


  function resetForm() {
    setTo("");
    setCc("");
    setBcc("");
    setSubject("");
    setBody("");
    setError(null);
    setShowCcBcc(false);
  }


  function closeModal() {
    if (saving) {
      return;
    }

    resetForm();
    onClose();
  }


  async function submitEmail(
    isDraft: boolean
  ) {
    if (
      !isDraft
      &&
      !to.trim()
    ) {
      setError(
        "At least one recipient is required."
      );

      return;
    }

    if (!subject.trim()) {
      setError(
        "Subject is required."
      );

      return;
    }

    if (
      !isDraft
      &&
      !body.trim()
    ) {
      setError(
        "Email message cannot be empty."
      );

      return;
    }

    try {
      setSaving(true);
      setError(null);

      const result =
        await composeEmail({
          to_recipients:
            parseRecipients(to),

          cc_recipients:
            parseRecipients(cc),

          bcc_recipients:
            parseRecipients(bcc),

          subject:
            subject.trim(),

          body_text:
            body.trim()
            || null,

          from_address:
            fromAddress,

          from_name:
            fromName
            || null,

          is_draft:
            isDraft,
        });

      onSent?.(
        result
      );

      resetForm();
      onClose();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to send email."
      );
    } finally {
      setSaving(false);
    }
  }


  function handleSubmit(
    event: FormEvent
  ) {
    event.preventDefault();

    void submitEmail(
      false
    );
  }


  return (
    <div className="modalBackdrop">
      <div className="modalCard">
        <div className="modalHeader">
          <div>
            <div className="modalTitleIcon">
              <Mail size={18} />
            </div>

            <div>
              <h2>
                Compose Email
              </h2>

              <p>
                Send an email directly
                from AIVA CRM.
              </p>
            </div>
          </div>

          <button
            type="button"
            className="modalClose"
            onClick={
              closeModal
            }
            aria-label="Close compose email"
          >
            <X size={18} />
          </button>
        </div>

        <form
          className="accountForm"
          onSubmit={
            handleSubmit
          }
        >
          <label>
            To

            <input
              type="text"
              value={to}
              onChange={(
                event
              ) =>
                setTo(
                  event.target.value
                )
              }
              placeholder="customer@example.com"
              autoFocus
            />
          </label>

          {!showCcBcc && (
            <button
              type="button"
              className="secondaryButton"
              onClick={() =>
                setShowCcBcc(
                  true
                )
              }
            >
              Add CC / BCC
            </button>
          )}

          {showCcBcc && (
            <div className="formGrid">
              <label>
                CC

                <input
                  type="text"
                  value={cc}
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

          <label>
            Subject

            <input
              type="text"
              value={subject}
              onChange={(
                event
              ) =>
                setSubject(
                  event.target.value
                )
              }
              placeholder="Enter subject"
            />
          </label>

          <label>
            Message

            <textarea
              rows={9}
              value={body}
              onChange={(
                event
              ) =>
                setBody(
                  event.target.value
                )
              }
              placeholder="Write your message..."
            />
          </label>

          {error && (
            <div className="formError">
              {error}
            </div>
          )}

          <div className="modalFooter">
            <button
              type="button"
              className="secondaryButton"
              disabled={saving}
              onClick={() =>
                void submitEmail(
                  true
                )
              }
            >
              {saving
                ? "Saving..."
                : "Save Draft"}
            </button>

            <button
              type="button"
              className="secondaryButton"
              disabled={saving}
              onClick={
                closeModal
              }
            >
              Cancel
            </button>

            <button
              type="submit"
              className="createButton"
              disabled={saving}
            >
              {saving
                ? "Sending..."
                : "Send Email"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
