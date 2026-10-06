"use client";

import {
  FormEvent,
  useState,
} from "react";

import {
  Building2,
  X,
} from "lucide-react";

import {
  createAccount,
} from "@/lib/accounts";

import type {
  Account,
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

    setError(null);
  }


  function handleClose() {
    if (saving) {
      return;
    }

    resetForm();
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
      parsedEmployeeCount !== null
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
      parsedRevenue !== null
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

      const account =
        await createAccount({
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
        });

      onCreated(account);

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


  return (
    <div className="modalBackdrop">
      <div
        className="modalCard"
        style={{
          maxHeight: "92vh",
          overflowY: "auto",
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
              autoFocus
              value={name}
              onChange={(event) =>
                setName(
                  event.target.value
                )
              }
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
                onChange={(event) =>
                  setLifecycleStage(
                    event.target.value
                      as LifecycleStage
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
                placeholder="northstar.com"
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
                placeholder="https://northstar.com"
              />
            </label>
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
                onChange={(event) =>
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
              value={description}
              onChange={(event) =>
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
                display: "block",
                marginBottom: "10px",
                fontSize: "11px",
              }}
            >
              Billing Address
            </strong>

            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "13px",
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
                  placeholder="100 Innovation Drive"
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
                    onChange={(event) =>
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
                    onChange={(event) =>
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
                    onChange={(event) =>
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
                    onChange={(event) =>
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
                ? "Creating..."
                : "Create Account"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
