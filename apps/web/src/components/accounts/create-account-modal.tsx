"use client";

import {
  FormEvent,
  useEffect,
  useState,
} from "react";

import {
  AlertTriangle,
  Building2,
  GitBranch,
  ShieldAlert,
  X,
} from "lucide-react";

import {
  checkAccountDuplicates,
  createAccount,
  getAccounts,
} from "@/lib/accounts";

import type {
  Account,
  AccountCreatePayload,
  AccountDuplicateCheckResponse,
  LifecycleStage,
} from "@/types/account";


type Props = {
  open: boolean;

  onClose: () => void;

  onCreated: (
    account: Account
  ) => void;
};


export function CreateAccountModal({
  open,
  onClose,
  onCreated,
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
    checkingDuplicates,
    setCheckingDuplicates,
  ] = useState(false);

  const [
    duplicateResult,
    setDuplicateResult,
  ] = useState<
    AccountDuplicateCheckResponse
    | null
  >(null);

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

    let cancelled =
      false;

    async function loadParentAccounts() {
      try {
        setLoadingParents(
          true
        );

        const result =
          await getAccounts({
            limit: 100,
            includeArchived:
              false,
          });

        if (!cancelled) {
          setParentAccounts(
            result
          );
        }
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
    open,
  ]);


  if (!open) {
    return null;
  }


  function clearDuplicateResult() {
    setDuplicateResult(
      null
    );
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


  function resetForm() {
    setName("");
    setDomain("");
    setWebsite("");
    setIndustry("");
    setPhone("");

    setLifecycleStage(
      "prospect"
    );

    setEmployeeCount("");
    setAnnualRevenue("");
    setDescription("");

    setBillingAddressLine1("");
    setBillingAddressLine2("");
    setBillingCity("");
    setBillingState("");
    setBillingPostalCode("");
    setBillingCountry("");

    setParentAccountId("");

    setDuplicateResult(
      null
    );

    setError(null);
  }


  function handleClose() {
    if (
      saving
      ||
      checkingDuplicates
    ) {
      return;
    }

    resetForm();
    onClose();
  }


  function buildPayload():
    AccountCreatePayload
    | null {
    const normalizedName =
      name.trim();

    if (!normalizedName) {
      setError(
        "Account name is required."
      );

      return null;
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
        parsedEmployeeCount
        < 0
      )
    ) {
      setError(
        "Employee count must be a whole number of 0 or greater."
      );

      return null;
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
        parsedRevenue
        < 0
      )
    ) {
      setError(
        "Annual revenue must be 0 or greater."
      );

      return null;
    }


    return {
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

      parent_account_id:
        parentAccountId
        || null,
    };
  }


  async function performCreate(
    payload:
      AccountCreatePayload
  ) {
    try {
      setSaving(true);
      setError(null);

      const account =
        await createAccount(
          payload
        );

      onCreated(
        account
      );

      resetForm();
      onClose();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to create account."
      );
    } finally {
      setSaving(false);
    }
  }


  async function submit(
    event: FormEvent
  ) {
    event.preventDefault();

    const payload =
      buildPayload();

    if (!payload) {
      return;
    }


    try {
      setCheckingDuplicates(
        true
      );

      setError(null);

      const result =
        await checkAccountDuplicates({
          name:
            payload.name,

          domain:
            payload.domain
            ?? null,
        });


      if (
        result.has_duplicates
      ) {
        setDuplicateResult(
          result
        );

        return;
      }


      setDuplicateResult(
        null
      );

      await performCreate(
        payload
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to check for duplicate accounts."
      );
    } finally {
      setCheckingDuplicates(
        false
      );
    }
  }


  async function createAnyway() {
    const payload =
      buildPayload();

    if (!payload) {
      return;
    }

    await performCreate(
      payload
    );
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
                New Account
              </h2>

              <p>
                Create a complete company
                profile in AIVA CRM.
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
            disabled={
              saving
              ||
              checkingDuplicates
            }
          >
            <X size={18} />
          </button>
        </div>


        <form
          className="accountForm"
          onSubmit={
            submit
          }
        >
          <label>
            Account name

            <input
              required
              autoFocus
              value={
                name
              }
              onChange={(
                event
              ) => {
                setName(
                  event.target.value
                );

                clearDuplicateResult();
              }}
              placeholder="Northstar Technologies"
            />
          </label>


          <div className="formGrid">
            <label>
              Lifecycle stage

              <select
                value={
                  lifecycleStage
                }
                onChange={(
                  event
                ) =>
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
                value={
                  industry
                }
                onChange={(
                  event
                ) =>
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
                value={
                  domain
                }
                onChange={(
                  event
                ) => {
                  setDomain(
                    event.target.value
                  );

                  clearDuplicateResult();
                }}
                placeholder="northstar.com"
              />
            </label>


            <label>
              Website

              <input
                value={
                  website
                }
                onChange={(
                  event
                ) =>
                  setWebsite(
                    event.target.value
                  )
                }
                placeholder="https://northstar.com"
              />
            </label>
          </div>


          <label>
            Parent Account

            <select
              value={
                parentAccountId
              }
              onChange={(
                event
              ) =>
                setParentAccountId(
                  event.target.value
                )
              }
              disabled={
                loadingParents
              }
            >
              <option value="">
                {loadingParents
                  ? "Loading accounts..."
                  : "No parent account"}
              </option>

              {parentAccounts.map(
                (
                  parent
                ) => (
                  <option
                    key={
                      parent.id
                    }
                    value={
                      parent.id
                    }
                  >
                    {
                      parent.name
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

              gap:
                "8px",

              alignItems:
                "flex-start",

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
              Use Parent Account for
              subsidiaries, divisions or
              related companies. Leave it
              empty for a top-level account.
            </span>
          </div>


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
                onChange={(
                  event
                ) =>
                  setEmployeeCount(
                    event.target.value
                  )
                }
                placeholder="250"
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
                onChange={(
                  event
                ) =>
                  setAnnualRevenue(
                    event.target.value
                  )
                }
                placeholder="25000000"
              />
            </label>
          </div>


          <label>
            Description

            <textarea
              rows={4}
              value={
                description
              }
              onChange={(
                event
              ) =>
                setDescription(
                  event.target.value
                )
              }
              placeholder="Company overview, relationship context, strategic notes..."
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
                  onChange={(
                    event
                  ) =>
                    setBillingAddressLine1(
                      event.target.value
                    )
                  }
                  placeholder="100 Innovation Drive"
                />
              </label>


              <label>
                Address line 2

                <input
                  value={
                    billingAddressLine2
                  }
                  onChange={(
                    event
                  ) =>
                    setBillingAddressLine2(
                      event.target.value
                    )
                  }
                  placeholder="Suite 500"
                />
              </label>


              <div className="formGrid">
                <label>
                  City

                  <input
                    value={
                      billingCity
                    }
                    onChange={(
                      event
                    ) =>
                      setBillingCity(
                        event.target.value
                      )
                    }
                    placeholder="Dallas"
                  />
                </label>


                <label>
                  State / Region

                  <input
                    value={
                      billingState
                    }
                    onChange={(
                      event
                    ) =>
                      setBillingState(
                        event.target.value
                      )
                    }
                    placeholder="Texas"
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
                    onChange={(
                      event
                    ) =>
                      setBillingPostalCode(
                        event.target.value
                      )
                    }
                    placeholder="75001"
                  />
                </label>


                <label>
                  Country

                  <input
                    value={
                      billingCountry
                    }
                    onChange={(
                      event
                    ) =>
                      setBillingCountry(
                        event.target.value
                      )
                    }
                    placeholder="United States"
                  />
                </label>
              </div>
            </div>
          </div>


          {duplicateResult
            ?.has_duplicates
            && (
            <div
              style={{
                border:
                  "1px solid #f59e0b",

                borderRadius:
                  "10px",

                padding:
                  "12px",

                background:
                  "rgba(245, 158, 11, 0.08)",
              }}
            >
              <div
                style={{
                  display:
                    "flex",

                  gap:
                    "8px",

                  alignItems:
                    "flex-start",
                }}
              >
                <ShieldAlert
                  size={18}
                />

                <div
                  style={{
                    flex:
                      1,
                  }}
                >
                  <strong>
                    Possible duplicate account
                  </strong>

                  <p
                    style={{
                      margin:
                        "4px 0 10px",

                      fontSize:
                        "10px",

                      lineHeight:
                        1.5,
                    }}
                  >
                    AIVA found an existing
                    account with matching
                    company information.
                    Review it before creating
                    another record.
                  </p>


                  <div
                    style={{
                      display:
                        "flex",

                      flexDirection:
                        "column",

                      gap:
                        "8px",
                    }}
                  >
                    {duplicateResult
                      .matches
                      .slice(
                        0,
                        5
                      )
                      .map(
                        (
                          match
                        ) => (
                          <div
                            key={
                              match
                                .account
                                .id
                            }
                            style={{
                              padding:
                                "9px",

                              border:
                                "1px solid var(--border)",

                              borderRadius:
                                "8px",

                              background:
                                "var(--surface)",
                            }}
                          >
                            <strong>
                              {
                                match
                                  .account
                                  .name
                              }
                            </strong>

                            <div
                              style={{
                                marginTop:
                                  "3px",

                                fontSize:
                                  "9px",

                                color:
                                  "var(--muted)",
                              }}
                            >
                              {match
                                .match_reasons
                                .map(
                                  (
                                    reason
                                  ) =>
                                    reason
                                      .replaceAll(
                                        "_",
                                        " "
                                      )
                                )
                                .join(
                                  " · "
                                )}
                              {" · "}
                              {
                                match.confidence
                              }{" "}
                              confidence

                              {match
                                .account
                                .is_archived
                                ? " · Archived"
                                : ""}
                            </div>
                          </div>
                        )
                      )}
                  </div>


                  <div
                    style={{
                      display:
                        "flex",

                      gap:
                        "8px",

                      marginTop:
                        "10px",
                    }}
                  >
                    <button
                      type="button"
                      className="secondaryButton"
                      onClick={() =>
                        setDuplicateResult(
                          null
                        )
                      }
                    >
                      Review Details
                    </button>

                    <button
                      type="button"
                      className="createButton"
                      onClick={() =>
                        void createAnyway()
                      }
                      disabled={
                        saving
                      }
                    >
                      Create Anyway
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}


          {error && (
            <div className="formError">
              <AlertTriangle
                size={14}
              />

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
                ||
                checkingDuplicates
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
                checkingDuplicates
                ||
                !name.trim()
              }
            >
              {saving
                ? "Creating..."
                : checkingDuplicates
                  ? "Checking..."
                  : "Create Account"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}