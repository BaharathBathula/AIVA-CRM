"use client";

import {
  FormEvent,
  useState,
} from "react";

import {
  ArrowRight,
  X,
} from "lucide-react";

import {
  convertLead,
} from "@/lib/leads";

import type {
  Lead,
  LeadConvertResult,
} from "@/types/lead";


type Props = {
  lead: Lead;
  open: boolean;

  onClose: () => void;

  onConverted: (
    result: LeadConvertResult
  ) => void;
};


export function ConvertLeadModal({
  lead,
  open,
  onClose,
  onConverted,
}: Props) {
  const [
    opportunityName,
    setOpportunityName,
  ] = useState(
    `${lead.company_name ?? "New Customer"} - New Business`
  );

  const [
    opportunityAmount,
    setOpportunityAmount,
  ] = useState("");

  const [
    expectedCloseDate,
    setExpectedCloseDate,
  ] = useState("");

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
      const result =
        await convertLead(
          lead.id,
          {
            opportunity_name:
              opportunityName.trim()
                || null,

            opportunity_amount:
              opportunityAmount
                ? Number(
                    opportunityAmount
                  )
                : null,

            expected_close_date:
              expectedCloseDate
                || null,
          }
        );

      onConverted(result);
      onClose();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to convert lead."
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
              <ArrowRight size={18} />
            </div>

            <div>
              <h2>
                Convert Lead
              </h2>

              <p>
                Create the account,
                contact and opportunity
                in one transaction.
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
          <div className="detailFields">
            <div>
              <span>
                Lead
              </span>

              <strong>
                {lead.first_name}{" "}
                {lead.last_name}
              </strong>
            </div>

            <div>
              <span>
                Company
              </span>

              <strong>
                {lead.company_name
                  || "—"}
              </strong>
            </div>

            <div>
              <span>
                Current Status
              </span>

              <strong>
                {lead.status}
              </strong>
            </div>
          </div>

          <label>
            Opportunity name

            <input
              required
              value={opportunityName}
              onChange={(event) =>
                setOpportunityName(
                  event.target.value
                )
              }
            />
          </label>

          <div className="formGrid">
            <label>
              Opportunity amount

              <input
                type="number"
                min="0"
                step="0.01"
                value={opportunityAmount}
                onChange={(event) =>
                  setOpportunityAmount(
                    event.target.value
                  )
                }
                placeholder="50000"
              />
            </label>

            <label>
              Expected close date

              <input
                type="date"
                value={expectedCloseDate}
                onChange={(event) =>
                  setExpectedCloseDate(
                    event.target.value
                  )
                }
              />
            </label>
          </div>

          <div className="aivaPlaceholder">
            <span>
              ATOMIC CONVERSION
            </span>

            <strong>
              AIVA will create three CRM records.
            </strong>

            <p>
              Account + Contact + Opportunity.
              If any step fails, the entire
              conversion is rolled back.
            </p>
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
              onClick={onClose}
            >
              Cancel
            </button>

            <button
              type="submit"
              className="createButton"
              disabled={
                saving ||
                !opportunityName.trim()
              }
            >
              <ArrowRight size={15} />

              {saving
                ? "Converting..."
                : "Convert Lead"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
