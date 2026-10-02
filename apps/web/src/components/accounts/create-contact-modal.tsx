"use client";

import {
  FormEvent,
  useState,
} from "react";

import {
  UserPlus,
  X,
} from "lucide-react";

import {
  createContact,
} from "@/lib/contacts";

import type {
  Contact,
} from "@/types/contact";


type Props = {
  accountId: string;
  open: boolean;
  onClose: () => void;
  onCreated: (
    contact: Contact
  ) => void;
};


export function CreateContactModal({
  accountId,
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

  const [jobTitle, setJobTitle] =
    useState("");

  const [department, setDepartment] =
    useState("");

  const [isPrimary, setIsPrimary] =
    useState(false);

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
      const contact =
        await createContact({
          account_id: accountId,

          first_name:
            firstName.trim(),

          last_name:
            lastName.trim(),

          email:
            email.trim() || null,

          phone:
            phone.trim() || null,

          job_title:
            jobTitle.trim() || null,

          department:
            department.trim() || null,

          is_primary: isPrimary,
        });

      onCreated(contact);

      setFirstName("");
      setLastName("");
      setEmail("");
      setPhone("");
      setJobTitle("");
      setDepartment("");
      setIsPrimary(false);

      onClose();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to create contact."
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
              <UserPlus size={18} />
            </div>

            <div>
              <h2>
                Add Contact
              </h2>

              <p>
                Add a person to this
                customer account.
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
                placeholder="Alice"
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
                placeholder="Morgan"
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
                placeholder="alice@company.com"
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
              Job title

              <input
                value={jobTitle}
                onChange={(event) =>
                  setJobTitle(
                    event.target.value
                  )
                }
                placeholder="Chief Technology Officer"
              />
            </label>

            <label>
              Department

              <input
                value={department}
                onChange={(event) =>
                  setDepartment(
                    event.target.value
                  )
                }
                placeholder="Technology"
              />
            </label>
          </div>

          <label className="checkboxLabel">
            <input
              type="checkbox"
              checked={isPrimary}
              onChange={(event) =>
                setIsPrimary(
                  event.target.checked
                )
              }
            />

            Primary contact
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
                ? "Adding..."
                : "Add Contact"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
