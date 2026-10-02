"use client";

import {
  FormEvent,
  useEffect,
  useState,
} from "react";

import {
  Handshake,
  X,
} from "lucide-react";

import {
  getAccounts,
} from "@/lib/accounts";

import {
  getContacts,
} from "@/lib/contacts";

import {
  createOpportunity,
} from "@/lib/opportunities";

import {
  getPipelines,
  getPipelineStages,
} from "@/lib/pipelines";

import type {
  Account,
} from "@/types/account";

import type {
  Contact,
} from "@/types/contact";

import type {
  Opportunity,
} from "@/types/opportunity";

import type {
  Pipeline,
  PipelineStage,
} from "@/types/pipeline";


type Props = {
  open: boolean;
  onClose: () => void;
  onCreated: (
    opportunity: Opportunity
  ) => void;
};


export function CreateOpportunityModal({
  open,
  onClose,
  onCreated,
}: Props) {
  const [accounts, setAccounts] =
    useState<Account[]>([]);

  const [contacts, setContacts] =
    useState<Contact[]>([]);

  const [pipelines, setPipelines] =
    useState<Pipeline[]>([]);

  const [stages, setStages] =
    useState<PipelineStage[]>([]);

  const [name, setName] =
    useState("");

  const [accountId, setAccountId] =
    useState("");

  const [
    primaryContactId,
    setPrimaryContactId,
  ] = useState("");

  const [pipelineId, setPipelineId] =
    useState("");

  const [stageId, setStageId] =
    useState("");

  const [amount, setAmount] =
    useState("");

  const [currency, setCurrency] =
    useState("USD");

  const [
    expectedCloseDate,
    setExpectedCloseDate,
  ] = useState("");

  const [description, setDescription] =
    useState("");

  const [loading, setLoading] =
    useState(false);

  const [saving, setSaving] =
    useState(false);

  const [error, setError] =
    useState<string | null>(null);


  useEffect(() => {
    if (!open) {
      return;
    }

    async function loadReferenceData() {
      try {
        setLoading(true);
        setError(null);

        const [
          accountData,
          pipelineData,
        ] = await Promise.all([
          getAccounts(),
          getPipelines(),
        ]);

        setAccounts(accountData);
        setPipelines(pipelineData);

        const defaultPipeline =
          pipelineData.find(
            (pipeline) =>
              pipeline.is_default
          ) ?? pipelineData[0];

        if (defaultPipeline) {
          setPipelineId(
            defaultPipeline.id
          );

          const stageData =
            await getPipelineStages(
              defaultPipeline.id
            );

          setStages(stageData);

          const firstOpenStage =
            stageData.find(
              (stage) =>
                stage.category
                === "open"
            ) ?? stageData[0];

          setStageId(
            firstOpenStage?.id ?? ""
          );
        }
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Unable to load opportunity data."
        );
      } finally {
        setLoading(false);
      }
    }

    loadReferenceData();
  }, [open]);


  useEffect(() => {
    if (!accountId) {
      setContacts([]);
      setPrimaryContactId("");
      return;
    }

    async function loadContacts() {
      try {
        const data =
          await getContacts(
            accountId
          );

        setContacts(data);
        setPrimaryContactId("");
      } catch {
        setContacts([]);
      }
    }

    loadContacts();
  }, [accountId]);


  async function changePipeline(
    nextPipelineId: string
  ) {
    setPipelineId(
      nextPipelineId
    );

    setStageId("");

    if (!nextPipelineId) {
      setStages([]);
      return;
    }

    try {
      const stageData =
        await getPipelineStages(
          nextPipelineId
        );

      setStages(stageData);

      const firstOpenStage =
        stageData.find(
          (stage) =>
            stage.category
            === "open"
        ) ?? stageData[0];

      setStageId(
        firstOpenStage?.id ?? ""
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to load pipeline stages."
      );
    }
  }


  function resetForm() {
    setName("");
    setAccountId("");
    setPrimaryContactId("");
    setAmount("");
    setCurrency("USD");
    setExpectedCloseDate("");
    setDescription("");
  }


  async function submit(
    event: FormEvent
  ) {
    event.preventDefault();

    if (
      !name.trim() ||
      !accountId
    ) {
      return;
    }

    try {
      setSaving(true);
      setError(null);

      const opportunity =
        await createOpportunity({
          name: name.trim(),
          account_id: accountId,

          primary_contact_id:
            primaryContactId
            || null,

          pipeline_id:
            pipelineId
            || null,

          stage_id:
            stageId
            || null,

          amount:
            amount
              ? Number(amount)
              : null,

          currency:
            currency
              .trim()
              .toUpperCase(),

          expected_close_date:
            expectedCloseDate
            || null,

          description:
            description.trim()
            || null,
        });

      onCreated(
        opportunity
      );

      resetForm();

      onClose();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to create opportunity."
      );
    } finally {
      setSaving(false);
    }
  }


  if (!open) {
    return null;
  }


  return (
    <div className="modalBackdrop">
      <div className="modalCard">
        <div className="modalHeader">
          <div>
            <div className="modalTitleIcon">
              <Handshake size={18} />
            </div>

            <div>
              <h2>
                New Opportunity
              </h2>

              <p>
                Create a new sales opportunity
                and place it in the pipeline.
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
            Opportunity name

            <input
              required
              value={name}
              onChange={(event) =>
                setName(
                  event.target.value
                )
              }
              placeholder="Acme Corporation - Expansion"
            />
          </label>

          <div className="formGrid">
            <label>
              Account

              <select
                required
                disabled={loading}
                value={accountId}
                onChange={(event) =>
                  setAccountId(
                    event.target.value
                  )
                }
              >
                <option value="">
                  Select account
                </option>

                {accounts.map(
                  (account) => (
                    <option
                      key={account.id}
                      value={account.id}
                    >
                      {account.name}
                    </option>
                  )
                )}
              </select>
            </label>

            <label>
              Primary contact

              <select
                disabled={!accountId}
                value={primaryContactId}
                onChange={(event) =>
                  setPrimaryContactId(
                    event.target.value
                  )
                }
              >
                <option value="">
                  No primary contact
                </option>

                {contacts.map(
                  (contact) => (
                    <option
                      key={contact.id}
                      value={contact.id}
                    >
                      {contact.first_name}{" "}
                      {contact.last_name}
                    </option>
                  )
                )}
              </select>
            </label>

            <label>
              Pipeline

              <select
                value={pipelineId}
                onChange={(event) =>
                  changePipeline(
                    event.target.value
                  )
                }
              >
                {pipelines.map(
                  (pipeline) => (
                    <option
                      key={pipeline.id}
                      value={pipeline.id}
                    >
                      {pipeline.name}
                    </option>
                  )
                )}
              </select>
            </label>

            <label>
              Stage

              <select
                value={stageId}
                onChange={(event) =>
                  setStageId(
                    event.target.value
                  )
                }
              >
                {stages.map(
                  (stage) => (
                    <option
                      key={stage.id}
                      value={stage.id}
                    >
                      {stage.name} —{" "}
                      {stage.probability}%
                    </option>
                  )
                )}
              </select>
            </label>

            <label>
              Amount

              <input
                min="0"
                step="0.01"
                type="number"
                value={amount}
                onChange={(event) =>
                  setAmount(
                    event.target.value
                  )
                }
                placeholder="25000"
              />
            </label>

            <label>
              Currency

              <input
                maxLength={3}
                value={currency}
                onChange={(event) =>
                  setCurrency(
                    event.target.value
                  )
                }
                placeholder="USD"
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

          <label>
            Description

            <textarea
              value={description}
              onChange={(event) =>
                setDescription(
                  event.target.value
                )
              }
              placeholder="Describe the deal, requirements, next steps or commercial context."
              rows={4}
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
                loading ||
                !name.trim() ||
                !accountId
              }
            >
              {saving
                ? "Creating..."
                : "Create Opportunity"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
