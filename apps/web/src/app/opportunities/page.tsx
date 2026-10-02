"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import Link from "next/link";

import {
  CircleDollarSign,
  Filter,
  Handshake,
  Plus,
  Search,
  Target,
  Trophy,
} from "lucide-react";

import {
  CreateOpportunityModal,
} from "@/components/opportunities/create-opportunity-modal";

import {
  Sidebar,
} from "@/components/sidebar";

import {
  Topbar,
} from "@/components/topbar";

import {
  getAccounts,
} from "@/lib/accounts";

import {
  getOpportunities,
} from "@/lib/opportunities";

import {
  getPipelines,
  getPipelineStages,
} from "@/lib/pipelines";

import type {
  Account,
} from "@/types/account";

import type {
  Opportunity,
} from "@/types/opportunity";

import type {
  Pipeline,
  PipelineStage,
  PipelineStageCategory,
} from "@/types/pipeline";


type FilterValue =
  | "all"
  | PipelineStageCategory;


const filters: FilterValue[] = [
  "all",
  "open",
  "won",
  "lost",
];


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
      `${value}T00:00:00`
    )
  );
}


function displayLabel(
  value: string
) {
  return value
    .replaceAll("_", " ")
    .replace(
      /\b\w/g,
      (letter) =>
        letter.toUpperCase()
    );
}


export default function OpportunitiesPage() {
  const [
    opportunities,
    setOpportunities,
  ] = useState<Opportunity[]>([]);

  const [accounts, setAccounts] =
    useState<Account[]>([]);

  const [pipelines, setPipelines] =
    useState<Pipeline[]>([]);

  const [stages, setStages] =
    useState<PipelineStage[]>([]);

  const [loading, setLoading] =
    useState(true);

  const [error, setError] =
    useState<string | null>(null);

  const [query, setQuery] =
    useState("");

  const [filter, setFilter] =
    useState<FilterValue>(
      "all"
    );

  const [
    createOpen,
    setCreateOpen,
  ] = useState(false);


  useEffect(() => {
    async function load() {
      try {
        setLoading(true);
        setError(null);

        const [
          opportunityData,
          accountData,
          pipelineData,
        ] = await Promise.all([
          getOpportunities(),
          getAccounts(),
          getPipelines(),
        ]);

        setOpportunities(
          opportunityData
        );

        setAccounts(
          accountData
        );

        setPipelines(
          pipelineData
        );

        const stageSets =
          await Promise.all(
            pipelineData.map(
              (pipeline) =>
                getPipelineStages(
                  pipeline.id
                )
            )
          );

        setStages(
          stageSets.flat()
        );
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Unable to load opportunities."
        );
      } finally {
        setLoading(false);
      }
    }

    load();
  }, []);


  const accountMap =
    useMemo(() => {
      return new Map(
        accounts.map(
          (account) => [
            account.id,
            account,
          ]
        )
      );
    }, [accounts]);


  const stageMap =
    useMemo(() => {
      return new Map(
        stages.map(
          (stage) => [
            stage.id,
            stage,
          ]
        )
      );
    }, [stages]);


  const filteredOpportunities =
    useMemo(() => {
      const normalized =
        query
          .trim()
          .toLowerCase();

      return opportunities.filter(
        (opportunity) => {
          const stage =
            stageMap.get(
              opportunity.stage_id
            );

          const account =
            accountMap.get(
              opportunity.account_id
            );

          const matchesFilter =
            filter === "all" ||
            stage?.category
            === filter;

          const searchable = [
            opportunity.name,
            account?.name,
            stage?.name,
            opportunity.description,
          ]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();

          const matchesSearch =
            !normalized ||
            searchable.includes(
              normalized
            );

          return (
            matchesFilter &&
            matchesSearch
          );
        }
      );
    }, [
      opportunities,
      query,
      filter,
      accountMap,
      stageMap,
    ]);


  const openOpportunities =
    opportunities.filter(
      (opportunity) =>
        stageMap.get(
          opportunity.stage_id
        )?.category === "open"
    );


  const totalPipelineValue =
    openOpportunities.reduce(
      (total, opportunity) =>
        total +
        Number(
          opportunity.amount ?? 0
        ),
      0
    );


  const weightedPipelineValue =
    openOpportunities.reduce(
      (total, opportunity) =>
        total +
        (
          Number(
            opportunity.amount ?? 0
          ) *
          opportunity.probability
        ) /
        100,
      0
    );


  const wonCount =
    opportunities.filter(
      (opportunity) =>
        stageMap.get(
          opportunity.stage_id
        )?.category === "won"
    ).length;


  function addCreatedOpportunity(
    opportunity: Opportunity
  ) {
    setOpportunities(
      (current) => [
        opportunity,
        ...current,
      ]
    );
  }


  return (
    <div className="appShell">
      <Sidebar active="Opportunities" />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <div className="pageHeading accountsHeading">
            <div>
              <p className="eyebrow">
                Sales
              </p>

              <h1>
                Opportunities
              </h1>

              <p>
                Manage active deals,
                pipeline value and sales
                progression.
              </p>
            </div>

            <button
              type="button"
              className="createButton"
              onClick={() =>
                setCreateOpen(true)
              }
            >
              <Plus size={16} />
              New Opportunity
            </button>
          </div>

          <div className="accountSummary">
            <div>
              <Handshake size={18} />

              <span>
                Open Deals
              </span>

              <strong>
                {
                  openOpportunities
                    .length
                }
              </strong>
            </div>

            <div>
              <CircleDollarSign
                size={18}
              />

              <span>
                Pipeline Value
              </span>

              <strong>
                {new Intl.NumberFormat(
                  "en-US",
                  {
                    style: "currency",
                    currency: "USD",
                    notation: "compact",
                    maximumFractionDigits: 1,
                  }
                ).format(
                  totalPipelineValue
                )}
              </strong>
            </div>

            <div>
              <Target size={18} />

              <span>
                Weighted Value
              </span>

              <strong>
                {new Intl.NumberFormat(
                  "en-US",
                  {
                    style: "currency",
                    currency: "USD",
                    notation: "compact",
                    maximumFractionDigits: 1,
                  }
                ).format(
                  weightedPipelineValue
                )}
              </strong>
            </div>

            <div>
              <Trophy size={18} />

              <span>
                Won Deals
              </span>

              <strong>
                {wonCount}
              </strong>
            </div>
          </div>

          <section className="accountsPanel">
            <div className="accountToolbar">
              <div className="accountSearch">
                <Search size={17} />

                <input
                  value={query}
                  onChange={(event) =>
                    setQuery(
                      event.target.value
                    )
                  }
                  placeholder="Search opportunities..."
                />
              </div>

              <div className="accountFilters">
                <Filter size={15} />

                {filters.map(
                  (item) => (
                    <button
                      key={item}
                      type="button"
                      className={
                        filter === item
                          ? "filterChip active"
                          : "filterChip"
                      }
                      onClick={() =>
                        setFilter(item)
                      }
                    >
                      {displayLabel(item)}
                    </button>
                  )
                )}
              </div>
            </div>

            {loading && (
              <div className="tableState">
                Loading opportunities...
              </div>
            )}

            {error && !loading && (
              <div className="tableState errorState">
                <strong>
                  Opportunities unavailable
                </strong>

                <span>
                  {error}
                </span>
              </div>
            )}

            {!loading &&
              !error &&
              filteredOpportunities
                .length === 0 && (
                <div className="emptyState">
                  <div className="emptyStateIcon">
                    <Handshake
                      size={23}
                    />
                  </div>

                  <h3>
                    No opportunities found
                  </h3>

                  <p>
                    Create your first sales
                    opportunity in AIVA CRM.
                  </p>

                  <button
                    type="button"
                    className="createButton"
                    onClick={() =>
                      setCreateOpen(
                        true
                      )
                    }
                  >
                    <Plus size={15} />
                    New Opportunity
                  </button>
                </div>
              )}

            {!loading &&
              !error &&
              filteredOpportunities
                .length > 0 && (
                <div className="accountTableWrapper">
                  <table className="accountTable">
                    <thead>
                      <tr>
                        <th>
                          Opportunity
                        </th>

                        <th>
                          Account
                        </th>

                        <th>
                          Stage
                        </th>

                        <th>
                          Amount
                        </th>

                        <th>
                          Probability
                        </th>

                        <th>
                          Close Date
                        </th>
                      </tr>
                    </thead>

                    <tbody>
                      {filteredOpportunities.map(
                        (opportunity) => {
                          const account =
                            accountMap.get(
                              opportunity.account_id
                            );

                          const stage =
                            stageMap.get(
                              opportunity.stage_id
                            );

                          return (
                            <tr
                              key={
                                opportunity.id
                              }
                            >
                              <td>
                                <Link
                                  href={
                                    `/opportunities/${opportunity.id}`
                                  }
                                  className="accountIdentity"
                                >
                                  <div className="accountLogo">
                                    {opportunity
                                      .name[0]
                                      .toUpperCase()}
                                  </div>

                                  <div>
                                    <strong>
                                      {
                                        opportunity.name
                                      }
                                    </strong>

                                    <span>
                                      {
                                        opportunity.currency
                                      }
                                    </span>
                                  </div>
                                </Link>
                              </td>

                              <td>
                                {account
                                  ?.name
                                  ?? "—"}
                              </td>

                              <td>
                                <span className="stageBadge">
                                  {stage
                                    ?.name
                                    ?? "Unknown"}
                                </span>
                              </td>

                              <td>
                                <strong>
                                  {formatMoney(
                                    opportunity.amount,
                                    opportunity.currency
                                  )}
                                </strong>
                              </td>

                              <td>
                                {
                                  opportunity.probability
                                }
                                %
                              </td>

                              <td>
                                {formatDate(
                                  opportunity.expected_close_date
                                )}
                              </td>
                            </tr>
                          );
                        }
                      )}
                    </tbody>
                  </table>
                </div>
              )}
          </section>
        </div>
      </main>

      <CreateOpportunityModal
        open={createOpen}
        onClose={() =>
          setCreateOpen(false)
        }
        onCreated={
          addCreatedOpportunity
        }
      />
    </div>
  );
}
