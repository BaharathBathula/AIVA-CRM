"use client";

import {
  FormEvent,
  useState,
} from "react";

import {
  Target,
  X,
} from "lucide-react";

import {
  createLead,
} from "@/lib/leads";

import type {
  EditableLeadStatus,
  Lead,
} from "@/types/lead";


type Props = {
  open: boolean;

  onClose: () => void;

  onCreated: (
    lead: Lead
  ) => void;
};


export function CreateLeadModal({
  open,
  onClose,
  onCreated,
}: Props) {
  const [firstName, setFirstName] =
    useState("");

  const [lastName, setLastName] =
    useState("");

  const [email, setEmail] =
    useState("");

  const [phone, setPhone] =
    useState("");

  const [
    companyName,
    setCompanyName,
  ] = useState("");

  const [
    jobTitle,
    setJobTitle,
  ] = useState("");

  const [source, setSource] =
    useState("manual");

  const [status, setStatus] =
    useState<EditableLeadStatus>(
      "new"
    );

  const [score, setScore] =
    useState(0);

  const [notes, setNotes] =
    useState("");

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
      const lead =
        await createLead({
          first_name:
            firstName.trim(),

          last_name:
            lastName.trim(),

          email:
            email.trim() || null,

          phone:
            phone.trim() || null,

          company_name:
            companyName.trim() || null,

          job_title:
            jobTitle.trim() || null,

          source,

          status,

          score,

          notes:
            notes.trim() || null,
        });

      onCreated(lead);

      setFirstName("");
      setLastName("");
      setEmail("");
      setPhone("");
      setCompanyName("");
      setJobTitle("");
      setSource("manual");
      setStatus("new");
      setScore(0);
      setNotes("");

      onClose();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to create lead."
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
              <Target size={18} />
            </div>

            <div>
              <h2>
                New Lead
              </h2>

              <p>
                Capture a potential
                customer for qualification.
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
              First name

              <input
                required
                value={firstName}
                onChange={(event) =>
                  setFirstName(
                    event.target.value
                  )
                }
                placeholder="Sarah"
              />
            </label>

            <label>
              Last name

              <input
                required
                value={lastName}
                onChange={(event) =>
                  setLastName(
                    event.target.value
                  )
                }
                placeholder="Johnson"
              />
            </label>

            <label>
              Email

              <input
                type="email"
                value={email}
                onChange={(event) =>
                  setEmail(
                    event.target.value
                  )
                }
                placeholder="sarah@acme.com"
              />
            </label>

            <label>
              Phone

              <input
                value={phone}
                onChange={(event) =>
                  setPhone(
                    event.target.value
                  )
                }
                placeholder="+1 555 123 4567"
              />
            </label>

            <label>
              Company

              <input
                value={companyName}
                onChange={(event) =>
                  setCompanyName(
                    event.target.value
                  )
                }
                placeholder="Acme Corporation"
              />
            </label>

            <label>
              Job title

              <input
                value={jobTitle}
                onChange={(event) =>
                  setJobTitle(
                    event.target.value
                  )
                }
                placeholder="VP of Sales"
              />
            </label>

            <label>
              Lead source

              <select
                value={source}
                onChange={(event) =>
                  setSource(
                    event.target.value
                  )
                }
              >
                <option value="manual">
                  Manual
                </option>

                <option value="website">
                  Website
                </option>

                <option value="referral">
                  Referral
                </option>

                <option value="linkedin">
                  LinkedIn
                </option>

                <option value="event">
                  Event
                </option>

                <option value="import">
                  Import
                </option>
              </select>
            </label>

            <label>
              Status

              <select
                value={status}
                onChange={(event) =>
                  setStatus(
                    event.target.value as EditableLeadStatus
                  )
                }
              >
                <option value="new">
                  New
                </option>

                <option value="contacted">
                  Contacted
                </option>

                <option value="qualified">
                  Qualified
                </option>

                <option value="nurture">
                  Nurture
                </option>

                <option value="unqualified">
                  Unqualified
                </option>
              </select>
            </label>

            <label>
              Lead score

              <input
                type="number"
                min={0}
                max={100}
                value={score}
                onChange={(event) =>
                  setScore(
                    Number(
                      event.target.value
                    )
                  )
                }
              />
            </label>
          </div>

          <label>
            Notes

            <textarea
              rows={4}
              value={notes}
              onChange={(event) =>
                setNotes(
                  event.target.value
                )
              }
              placeholder="Add qualification notes..."
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
                !firstName.trim() ||
                !lastName.trim()
              }
            >
              {saving
                ? "Creating..."
                : "Create Lead"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
