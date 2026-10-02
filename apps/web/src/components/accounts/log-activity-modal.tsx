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
  accountId: string;
  open: boolean;
  onClose: () => void;

  onCreated: (
    activity: Activity
  ) => void;
};


export function LogActivityModal({
  accountId,
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

  const [subject, setSubject] =
    useState("");

  const [body, setBody] =
    useState("");

  const [
    direction,
    setDirection,
  ] = useState<
    ActivityDirection | ""
  >("");

  const [saving, setSaving] =
    useState(false);

  const [error, setError] =
    useState<string | null>(null);


  if (!open) {
    return null;
  }


  async function submit(
    event: FormEvent
  ) {
    event.preventDefault();

    setSaving(true);
    setError(null);

    try {
      const activity =
        await createActivity({
          account_id: accountId,
          activity_type:
            activityType,
          subject:
            subject.trim(),
          body:
            body.trim() || null,
          direction:
            direction || null,
        });

      onCreated(activity);

      setActivityType("note");
      setSubject("");
      setBody("");
      setDirection("");

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


  return (
    <div className="modalBackdrop">
      <div className="modalCard">
        <div className="modalHeader">
          <div>
            <div className="modalTitleIcon">
              <ActivityIcon size={18} />
            </div>

            <div>
              <h2>
                Log Activity
              </h2>

              <p>
                Add a customer interaction
                to the timeline.
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
                  setActivityType(
                    event.target
                      .value as ActivityType
                  )
                }
              >
                <option value="note">
                  Note
                </option>

                <option value="email">
                  Email
                </option>

                <option value="call">
                  Call
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
                  setDirection(
                    event.target.value as ActivityDirection | ""
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
              placeholder="Add notes about this customer interaction..."
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
