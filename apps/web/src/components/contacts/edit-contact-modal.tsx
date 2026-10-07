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
  getOrganizationMembers,
} from "@/lib/organization-members";

import {
  checkContactDuplicates,
  updateContact,
} from "@/lib/contacts";

import type {
  Account,
} from "@/types/account";

import type {
  Contact,
} from "@/types/contact";

import type {
  OrganizationMember,
} from "@/types/organization-member";


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
    ownerUserId,
    setOwnerUserId,
  ] = useState("");

  const [
    organizationMembers,
    setOrganizationMembers,
  ] = useState<OrganizationMember[]>([]);

  const [
    loadingMembers,
    setLoadingMembers,
  ] = useState(false);

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
    segment,
    setSegment,
  ] = useState("");

  const [
    tagsText,
    setTagsText,
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
    duplicateMatches,
    setDuplicateMatches,
  ] = useState<
    Array<{
      id: string;
      name: string;
      email: string | null;
      confidence: string;
      reasons: string[];
    }>
  >([]);

  const [
    duplicateConfirmed,
    setDuplicateConfirmed,
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


    setOwnerUserId(
      contact.owner_user_id
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


    setSegment(
      contact.segment
      ?? ""
    );

    setTagsText(
      contact.tags.join(
        ", "
      )
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

    setDuplicateMatches([]);
    setDuplicateConfirmed(false);

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


  useEffect(() => {
    if (!open) {
      return;
    }

    let cancelled =
      false;

    async function loadOrganizationMembers() {
      try {
        setLoadingMembers(
          true
        );

        const members =
          await getOrganizationMembers();

        if (!cancelled) {
          setOrganizationMembers(
            members
          );
        }
      } catch {
        if (!cancelled) {
          setOrganizationMembers(
            []
          );
        }
      } finally {
        if (!cancelled) {
          setLoadingMembers(
            false
          );
        }
      }
    }

    void loadOrganizationMembers();

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

      if (!duplicateConfirmed) {
        const duplicateResult =
          await checkContactDuplicates({
            firstName:
              normalizedFirstName,
            lastName:
              normalizedLastName,
            email,
            phone,
            mobile,
            accountId:
              accountId || undefined,
            excludeContactId:
              contact.id,
          });

        if (
          duplicateResult
            .has_duplicates
        ) {
          setDuplicateMatches(
            duplicateResult.matches.map(
              (
                match
              ) => ({
                id:
                  match.contact.id,

                name:
                  `${match.contact.first_name} ${match.contact.last_name}`,

                email:
                  match.contact.email,

                confidence:
                  match.confidence,

                reasons:
                  match.reasons,
              })
            )
          );

          setDuplicateConfirmed(
            true
          );

          setSaving(false);

          return;
        }
      }

      const updatedContact =
        await updateContact(
          contact.id,
          {
            account_id:
              accountId
              || null,

            owner_user_id:
              ownerUserId
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


            segment:
              segment.trim()
              || null,

            tags:
              tagsText
                .split(",")
                .map(
                  (
                    tag
                  ) =>
                    tag.trim()
                )
                .filter(
                  Boolean
                ),

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


          <label>
            Contact Owner

            <select
              value={
                ownerUserId
              }
              disabled={
                loadingMembers
                ||
                saving
              }
              onChange={(
                event
              ) =>
                setOwnerUserId(
                  event.target.value
                )
              }
            >
              <option value="">
                {loadingMembers
                  ? "Loading users..."
                  : "Unassigned"}
              </option>

              {organizationMembers.map(
                (
                  member
                ) => (
                  <option
                    key={
                      member.user_id
                    }
                    value={
                      member.user_id
                    }
                  >
                    {
                      member.full_name
                    }{" "}
                    —{" "}
                    {
                      member.email
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
            Contact Segment

            <select
              value={
                segment
              }
              onChange={(
                event
              ) =>
                setSegment(
                  event.target.value
                )
              }
            >
              <option value="">
                Unsegmented
              </option>

              {segment
                &&
                ![
                  "VIP",
                  "Decision Maker",
                  "Champion",
                  "Influencer",
                  "Customer",
                  "Partner",
                  "Prospect",
                  "Vendor",
                ].includes(
                  segment
                ) && (
                  <option
                    value={
                      segment
                    }
                  >
                    {
                      segment
                    }
                  </option>
                )}

              <option value="VIP">
                VIP
              </option>

              <option value="Decision Maker">
                Decision Maker
              </option>

              <option value="Champion">
                Champion
              </option>

              <option value="Influencer">
                Influencer
              </option>

              <option value="Customer">
                Customer
              </option>

              <option value="Partner">
                Partner
              </option>

              <option value="Prospect">
                Prospect
              </option>

              <option value="Vendor">
                Vendor
              </option>
            </select>
          </label>


          <label>
            Tags

            <input
              value={
                tagsText
              }
              onChange={(
                event
              ) =>
                setTagsText(
                  event.target.value
                )
              }
              placeholder="Executive, High Value, Renewal"
            />

            <span
              style={{
                color:
                  "var(--muted)",
                fontSize:
                  "8px",
                fontWeight:
                  400,
              }}
            >
              Separate multiple tags with commas.
            </span>
          </label>


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


          {duplicateMatches.length > 0 && (
            <div
              style={{
                padding: "12px",
                border:
                  "1px solid #f0d69a",
                borderRadius:
                  "10px",
                background:
                  "#fff9e8",
              }}
            >
              <strong
                style={{
                  display:
                    "block",
                  marginBottom:
                    "7px",
                  fontSize:
                    "10px",
                }}
              >
                Possible Duplicate Contact
              </strong>

              {duplicateMatches.map(
                (
                  match
                ) => (
                  <div
                    key={
                      match.id
                    }
                    style={{
                      marginTop:
                        "7px",
                      padding:
                        "9px",
                      border:
                        "1px solid #eadfbf",
                      borderRadius:
                        "8px",
                      background:
                        "white",
                    }}
                  >
                    <strong
                      style={{
                        display:
                          "block",
                        fontSize:
                          "10px",
                      }}
                    >
                      {
                        match.name
                      }
                    </strong>

                    {match.email && (
                      <span
                        style={{
                          display:
                            "block",
                          marginTop:
                            "3px",
                          color:
                            "var(--muted)",
                          fontSize:
                            "9px",
                        }}
                      >
                        {
                          match.email
                        }
                      </span>
                    )}

                    <span
                      style={{
                        display:
                          "block",
                        marginTop:
                          "4px",
                        color:
                          "#8a6700",
                        fontSize:
                          "8px",
                        fontWeight:
                          700,
                      }}
                    >
                      {
                        match.confidence
                          .toUpperCase()
                      }{" "}
                      CONFIDENCE
                    </span>

                    <span
                      style={{
                        display:
                          "block",
                        marginTop:
                          "3px",
                        color:
                          "var(--muted)",
                        fontSize:
                          "8px",
                      }}
                    >
                      {
                        match.reasons.join(
                          " · "
                        )
                      }
                    </span>
                  </div>
                )
              )}

              <p
                style={{
                  margin:
                    "9px 0 0",
                  fontSize:
                    "9px",
                  color:
                    "#775b00",
                }}
              >
                Submit again only if this
                update is intentional.
              </p>
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
                : duplicateMatches.length > 0
                  ? "Save Anyway"
                  : "Save Changes"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}