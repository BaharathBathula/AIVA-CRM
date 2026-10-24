"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import Link from "next/link";

import {
  ArrowLeft,
  Building2,
  CalendarDays,
  CircleDollarSign,
  Contact as ContactIcon,
  Handshake,
  Target,
  Trophy,
} from "lucide-react";

import {
  useParams,
} from "next/navigation";

import {
  ActivityTimeline,
} from "@/components/activities/activity-timeline";
import { RecordDocuments, RecordMeetings, DealProjectButton } from "@/components/record-panels";
import { PinButton } from "@/components/pins";
import { RecordCloudFiles } from "@/components/integrations/record/RecordCloudFiles";
import { RecordSignatures } from "@/components/integrations/record/RecordSignatures";
import { OrderErp } from "@/components/integrations/record/RecordErp";
import { ShareToSlackButton } from "@/components/integrations/record/ShareToSlackButton";
import { ApprovalModal, RecordActions, RecordInsights, approvalFromError, type ApprovalNeeded } from "@/components/record-extras";

import {
  LogActivityModal,
} from "@/components/activities/log-activity-modal";

import {
  Sidebar,
} from "@/components/sidebar";

import {
  Topbar,
} from "@/components/topbar";

import {
  getOpportunityActivities,
} from "@/lib/activities";

import {
  getAccount,
} from "@/lib/accounts";

import {
  getContact,
} from "@/lib/contacts";

import {
  getOpportunity,
  moveOpportunity,
  updateOpportunity,
} from "@/lib/opportunities";

import {
  getPipeline,
  getPipelineStages,
} from "@/lib/pipelines";

import type {
  Activity,
} from "@/types/activity";

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


function formatMoney(
  value: string | null,
  currency: string
) {
  if (!value) {
    return "—";
  }

  return new Intl.NumberFormat(
    "en-US",
    {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }
  ).format(Number(value));
}


function formatDate(
  value: string | null
) {
  if (!value) {
    return "—";
  }

  return new Intl.DateTimeFormat(
    "en-US",
    {
      month: "short",
      day: "numeric",
      year: "numeric",
    }
  ).format(
    new Date(
      value.length === 10
        ? `${value}T00:00:00`
        : value
    )
  );
}


export default function OpportunityDetailPage() {
  const params = useParams<{
    opportunityId: string;
  }>();

  const opportunityId =
    params.opportunityId;

  const [
    opportunity,
    setOpportunity,
  ] = useState<Opportunity | null>(
    null
  );

  const [
    activities,
    setActivities,
  ] = useState<Activity[]>([]);

  const [account, setAccount] =
    useState<Account | null>(null);

  const [contact, setContact] =
    useState<Contact | null>(null);

  const [pipeline, setPipeline] =
    useState<Pipeline | null>(null);

  const [stages, setStages] =
    useState<PipelineStage[]>([]);

  const [loading, setLoading] =
    useState(true);

  // AIVA_O28_EDIT_FORM
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  const [editForm, setEditForm] = useState({
    name: "",
    description: "",
    amount: "",
    currency: "USD",
    probability: "0",
    expected_close_date: "",
    priority: "medium" as Opportunity["priority"],
    forecast_category: "pipeline" as Opportunity["forecast_category"],
    next_step: "",
  });

  const [moving, setMoving] =
    useState(false);

  const [error, setError] =
    useState<string | null>(null);

  const [
    activityModalOpen,
    setActivityModalOpen,
  ] = useState(false);


  const [extrasVersion, setExtrasVersion] = useState(0);
  const [approvalNeeded, setApprovalNeeded] = useState<ApprovalNeeded | null>(null);

  useEffect(() => {
    if (!opportunityId) {
      return;
    }

    async function load() {
      try {
        setLoading(true);
        setError(null);

        const [
          opportunityData,
          activityData,
        ] = await Promise.all([
          getOpportunity(
            opportunityId
          ),
          getOpportunityActivities(
            opportunityId
          ),
        ]);

        setOpportunity(
          opportunityData
        );

        setActivities(
          activityData
        );

        const [
          accountData,
          pipelineData,
          stageData,
        ] = await Promise.all([
          getAccount(
            opportunityData.account_id
          ),

          getPipeline(
            opportunityData.pipeline_id
          ),

          getPipelineStages(
            opportunityData.pipeline_id
          ),
        ]);

        setAccount(
          accountData
        );

        setPipeline(
          pipelineData
        );

        setStages(
          stageData
        );

        if (
          opportunityData.primary_contact_id
        ) {
          try {
            const contactData =
              await getContact(
                opportunityData
                  .primary_contact_id
              );

            setContact(
              contactData
            );
          } catch {
            setContact(null);
          }
        }
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Unable to load opportunity."
        );
      } finally {
        setLoading(false);
      }
    }

    load();
  }, [opportunityId, extrasVersion]);


  const currentStage =
    useMemo(() => {
      if (!opportunity) {
        return null;
      }

      return (
        stages.find(
          (stage) =>
            stage.id
            === opportunity.stage_id
        ) ?? null
      );
    }, [
      opportunity,
      stages,
    ]);


  function startEditing() {
    if (!opportunity) return;

    setEditForm({
      name: opportunity.name,
      description: opportunity.description ?? "",
      amount: opportunity.amount ?? "",
      currency: opportunity.currency,
      probability: String(opportunity.probability),
      expected_close_date: opportunity.expected_close_date?.slice(0, 10) ?? "",
      priority: opportunity.priority,
      forecast_category: opportunity.forecast_category,
      next_step: opportunity.next_step ?? "",
    });

    setError(null);
    setSaveMessage(null);
    setEditing(true);
  }

  function cancelEditing() {
    if (saving) return;
    setEditing(false);
    setError(null);
  }

  async function saveOpportunityChanges(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!opportunity || saving) return;

    const name = editForm.name.trim();
    const amount = editForm.amount.trim() === ""
      ? null
      : Number(editForm.amount);
    const probability = Number(editForm.probability);

    if (!name) {
      setError("Opportunity name is required.");
      return;
    }

    if (
      (amount !== null && (!Number.isFinite(amount) || amount < 0)) ||
      !Number.isFinite(probability) ||
      probability < 0 ||
      probability > 100
    ) {
      setError("Enter a valid amount and probability between 0 and 100.");
      return;
    }

    if (!/^[A-Za-z]{3}$/.test(editForm.currency.trim())) {
      setError("Enter a valid three-letter currency code.");
      return;
    }

    try {
      setSaving(true);
      setError(null);
      setSaveMessage(null);

      const updated = await updateOpportunity(opportunity.id, {
        name,
        description: editForm.description.trim() || null,
        amount,
        currency: editForm.currency.trim().toUpperCase(),
        probability,
        expected_close_date: editForm.expected_close_date || null,
        priority: editForm.priority,
        forecast_category: editForm.forecast_category,
        next_step: editForm.next_step.trim() || null,
      });

      setOpportunity(updated);
      setEditing(false);
      setSaveMessage("Opportunity updated successfully.");
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to update opportunity."
      );
    } finally {
      setSaving(false);
    }
  }

  async function changeStage(
    stageId: string
  ) {
    if (
      !opportunity ||
      stageId
      === opportunity.stage_id
    ) {
      return;
    }

    try {
      setMoving(true);
      setError(null);

      const updated =
        await moveOpportunity(
          opportunity.id,
          stageId
        );

      setOpportunity(
        updated
      );
      setExtrasVersion((v) => v + 1);
    } catch (err) {
      const needed = approvalFromError(err);
      if (needed) {
        setApprovalNeeded(needed);
        return;
      }
      setError(
        err instanceof Error
          ? err.message
          : "Unable to move opportunity."
      );
    } finally {
      setMoving(false);
    }
  }


  if (loading) {
    return (
      <div className="appShell">
        <Sidebar active="Opportunities" />

        <main className="mainArea">
          <Topbar />

          <div className="pageContent">
            <div className="detailLoading">
              Loading opportunity...
            </div>
          </div>
        </main>
      </div>
    );
  }


  if (
    error &&
    !opportunity
  ) {
    return (
      <div className="appShell">
        <Sidebar active="Opportunities" />

        <main className="mainArea">
          <Topbar />

          <div className="pageContent">
            <Link
              href="/opportunities"
              className="backLink"
            >
              <ArrowLeft size={15} />
              Opportunities
            </Link>

            <div className="detailError">
              <strong>
                Opportunity unavailable
              </strong>

              <p>
                {error}
              </p>
            </div>
          </div>
        </main>
      </div>
    );
  }


  if (!opportunity) {
    return null;
  }


  return (
    <div className="appShell">
      <Sidebar active="Opportunities" />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <Link
            href="/opportunities"
            className="backLink"
          >
            <ArrowLeft size={15} />
            Back to Opportunities
          </Link>

          <section className="accountHero">
            <div className="accountHeroIdentity">
              <div className="accountHeroLogo">
                {opportunity
                  .name[0]
                  .toUpperCase()}
              </div>

              <div>
                <div className="accountHeroTitle">
                  <h1>
                    {opportunity.name}
                  </h1>

                  <span className="stageBadge">
                    {currentStage?.name
                      ?? "Unknown Stage"}
                  </span>
                </div>

                <div className="accountHeroMeta">
                  {account && (
                    <span>
                      <Building2 size={14} />
                      {account.name}
                    </span>
                  )}

                  <span>
                    <CircleDollarSign
                      size={14}
                    />

                    {formatMoney(
                      opportunity.amount,
                      opportunity.currency
                    )}
                  </span>

                  <span>
                    <Target size={14} />

                    {
                      opportunity.probability
                    }
                    %
                  </span>
                </div>
              </div>
            </div>

            <div className="detailActions">
              <RecordActions objectType="opportunity" recordId={opportunity.id} call={{ name: opportunity.name, phone: null, opportunity_id: opportunity.id, account_id: opportunity.account_id }} onChanged={() => setExtrasVersion((v) => v + 1)} />
              <PinButton type="opportunity" id={opportunity.id} />
              <ShareToSlackButton recordType="opportunity" recordId={opportunity.id} className="secondaryButton" />
              <DealProjectButton opportunityId={opportunity.id} />
              <select
                className="detailStageSelect"
                aria-label="Stage"
                value={
                  opportunity.stage_id
                }
                disabled={moving}
                onChange={(event) =>
                  changeStage(
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
                      {stage.name}
                      {" — "}
                      {stage.probability}%
                    </option>
                  )
                )}
              </select>
              {!editing && (
                <Link
                  href={`/quotes/new?opportunity_id=${opportunity.id}`}
                  className="secondaryButton"
                >
                  Create quote
                </Link>
              )}
              {!editing && (
                <button
                  type="button"
                  className="primaryButton"
                  onClick={startEditing}
                  disabled={moving}
                >
                  Edit opportunity
                </button>
              )}
            </div>
          </section>

          {saveMessage && (
            <div role="status" style={{
              padding: 12,
              marginBottom: 16,
              borderRadius: 8,
              background: "var(--success-soft)",
              color: "var(--success)",
            }}>
              {saveMessage}
            </div>
          )}

          {editing && (
            <form
              className="aivaOpportunityEditForm"
              onSubmit={saveOpportunityChanges}
              style={{
                marginBottom: 24,
                padding: 24,
                border: "1px solid var(--border)",
                borderRadius: 12,
                background: "var(--surface)",
              }}
            >
              <h2 style={{ marginBottom: 18 }}>Edit Opportunity</h2>

              <div className="aivaOpportunityEditGrid">
                <label>
                  Opportunity Name *
                  <input
                    required
                    value={editForm.name}
                    onChange={event => setEditForm(current => ({
                      ...current, name: event.target.value
                    }))}
                  />
                </label>

                <label>
                  Amount
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={editForm.amount}
                    onChange={event => setEditForm(current => ({
                      ...current, amount: event.target.value
                    }))}
                  />
                </label>

                <label>
                  Currency
                  <input
                    maxLength={3}
                    required
                    value={editForm.currency}
                    onChange={event => setEditForm(current => ({
                      ...current, currency: event.target.value
                    }))}
                  />
                </label>

                <label>
                  Probability (%)
                  <input
                    type="number"
                    min="0"
                    max="100"
                    required
                    value={editForm.probability}
                    onChange={event => setEditForm(current => ({
                      ...current, probability: event.target.value
                    }))}
                  />
                </label>

                <label>
                  Expected Close Date
                  <input
                    type="date"
                    value={editForm.expected_close_date}
                    onChange={event => setEditForm(current => ({
                      ...current, expected_close_date: event.target.value
                    }))}
                  />
                </label>

                <label>
                  Priority
                  <select
                    value={editForm.priority}
                    onChange={event => setEditForm(current => ({
                      ...current,
                      priority: event.target.value as Opportunity["priority"]
                    }))}
                  >
                    {["low", "medium", "high", "critical"].map(value => (
                      <option key={value} value={value}>{value}</option>
                    ))}
                  </select>
                </label>

                <label>
                  Forecast Category
                  <select
                    value={editForm.forecast_category}
                    onChange={event => setEditForm(current => ({
                      ...current,
                      forecast_category:
                        event.target.value as Opportunity["forecast_category"]
                    }))}
                  >
                    {["pipeline", "best_case", "commit", "closed", "omitted"].map(value => (
                      <option key={value} value={value}>
                        {value.replaceAll("_", " ")}
                      </option>
                    ))}
                  </select>
                </label>

                <label>
                  Next Step
                  <input
                    value={editForm.next_step}
                    onChange={event => setEditForm(current => ({
                      ...current, next_step: event.target.value
                    }))}
                  />
                </label>
              </div>

              <label className="aivaOpportunityEditDescription">
                Description
                <textarea
                  rows={4}
                  value={editForm.description}
                  onChange={event => setEditForm(current => ({
                    ...current, description: event.target.value
                  }))}
                  style={{ width: "100%" }}
                />
              </label>

              <div className="aivaOpportunityEditActions">
                <button
                  type="button"
                  onClick={cancelEditing}
                  disabled={saving}
                >
                  Cancel
                </button>

                <button
                  type="submit"
                  className="primaryButton"
                  disabled={saving}
                >
                  {saving ? "Saving..." : "Save Changes"}
                </button>
              </div>
            </form>
          )}

          {error && (
            <div className="formError">
              {error}
            </div>
          )}

          <div className="accountDetailGrid">
            <div className="accountMainColumn">
              <ActivityTimeline
                activities={activities}
                onLogActivity={() =>
                  setActivityModalOpen(
                    true
                  )
                }
              />

              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Deal Overview
                    </h2>

                    <p>
                      Commercial and pipeline
                      information for this
                      opportunity.
                    </p>
                  </div>
                </div>

                <div className="detailFields">
                  <div>
                    <span>
                      Amount
                    </span>

                    <strong>
                      {formatMoney(
                        opportunity.amount,
                        opportunity.currency
                      )}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Probability
                    </span>

                    <strong>
                      {
                        opportunity.probability
                      }
                      %
                    </strong>
                  </div>

                  <div>
                    <span>
                      Expected Close
                    </span>

                    <strong>
                      {formatDate(
                        opportunity
                          .expected_close_date
                      )}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Currency
                    </span>

                    <strong>
                      {
                        opportunity.currency
                      }
                    </strong>
                  </div>

                  <div>
                    <span>
                      Pipeline
                    </span>

                    <strong>
                      {pipeline?.name
                        ?? "—"}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Stage
                    </span>

                    <strong>
                      {currentStage?.name
                        ?? "—"}
                    </strong>
                  </div>
                </div>

                <div className="aivaPlaceholder">
                  <span>
                    Deal description
                  </span>

                  <strong>
                    {opportunity.description
                      || "No deal description yet."}
                  </strong>
                </div>
              </section>

              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Pipeline Progress
                    </h2>

                    <p>
                      Move the opportunity through
                      the sales process.
                    </p>
                  </div>
                </div>

                <div className="detailFields">
                  {stages.map(
                    (stage) => (
                      <div
                        key={stage.id}
                      >
                        <span>
                          {stage.position}.
                          {" "}
                          {stage.name}
                        </span>

                        <strong>
                          {
                            stage.id
                            === opportunity.stage_id
                              ? "Current"
                              : `${stage.probability}%`
                          }
                        </strong>
                      </div>
                    )
                  )}
                </div>
              </section>
            </div>

            <aside className="accountSideColumn">
              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Account
                    </h2>

                    <p>
                      Customer account attached
                      to this opportunity.
                    </p>
                  </div>
                </div>

                <div className="detailFields">
                  <div>
                    <span>
                      Account Name
                    </span>

                    {account ? (
                      <Link
                        href={
                          `/accounts/${account.id}`
                        }
                      >
                        {account.name}
                      </Link>
                    ) : (
                      <strong>
                        —
                      </strong>
                    )}
                  </div>

                  <div>
                    <span>
                      Lifecycle
                    </span>

                    <strong>
                      {account
                        ?.lifecycle_stage
                        ?? "—"}
                    </strong>
                  </div>
                </div>
              </section>

              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Primary Contact
                    </h2>

                    <p>
                      Main contact associated
                      with this deal.
                    </p>
                  </div>
                </div>

                <div className="detailFields">
                  <div>
                    <span>
                      Contact
                    </span>

                    <strong>
                      {contact
                        ? `${contact.first_name} ${contact.last_name}`
                        : "—"}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Job Title
                    </span>

                    <strong>
                      {contact
                        ?.job_title
                        ?? "—"}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Email
                    </span>

                    <strong>
                      {contact
                        ?.email
                        ?? "—"}
                    </strong>
                  </div>
                </div>
              </section>

              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Deal Status
                    </h2>

                    <p>
                      Current lifecycle status.
                    </p>
                  </div>
                </div>

                <div className="detailFields">
                  <div>
                    <span>
                      Category
                    </span>

                    <strong>
                      {currentStage?.category
                        ?? "—"}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Closed At
                    </span>

                    <strong>
                      {formatDate(
                        opportunity.closed_at
                      )}
                    </strong>
                  </div>

                  <div>
                    <span>
                      Created
                    </span>

                    <strong>
                      {formatDate(
                        opportunity.created_at
                      )}
                    </strong>
                  </div>
                </div>
              </section>

              <RecordInsights objectType="opportunity" recordId={opportunity.id} version={extrasVersion} onChanged={() => setExtrasVersion((v) => v + 1)} />
              <RecordMeetings recordType="opportunity" recordId={opportunity.id} defaults={{ opportunity_id: opportunity.id, account_id: opportunity.account_id, contact_id: opportunity.primary_contact_id ?? null, title: opportunity.name }} />
              <RecordDocuments recordType="opportunity" recordId={opportunity.id} />
              <RecordCloudFiles recordType="opportunity" recordId={opportunity.id} />
              <RecordSignatures recordType="opportunity" recordId={opportunity.id} />
              <OrderErp recordType="opportunity" recordId={opportunity.id} />
            </aside>
          </div>
        </div>
      </main>
      <LogActivityModal
        open={activityModalOpen}
        opportunityId={
          opportunity.id
        }
        accountId={
          opportunity.account_id
        }
        contactId={
          opportunity.primary_contact_id
            ?? undefined
        }
        onClose={() =>
          setActivityModalOpen(
            false
          )
        }
        onCreated={(activity) =>
          setActivities(
            (current) => [
              activity,
              ...current,
            ]
          )
        }
      />
      <ApprovalModal
        needed={approvalNeeded}
        objectType="opportunity"
        recordId={opportunity.id}
        onClose={() => setApprovalNeeded(null)}
        onSubmitted={() => setExtrasVersion((v) => v + 1)}
      />
    </div>
  );
}
