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
  const [name, setName] =
    useState("");

  const [domain, setDomain] =
    useState("");

  const [industry, setIndustry] =
    useState("");

  const [website, setWebsite] =
    useState("");

  const [phone, setPhone] =
    useState("");

  const [
    lifecycleStage,
    setLifecycleStage,
  ] = useState<LifecycleStage>(
    "prospect"
  );

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
      const account =
        await createAccount({
          name,
          domain:
            domain.trim() || null,
          website:
            website.trim() || null,
          industry:
            industry.trim() || null,
          phone:
            phone.trim() || null,
          lifecycle_stage:
            lifecycleStage,
        });

      onCreated(account);

      setName("");
      setDomain("");
      setWebsite("");
      setIndustry("");
      setPhone("");
      setLifecycleStage(
        "prospect"
      );

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
      <div className="modalCard">
        <div className="modalHeader">
          <div>
            <div className="modalTitleIcon">
              <Building2 size={18} />
            </div>

            <div>
              <h2>
                New Account
              </h2>

              <p>
                Add a company or organization
                to AIVA CRM.
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
              placeholder="Northstar Technologies"
            />
          </label>

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
          </div>

          <label>
            Lifecycle stage

            <select
              value={lifecycleStage}
              onChange={(event) =>
                setLifecycleStage(
                  event.target
                    .value as LifecycleStage
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
            </select>
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
              disabled={
                saving ||
                !name.trim()
              }
              type="submit"
              className="createButton"
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
