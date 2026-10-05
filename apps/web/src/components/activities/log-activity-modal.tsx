"use client";

import {
  FormEvent,
  useState,
} from "react";

import {
  Activity as ActivityIcon,
  X,
} from "lucide-react";

import {
  createActivity,
} from "@/lib/activities";

import type {
  Activity,
  ActivityDirection,
  ActivityType,
} from "@/types/activity";


type Props = {
  open: boolean;
  onClose: () => void;
  onCreated: (
    activity: Activity
  ) => void;
};


export function LogActivityModal({
  open,
  onClose,
  onCreated,
}: Props) {
  const [
    activityType,
    setActivityType,
  ] = useState<ActivityType>(
    "note"
  );

  const [
    direction,
    setDirection,
  ] = useState<
    ActivityDirection | ""
  >("");

  const [
    subject,
    setSubject,
  ] = useState("");

  const [
    body,
    setBody,
  ] = useState("");

  const [
    occurredAt,
    setOccurredAt,
  ] = useState("");

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


  async function submit(
    event: FormEvent
  ) {
    event.preventDefault();

    if (!subject.trim()) {
      return;
    }

    try {
      setSaving(true);
      setError(null);

      const activity =
        await createActivity({
          activity_type:
            activityType,

          subject:
            subject.trim(),

          body:
            body.trim() || null,

          direction:
            direction || null,

          occurred_at:
            occurredAt
              ? new Date(
                  occurredAt
                ).toISOString()
              : undefined,
        });

      onCreated(activity);

      setActivityType("note");
      setDirection("");
      setSubject("");
      setBody("");
      setOccurredAt("");

      onClose();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to log activity."
      );
    } finally {
      setSaving(false);
    }
  }


  function handleActivityTypeChange(
    value: string
  ) {
    setActivityType(
      value as ActivityType
    );
  }


  function handleDirectionChange(
    value: string
  ) {
    setDirection(
      value as
        | ActivityDirection
        | ""
    );
  }


  return (
    <div className="modalBackdrop">
      <div className="modalCard">
        <div className="modalHeader">
          <div>
            <div className="modalTitleIcon">
              <ActivityIcon
                size={18}
              />
            </div>

            <div>
              <h2>
                Log Activity
              </h2>

              <p>
                Record a CRM interaction
                or relationship event.
              </p>
            </div>
          </div>

          <button
            type="button"
            className="modalClose"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </div>

        <form
          className="accountForm"
          onSubmit={submit}
        >
          <div className="formGrid">
            <label>
              Activity type

              <select
                value={activityType}
                onChange={(event) =>
                  handleActivityTypeChange(
                    event.target.value
                  )
                }
              >
                <option value="note">
                  Note
                </option>

                <option value="call">
                  Call
                </option>

                <option value="email">
                  Email
                </option>

                <option value="meeting">
                  Meeting
                </option>

                <option value="sms">
                  SMS
                </option>

                <option value="document">
                  Document
                </option>
              </select>
            </label>

            <label>
              Direction

              <select
                value={direction}
                onChange={(event) =>
                  handleDirectionChange(
                    event.target.value
                  )
                }
              >
                <option value="">
                  Not applicable
                </option>

                <option value="inbound">
                  Inbound
                </option>

                <option value="outbound">
                  Outbound
                </option>
              </select>
            </label>
          </div>

          <label>
            Subject

            <input
              required
              value={subject}
              onChange={(event) =>
                setSubject(
                  event.target.value
                )
              }
              placeholder="Discovery call completed"
            />
          </label>

          <label>
            Details

            <textarea
              rows={5}
              value={body}
              onChange={(event) =>
                setBody(
                  event.target.value
                )
              }
              placeholder="Add notes about this interaction..."
            />
          </label>

          <label>
            Occurred at

            <input
              type="datetime-local"
              value={occurredAt}
              onChange={(event) =>
                setOccurredAt(
                  event.target.value
                )
              }
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
              onClick={onClose}
            >
              Cancel
            </button>

            <button
              type="submit"
              className="createButton"
              disabled={
                saving ||
                !subject.trim()
              }
            >
              {saving
                ? "Saving..."
                : "Log Activity"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
