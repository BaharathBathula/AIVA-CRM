"use client";

import {
  FormEvent,
  useEffect,
  useState,
} from "react";

import {
  Building2,
  GitBranch,
  X,
} from "lucide-react";

import {
  getAccounts,
  updateAccount,
} from "@/lib/accounts";

import type {
  Account,
  LifecycleStage,
} from "@/types/account";


type Props = {
  account: Account;
  open: boolean;

  onClose: () => void;

  onUpdated: (
    account: Account
  ) => void;
};


export function EditAccountModal({
  account,
  open,
  onClose,
  onUpdated,
}: Props) {
  const [
    name,
    setName,
  ] = useState("");

  const [
    domain,
    setDomain,
  ] = useState("");

  const [
    website,
    setWebsite,
  ] = useState("");

  const [
    industry,
    setIndustry,
  ] = useState("");

  const [
    phone,
    setPhone,
  ] = useState("");

  const [
    lifecycleStage,
    setLifecycleStage,
  ] = useState<LifecycleStage>(
    "prospect"
  );

  const [
    employeeCount,
    setEmployeeCount,
  ] = useState("");

  const [
    annualRevenue,
    setAnnualRevenue,
  ] = useState("");

  const [
    description,
    setDescription,
  ] = useState("");

  const [
    billingAddressLine1,
    setBillingAddressLine1,
  ] = useState("");

  const [
    billingAddressLine2,
    setBillingAddressLine2,
  ] = useState("");

  const [
    billingCity,
    setBillingCity,
  ] = useState("");

  const [
    billingState,
    setBillingState,
  ] = useState("");

  const [
    billingPostalCode,
    setBillingPostalCode,
  ] = useState("");

  const [
    billingCountry,
    setBillingCountry,
  ] = useState("");

  const [
    parentAccountId,
    setParentAccountId,
  ] = useState("");

  const [
    parentAccounts,
    setParentAccounts,
  ] = useState<Account[]>([]);

  const [
    loadingParents,
    setLoadingParents,
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


  useEffect(() => {
    if (!open) {
      return;
    }

    setName(
      account.name
    );

    setDomain(
      account.domain ?? ""
    );

    setWebsite(
      account.website ?? ""
    );

    setIndustry(
      account.industry ?? ""
    );

    setPhone(
      account.phone ?? ""
    );

    setLifecycleStage(
      account.lifecycle_stage
    );

    setEmployeeCount(
      account.employee_count
        !== null
        ? String(
            account.employee_count
          )
        : ""
    );

    setAnnualRevenue(
      account.annual_revenue
        ?? ""
    );

    setDescription(
      account.description ?? ""
    );

    setBillingAddressLine1(
      account.billing_address_line1
        ?? ""
    );

    setBillingAddressLine2(
      account.billing_address_line2
        ?? ""
    );

    setBillingCity(
      account.billing_city
        ?? ""
    );

    setBillingState(
      account.billing_state
        ?? ""
    );

    setBillingPostalCode(
      account.billing_postal_code
        ?? ""
    );

    setBillingCountry(
      account.billing_country
        ?? ""
    );

    setParentAccountId(
      account.parent_account_id
        ?? ""
    );

    setError(null);
  }, [
    account,
    open,
  ]);


  useEffect(() => {
    if (!open) {
      return;
    }

    let cancelled =
      false;

    async function loadParentAccounts() {
      try {
        setLoadingParents(
          true
        );

        const accounts =
          await getAccounts({
            limit: 100,
            includeArchived:
              false,
          });

        if (cancelled) {
          return;
        }

        setParentAccounts(
          accounts.filter(
            (
              candidate
            ) =>
              candidate.id
              !== account.id
          )
        );
      } catch {
        if (!cancelled) {
          setParentAccounts(
            []
          );
        }
      } finally {
        if (!cancelled) {
          setLoadingParents(
            false
          );
        }
      }
    }

    void loadParentAccounts();

    return () => {
      cancelled = true;
    };
  }, [
    account.id,
    open,
  ]);


  if (!open) {
    return null;
  }


  function handleLifecycleStageChange(
    value: string
  ) {
    switch (value) {
      case "prospect":
        setLifecycleStage(
          "prospect"
        );
        break;

      case "lead":
        setLifecycleStage(
          "lead"
        );
        break;

      case "customer":
        setLifecycleStage(
          "customer"
        );
        break;

      case "partner":
        setLifecycleStage(
          "partner"
        );
        break;

      case "inactive":
        setLifecycleStage(
          "inactive"
        );
        break;

      case "churned":
        setLifecycleStage(
          "churned"
        );
        break;

      default:
        setLifecycleStage(
          "prospect"
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

    const normalizedName =
      name.trim();

    if (!normalizedName) {
      setError(
        "Account name is required."
      );

      return;
    }


    const parsedEmployeeCount =
      employeeCount.trim()
        ? Number(
            employeeCount
          )
        : null;


    if (
      parsedEmployeeCount
      !== null
      &&
      (
        !Number.isInteger(
          parsedEmployeeCount
        )
        ||
        parsedEmployeeCount < 0
      )
    ) {
      setError(
        "Employee count must be a whole number of 0 or greater."
      );

      return;
    }


    const parsedRevenue =
      annualRevenue.trim()
        ? Number(
            annualRevenue
          )
        : null;


    if (
      parsedRevenue
      !== null
      &&
      (
        !Number.isFinite(
          parsedRevenue
        )
        ||
        parsedRevenue < 0
      )
    ) {
      setError(
        "Annual revenue must be 0 or greater."
      );

      return;
    }


    try {
      setSaving(true);
      setError(null);

      const originalParentId =
        account.parent_account_id
        ?? "";

      const parentChanged =
        parentAccountId
        !== originalParentId;


      const updatedAccount =
        await updateAccount(
          account.id,
          {
            name:
              normalizedName,

            domain:
              domain.trim()
              || null,

            website:
              website.trim()
              || null,

            industry:
              industry.trim()
              || null,

            phone:
              phone.trim()
              || null,

            lifecycle_stage:
              lifecycleStage,

            employee_count:
              parsedEmployeeCount,

            annual_revenue:
              parsedRevenue,

            description:
              description.trim()
              || null,

            billing_address_line1:
              billingAddressLine1
                .trim()
              || null,

            billing_address_line2:
              billingAddressLine2
                .trim()
              || null,

            billing_city:
              billingCity.trim()
              || null,

            billing_state:
              billingState.trim()
              || null,

            billing_postal_code:
              billingPostalCode
                .trim()
              || null,

            billing_country:
              billingCountry.trim()
              || null,

            ...(parentChanged
              ? {
                  parent_account_id:
                    parentAccountId
                    || null,
                }
              : {}),
          }
        );

      onUpdated(
        updatedAccount
      );

      onClose();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to update account."
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
              <Building2
                size={18}
              />
            </div>

            <div>
              <h2>
                Edit Account
              </h2>

              <p>
                Update company and
                relationship information.
              </p>
            </div>
          </div>

          <button
            type="button"
            className="modalClose"
            onClick={
              handleClose
            }
            aria-label="Close"
            disabled={saving}
          >
            <X size={18} />
          </button>
        </div>


        <form
          className="accountForm"
          onSubmit={submit}
        >
          <label>
            Account name

            <input
              required
              value={name}
              onChange={(event) =>
                setName(
                  event.target.value
                )
              }
            />
          </label>


          <div className="formGrid">
            <label>
              Lifecycle stage

              <select
                value={
                  lifecycleStage
                }
                onChange={(event) =>
                  handleLifecycleStageChange(
                    event.target.value
                  )
                }
              >
                <option value="prospect">
                  Prospect
                </option>

                <option value="lead">
                  Lead
                </option>

                <option value="customer">
                  Customer
                </option>

                <option value="partner">
                  Partner
                </option>

                <option value="inactive">
                  Inactive
                </option>

                <option value="churned">
                  Churned
                </option>
              </select>
            </label>


            <label>
              Industry

              <input
                value={industry}
                onChange={(event) =>
                  setIndustry(
                    event.target.value
                  )
                }
                placeholder="Technology"
              />
            </label>
          </div>


          <div className="formGrid">
            <label>
              Domain

              <input
                value={domain}
                onChange={(event) =>
                  setDomain(
                    event.target.value
                  )
                }
                placeholder="company.com"
              />
            </label>


            <label>
              Website

              <input
                value={website}
                onChange={(event) =>
                  setWebsite(
                    event.target.value
                  )
                }
                placeholder="https://company.com"
              />
            </label>
          </div>


          <label>
            Parent Account

            <select
              value={
                parentAccountId
              }
              disabled={
                loadingParents
                ||
                saving
              }
              onChange={(event) =>
                setParentAccountId(
                  event.target.value
                )
              }
            >
              <option value="">
                {loadingParents
                  ? "Loading accounts..."
                  : "No parent — top-level account"}
              </option>

              {parentAccounts.map(
                (
                  candidate
                ) => (
                  <option
                    key={
                      candidate.id
                    }
                    value={
                      candidate.id
                    }
                  >
                    {
                      candidate.name
                    }
                  </option>
                )
              )}
            </select>
          </label>


          <div
            style={{
              display:
                "flex",
              alignItems:
                "flex-start",
              gap:
                "8px",
              padding:
                "10px 12px",
              border:
                "1px solid var(--border)",
              borderRadius:
                "10px",
              color:
                "var(--muted)",
              fontSize:
                "10px",
              lineHeight:
                1.5,
            }}
          >
            <GitBranch
              size={15}
            />

            <span>
              Select the parent company
              when this account is a
              subsidiary, division or
              related business. An account
              cannot be its own parent.
            </span>
          </div>


          <label>
            Phone

            <input
              type="tel"
              value={phone}
              onChange={(event) =>
                setPhone(
                  event.target.value
                )
              }
              placeholder="+1 555 123 4567"
            />
          </label>


          <div className="formGrid">
            <label>
              Employees

              <input
                type="number"
                min="0"
                step="1"
                value={
                  employeeCount
                }
                onChange={(event) =>
                  setEmployeeCount(
                    event.target.value
                  )
                }
              />
            </label>


            <label>
              Annual revenue

              <input
                type="number"
                min="0"
                step="0.01"
                value={
                  annualRevenue
                }
                onChange={(event) =>
                  setAnnualRevenue(
                    event.target.value
                  )
                }
              />
            </label>
          </div>


          <label>
            Description

            <textarea
              rows={4}
              value={description}
              onChange={(event) =>
                setDescription(
                  event.target.value
                )
              }
              placeholder="Company overview and relationship notes..."
            />
          </label>


          <div>
            <strong
              style={{
                display:
                  "block",
                marginBottom:
                  "10px",
                fontSize:
                  "11px",
              }}
            >
              Billing Address
            </strong>

            <div
              style={{
                display:
                  "flex",
                flexDirection:
                  "column",
                gap:
                  "13px",
              }}
            >
              <label>
                Address line 1

                <input
                  value={
                    billingAddressLine1
                  }
                  onChange={(event) =>
                    setBillingAddressLine1(
                      event.target.value
                    )
                  }
                />
              </label>


              <label>
                Address line 2

                <input
                  value={
                    billingAddressLine2
                  }
                  onChange={(event) =>
                    setBillingAddressLine2(
                      event.target.value
                    )
                  }
                />
              </label>


              <div className="formGrid">
                <label>
                  City

                  <input
                    value={
                      billingCity
                    }
                    onChange={(event) =>
                      setBillingCity(
                        event.target.value
                      )
                    }
                  />
                </label>


                <label>
                  State / Region

                  <input
                    value={
                      billingState
                    }
                    onChange={(event) =>
                      setBillingState(
                        event.target.value
                      )
                    }
                  />
                </label>
              </div>


              <div className="formGrid">
                <label>
                  Postal code

                  <input
                    value={
                      billingPostalCode
                    }
                    onChange={(event) =>
                      setBillingPostalCode(
                        event.target.value
                      )
                    }
                  />
                </label>


                <label>
                  Country

                  <input
                    value={
                      billingCountry
                    }
                    onChange={(event) =>
                      setBillingCountry(
                        event.target.value
                      )
                    }
                  />
                </label>
              </div>
            </div>
          </div>


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
              disabled={saving}
            >
              Cancel
            </button>

            <button
              type="submit"
              className="createButton"
              disabled={
                saving
                ||
                !name.trim()
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