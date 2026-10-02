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
  CircleDollarSign,
  Handshake,
  Target,
} from "lucide-react";

import {
  useParams,
} from "next/navigation";

import {
  Sidebar,
} from "@/components/sidebar";

import {
  Topbar,
} from "@/components/topbar";

import {
  getAccount,
} from "@/lib/accounts";

import {
  getContact,
} from "@/lib/contacts";

import {
  getOpportunity,
  moveOpportunity,
} from "@/lib/opportunities";

import {
  getPipeline,
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
  ).format(
    Number(value)
  );
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

  const [moving, setMoving] =
    useState(false);

  const [error, setError] =
    useState<string | null>(null);


  useEffect(() => {
    if (!opportunityId) {
      return;
    }

    async function load() {
      try {
        setLoading(true);
        setError(null);

        const opportunityData =
          await getOpportunity(
            opportunityId
          );

        setOpportunity(
          opportunityData
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
  }, [opportunityId]);


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
    } catch (err) {
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
              <select
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
            </div>
          </section>

          {error && (
            <div className="formError">
              {error}
            </div>
          )}

          <div className="accountDetailGrid">
            <div className="accountMainColumn">
              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Deal Overview
                    </h2>

                    <p>
                      Commercial and pipeline
                      information for this opportunity.
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
                    DEAL DESCRIPTION
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

              <section className="detailPanel aiAccountCard">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      AIVA Deal Intelligence
                    </h2>

                    <p>
                      AI-assisted revenue intelligence.
                    </p>
                  </div>
                </div>

                <div className="aivaPlaceholder">
                  <Handshake size={20} />

                  <span>
                    AI COMING NEXT
                  </span>

                  <strong>
                    Deal risk, next-best action
                    and forecast intelligence.
                  </strong>

                  <p>
                    AIVA will analyze activities,
                    engagement and pipeline velocity
                    to recommend the next action.
                  </p>
                </div>
              </section>
            </aside>
          </div>
        </div>
      </main>
    </div>
  );
}
