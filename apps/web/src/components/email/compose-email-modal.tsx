"use client";

import {
  FormEvent,
  useEffect,
  useState,
} from "react";

import {
  Mail,
  X,
} from "lucide-react";

import {
  useSession,
} from "@/components/providers";

import {
  SnippetPicker,
} from "@/components/snippet-picker";

import {
  composeEmail,
} from "@/lib/emails";

import {
  api,
  useDebounced,
} from "@/lib/use-api";

import type {
  SearchGroup,
} from "@/lib/platform-types";

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


const MERGE_TOKEN =
  /\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*(?:\|\s*([^}]*?)\s*)?\}\}/g;


/**
 * Fills a snippet's merge fields when there's no CRM record to fill them
 * from: your own name, company and today's date are known; anything else
 * uses the field's fallback (``{{first_name|there}}``) or is left out.
 */
export function fillSnippetWithoutRecord(
  text: string,
  known: {
    senderName?: string | null;
    organizationName?: string | null;
  }
): string {
  const values: Record<string, string> = {
    sender_name:
      known.senderName ?? "",

    organization_name:
      known.organizationName ?? "",

    today:
      new Date().toLocaleDateString(
        "en-US",
        {
          month: "short",
          day: "2-digit",
          year: "numeric",
        }
      ),
  };

  return text.replace(
    MERGE_TOKEN,
    (_match, key: string, fallback?: string) =>
      values[key] || fallback || ""
  );
}


/** Adds inserted text after what's already in a message box. */
export function appendText(
  current: string,
  addition: string
): string {
  if (!current.trim()) {
    return addition;
  }

  return `${current.replace(/\s+$/, "")}\n\n${addition}`;
}


/** The contact whose email is the only "To" address, so snippets can fill in their details. */
function useContactForAddress(
  to: string
): string | null {
  const address =
    useDebounced(
      to.trim().toLowerCase(),
      400
    );

  const [
    contactId,
    setContactId,
  ] = useState<string | null>(
    null
  );

  useEffect(() => {
    const single =
      /^[^\s,@]+@[^\s,@]+\.[^\s,@]+$/.test(
        address
      );

    if (!single) {
      setContactId(null);

      return;
    }

    let active = true;

    api
      .get<{ groups: SearchGroup[] }>(
        `/search?q=${encodeURIComponent(address)}`
      )
      .then((result) => {
        if (!active) {
          return;
        }

        const contacts =
          result.groups.find(
            (group) =>
              group.type === "contact"
          );

        const match =
          contacts?.items.find(
            (item) =>
              (item.subtitle ?? "").toLowerCase()
              === address
          );

        setContactId(
          match?.id ?? null
        );
      })
      .catch(() => {
        if (active) {
          setContactId(null);
        }
      });

    return () => {
      active = false;
    };
  }, [address]);

  return contactId;
}


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

  const {
    session,
  } = useSession();

  const contactId =
    useContactForAddress(
      open ? to : ""
    );


  if (!open) {
    return null;
  }


  function insertSnippet(
    text: string
  ) {
    const filled =
      contactId
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

          <div className="composer-tools">
            <SnippetPicker
              onInsert={
                insertSnippet
              }
              recordType={
                contactId
                  ? "contact"
                  : undefined
              }
              recordId={
                contactId
                ?? undefined
              }
              disabled={saving}
            />
          </div>

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
