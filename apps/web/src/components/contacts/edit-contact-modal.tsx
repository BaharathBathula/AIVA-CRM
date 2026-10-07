"use client";

import {
  FormEvent,
  useEffect,
  useState,
} from "react";

import {
  Pencil,
  X,
} from "lucide-react";

import {
  getAccounts,
} from "@/lib/accounts";

import {
  updateContact,
} from "@/lib/contacts";

import type {
  Account,
} from "@/types/account";

import type {
  Contact,
} from "@/types/contact";


type Props = {
  contact: Contact;
  open: boolean;

  onClose: () => void;

  onUpdated: (
    contact: Contact
  ) => void;
};


export function EditContactModal({
  contact,
  open,
  onClose,
  onUpdated,
}: Props) {
  const [
    accounts,
    setAccounts,
  ] = useState<Account[]>([]);

  const [
    accountId,
    setAccountId,
  ] = useState("");

  const [
    firstName,
    setFirstName,
  ] = useState("");

  const [
    lastName,
    setLastName,
  ] = useState("");

  const [
    email,
    setEmail,
  ] = useState("");

  const [
    phone,
    setPhone,
  ] = useState("");

  const [
    mobile,
    setMobile,
  ] = useState("");

  const [
    jobTitle,
    setJobTitle,
  ] = useState("");

  const [
    department,
    setDepartment,
  ] = useState("");

  const [
    linkedinUrl,
    setLinkedinUrl,
  ] = useState("");

  const [
    isPrimary,
    setIsPrimary,
  ] = useState(false);

  const [
    isActive,
    setIsActive,
  ] = useState(true);

  const [
    loadingAccounts,
    setLoadingAccounts,
  ] = useState(false);

  const [
    saving,
    setSaving,
  ] = useState(false);

  const [
    error,
    setError,
  ] = useState<
    string | null
  >(null);


  useEffect(() => {
    if (!open) {
      return;
    }

    setAccountId(
      contact.account_id
      ?? ""
    );

    setFirstName(
      contact.first_name
    );

    setLastName(
      contact.last_name
    );

    setEmail(
      contact.email
      ?? ""
    );

    setPhone(
      contact.phone
      ?? ""
    );

    setMobile(
      contact.mobile
      ?? ""
    );

    setJobTitle(
      contact.job_title
      ?? ""
    );

    setDepartment(
      contact.department
      ?? ""
    );

    setLinkedinUrl(
      contact.linkedin_url
      ?? ""
    );

    setIsPrimary(
      contact.is_primary
    );

    setIsActive(
      contact.is_active
    );

    setError(null);
  }, [
    contact,
    open,
  ]);


  useEffect(() => {
    if (!open) {
      return;
    }

    let cancelled =
      false;

    async function loadAccounts() {
      try {
        setLoadingAccounts(
          true
        );

        const data =
          await getAccounts();

        if (!cancelled) {
          setAccounts(
            data
          );
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "Unable to load accounts."
          );
        }
      } finally {
        if (!cancelled) {
          setLoadingAccounts(
            false
          );
        }
      }
    }

    void loadAccounts();

    return () => {
      cancelled = true;
    };
  }, [
    open,
  ]);


  if (!open) {
    return null;
  }


  function handleAccountChange(
    value: string
  ) {
    setAccountId(
      value
    );

    if (!value) {
      setIsPrimary(
        false
      );
    }
  }


  function handleClose() {
    if (saving) {
      return;
    }

    setError(null);
    onClose();
  }


  async function submit(
    event: FormEvent
  ) {
    event.preventDefault();

    const normalizedFirstName =
      firstName.trim();

    const normalizedLastName =
      lastName.trim();

    if (
      !normalizedFirstName
      ||
      !normalizedLastName
    ) {
      setError(
        "First name and last name are required."
      );

      return;
    }

    if (
      isPrimary
      &&
      !accountId
    ) {
      setError(
        "A primary contact must belong to an account."
      );

      return;
    }

    try {
      setSaving(true);
      setError(null);

      const updatedContact =
        await updateContact(
          contact.id,
          {
            account_id:
              accountId
              || null,

            first_name:
              normalizedFirstName,

            last_name:
              normalizedLastName,

            email:
              email.trim()
              || null,

            phone:
              phone.trim()
              || null,

            mobile:
              mobile.trim()
              || null,

            job_title:
              jobTitle.trim()
              || null,

            department:
              department.trim()
              || null,

            linkedin_url:
              linkedinUrl.trim()
              || null,

            is_primary:
              isPrimary,

            is_active:
              isActive,
          }
        );

      onUpdated(
        updatedContact
      );

      onClose();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to update contact."
      );
    } finally {
      setSaving(false);
    }
  }


  return (
    <div className="modalBackdrop">
      <div
        className="modalCard"
        style={{
          maxHeight:
            "92vh",
          overflowY:
            "auto",
        }}
      >
        <div className="modalHeader">
          <div>
            <div className="modalTitleIcon">
              <Pencil
                size={18}
              />
            </div>

            <div>
              <h2>
                Edit Contact
              </h2>

              <p>
                Update contact and
                account relationship
                information.
              </p>
            </div>
          </div>

          <button
            type="button"
            className="modalClose"
            onClick={
              handleClose
            }
            disabled={
              saving
            }
            aria-label="Close"
          >
            <X
              size={18}
            />
          </button>
        </div>


        <form
          className="accountForm"
          onSubmit={
            submit
          }
        >
          <label>
            Account

            <select
              value={
                accountId
              }
              disabled={
                loadingAccounts
                ||
                saving
              }
              onChange={(
                event
              ) =>
                handleAccountChange(
                  event.target.value
                )
              }
            >
              <option value="">
                {loadingAccounts
                  ? "Loading accounts..."
                  : "No account"}
              </option>

              {accounts.map(
                (
                  account
                ) => (
                  <option
                    key={
                      account.id
                    }
                    value={
                      account.id
                    }
                  >
                    {
                      account.name
                    }
                  </option>
                )
              )}
            </select>
          </label>


          <div className="formGrid">
            <label>
              First name

              <input
                required
                value={
                  firstName
                }
                onChange={(
                  event
                ) =>
                  setFirstName(
                    event.target.value
                  )
                }
              />
            </label>


            <label>
              Last name

              <input
                required
                value={
                  lastName
                }
                onChange={(
                  event
                ) =>
                  setLastName(
                    event.target.value
                  )
                }
              />
            </label>
          </div>


          <label>
            Email

            <input
              type="email"
              value={
                email
              }
              onChange={(
                event
              ) =>
                setEmail(
                  event.target.value
                )
              }
            />
          </label>


          <div className="formGrid">
            <label>
              Phone

              <input
                type="tel"
                value={
                  phone
                }
                onChange={(
                  event
                ) =>
                  setPhone(
                    event.target.value
                  )
                }
              />
            </label>


            <label>
              Mobile

              <input
                type="tel"
                value={
                  mobile
                }
                onChange={(
                  event
                ) =>
                  setMobile(
                    event.target.value
                  )
                }
              />
            </label>
          </div>


          <div className="formGrid">
            <label>
              Job title

              <input
                value={
                  jobTitle
                }
                onChange={(
                  event
                ) =>
                  setJobTitle(
                    event.target.value
                  )
                }
              />
            </label>


            <label>
              Department

              <input
                value={
                  department
                }
                onChange={(
                  event
                ) =>
                  setDepartment(
                    event.target.value
                  )
                }
              />
            </label>
          </div>


          <label>
            LinkedIn

            <input
              type="url"
              value={
                linkedinUrl
              }
              onChange={(
                event
              ) =>
                  setLinkedinUrl(
                    event.target.value
                  )
              }
            />
          </label>


          <div className="formGrid">
            <label>
              Status

              <select
                value={
                  isActive
                    ? "active"
                    : "inactive"
                }
                onChange={(
                  event
                ) =>
                  setIsActive(
                    event.target.value
                    === "active"
                  )
                }
              >
                <option value="active">
                  Active
                </option>

                <option value="inactive">
                  Inactive
                </option>
              </select>
            </label>


            <label>
              Relationship

              <select
                value={
                  isPrimary
                    ? "primary"
                    : "standard"
                }
                disabled={
                  !accountId
                  ||
                  saving
                }
                onChange={(
                  event
                ) =>
                  setIsPrimary(
                    event.target.value
                    === "primary"
                  )
                }
              >
                <option value="standard">
                  Standard Contact
                </option>

                <option value="primary">
                  Primary Contact
                </option>
              </select>
            </label>
          </div>


          {!accountId && (
            <div
              style={{
                color:
                  "var(--muted)",
                fontSize:
                  "9px",
              }}
            >
              A contact must belong to
              an account before it can
              be marked as primary.
            </div>
          )}


          {error && (
            <div className="formError">
              {error}
            </div>
          )}


          <div className="modalFooter">
            <button
              type="button"
              className="secondaryButton"
              onClick={
                handleClose
              }
              disabled={
                saving
              }
            >
              Cancel
            </button>

            <button
              type="submit"
              className="createButton"
              disabled={
                saving
                ||
                !firstName.trim()
                ||
                !lastName.trim()
              }
            >
              {saving
                ? "Saving..."
                : "Save Changes"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}