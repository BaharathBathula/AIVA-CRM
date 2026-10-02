"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import type {
  DragEvent,
} from "react";

import Link from "next/link";

import {
  Building2,
  CircleDollarSign,
  GripVertical,
  Loader2,
  Plus,
  RefreshCcw,
  SquareKanban,
  Target,
} from "lucide-react";

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
  moveOpportunity,
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
} from "@/types/pipeline";


function formatMoney(
  value: number
) {
  return new Intl.NumberFormat(
    "en-US",
    {
      style: "currency",
      currency: "USD",
      notation: "compact",
      maximumFractionDigits: 1,
    }
  ).format(value);
}


function formatOpportunityMoney(
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
    return "No close date";
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


export default function PipelinePage() {
  const [pipelines, setPipelines] =
    useState<Pipeline[]>([]);

  const [
    selectedPipelineId,
    setSelectedPipelineId,
  ] = useState("");

  const [stages, setStages] =
    useState<PipelineStage[]>([]);

  const [
    opportunities,
    setOpportunities,
  ] = useState<Opportunity[]>([]);

  const [accounts, setAccounts] =
    useState<Account[]>([]);

  const [loading, setLoading] =
    useState(true);

  const [movingId, setMovingId] =
    useState<string | null>(null);

  const [
    draggingOpportunityId,
    setDraggingOpportunityId,
  ] = useState<string | null>(null);

  const [
    dragOverStageId,
    setDragOverStageId,
  ] = useState<string | null>(null);

  const [error, setError] =
    useState<string | null>(null);


  async function loadPipeline(
    pipelineId?: string
  ) {
    try {
      setLoading(true);
      setError(null);

      const [
        pipelineData,
        accountData,
      ] = await Promise.all([
        getPipelines(),
        getAccounts(),
      ]);

      setPipelines(
        pipelineData
      );

      setAccounts(
        accountData
      );

      const selected =
        pipelineId
        || pipelineData.find(
          (pipeline) =>
            pipeline.is_default
        )?.id
        || pipelineData[0]?.id
        || "";

      setSelectedPipelineId(
        selected
      );

      if (!selected) {
        setStages([]);
        setOpportunities([]);
        return;
      }

      const [
        stageData,
        opportunityData,
      ] = await Promise.all([
        getPipelineStages(
          selected
        ),
        getOpportunities({
          pipelineId: selected,
        }),
      ]);

      setStages(
        [...stageData].sort(
          (a, b) =>
            a.position - b.position
        )
      );

      setOpportunities(
        opportunityData
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to load sales pipeline."
      );
    } finally {
      setLoading(false);
    }
  }


  useEffect(() => {
    loadPipeline();
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


  const totalPipelineValue =
    useMemo(() => {
      return opportunities
        .filter(
          (opportunity) =>
            stages.find(
              (stage) =>
                stage.id
                === opportunity.stage_id
            )?.category
            === "open"
        )
        .reduce(
          (total, opportunity) =>
            total +
            Number(
              opportunity.amount ?? 0
            ),
          0
        );
    }, [
      opportunities,
      stages,
    ]);


  const weightedPipelineValue =
    useMemo(() => {
      return opportunities
        .filter(
          (opportunity) =>
            stages.find(
              (stage) =>
                stage.id
                === opportunity.stage_id
            )?.category
            === "open"
        )
        .reduce(
          (total, opportunity) =>
            total +
            (
              Number(
                opportunity.amount
                ?? 0
              )
              *
              opportunity.probability
            ) /
            100,
          0
        );
    }, [
      opportunities,
      stages,
    ]);


  async function changePipeline(
    pipelineId: string
  ) {
    setSelectedPipelineId(
      pipelineId
    );

    await loadPipeline(
      pipelineId
    );
  }


  async function moveDeal(
    opportunityId: string,
    targetStageId: string
  ) {
    const opportunity =
      opportunities.find(
        (item) =>
          item.id === opportunityId
      );

    if (
      !opportunity ||
      opportunity.stage_id
      === targetStageId
    ) {
      return;
    }

    const previous =
      opportunity;

    const targetStage =
      stages.find(
        (stage) =>
          stage.id
          === targetStageId
      );

    if (!targetStage) {
      return;
    }

    setMovingId(
      opportunityId
    );

    setError(null);

    setOpportunities(
      (current) =>
        current.map(
          (item) =>
            item.id
            === opportunityId
              ? {
                  ...item,
                  stage_id:
                    targetStageId,
                  probability:
                    targetStage.probability,
                  closed_at:
                    targetStage.category
                    === "open"
                      ? null
                      : item.closed_at,
                }
              : item
        )
    );

    try {
      const updated =
        await moveOpportunity(
          opportunityId,
          targetStageId
        );

      setOpportunities(
        (current) =>
          current.map(
            (item) =>
              item.id
              === updated.id
                ? updated
                : item
          )
      );
    } catch (err) {
      setOpportunities(
        (current) =>
          current.map(
            (item) =>
              item.id
              === previous.id
                ? previous
                : item
          )
      );

      setError(
        err instanceof Error
          ? err.message
          : "Unable to move opportunity."
      );
    } finally {
      setMovingId(null);
    }
  }


  function handleDragStart(
    event: DragEvent<HTMLDivElement>,
    opportunityId: string
  ) {
    setDraggingOpportunityId(
      opportunityId
    );

    event.dataTransfer.effectAllowed =
      "move";

    event.dataTransfer.setData(
      "text/plain",
      opportunityId
    );
  }


  function handleDragEnd() {
    setDraggingOpportunityId(
      null
    );

    setDragOverStageId(
      null
    );
  }


  function handleDragOver(
    event: DragEvent<HTMLDivElement>,
    stageId: string
  ) {
    event.preventDefault();

    event.dataTransfer.dropEffect =
      "move";

    setDragOverStageId(
      stageId
    );
  }


  async function handleDrop(
    event: DragEvent<HTMLDivElement>,
    stageId: string
  ) {
    event.preventDefault();

    const opportunityId =
      event.dataTransfer.getData(
        "text/plain"
      )
      || draggingOpportunityId;

    setDragOverStageId(
      null
    );

    setDraggingOpportunityId(
      null
    );

    if (!opportunityId) {
      return;
    }

    await moveDeal(
      opportunityId,
      stageId
    );
  }


  return (
    <div className="appShell">
      <Sidebar active="Pipeline" />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <div className="pipelinePageHeader">
            <div>
              <p className="eyebrow">
                Sales
              </p>

              <h1>
                Sales Pipeline
              </h1>

              <p>
                Drag opportunities between
                stages to update deal
                progression automatically.
              </p>
            </div>

            <div className="pipelineHeaderActions">
              <select
                value={
                  selectedPipelineId
                }
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

              <button
                type="button"
                className="pipelineRefresh"
                onClick={() =>
                  loadPipeline(
                    selectedPipelineId
                  )
                }
              >
                <RefreshCcw size={15} />
                Refresh
              </button>

              <Link
                href="/opportunities"
                className="pipelineNewDeal"
              >
                <Plus size={15} />
                New Opportunity
              </Link>
            </div>
          </div>

          <div className="pipelineMetrics">
            <div>
              <SquareKanban
                size={18}
              />

              <span>
                Total Deals
              </span>

              <strong>
                {
                  opportunities.length
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
                {formatMoney(
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
                {formatMoney(
                  weightedPipelineValue
                )}
              </strong>
            </div>
          </div>

          {error && (
            <div className="formError">
              {error}
            </div>
          )}

          {loading ? (
            <div className="pipelineLoading">
              <Loader2
                size={20}
              />

              Loading pipeline...
            </div>
          ) : (
            <div className="kanbanScroller">
              <div className="kanbanBoard">
                {stages.map(
                  (stage) => {
                    const stageDeals =
                      opportunities.filter(
                        (opportunity) =>
                          opportunity.stage_id
                          === stage.id
                      );

                    const stageValue =
                      stageDeals.reduce(
                        (
                          total,
                          opportunity
                        ) =>
                          total +
                          Number(
                            opportunity.amount
                            ?? 0
                          ),
                        0
                      );

                    const isDragOver =
                      dragOverStageId
                      === stage.id;

                    return (
                      <div
                        key={stage.id}
                        className={
                          isDragOver
                            ? "kanbanColumn dragOver"
                            : "kanbanColumn"
                        }
                        onDragOver={(
                          event
                        ) =>
                          handleDragOver(
                            event,
                            stage.id
                          )
                        }
                        onDrop={(
                          event
                        ) =>
                          handleDrop(
                            event,
                            stage.id
                          )
                        }
                      >
                        <div className="kanbanColumnHeader">
                          <div>
                            <div className="kanbanStageTitle">
                              <span
                                className={
                                  `stageDot stage-${stage.category}`
                                }
                              />

                              <strong>
                                {stage.name}
                              </strong>

                              <span className="kanbanCount">
                                {
                                  stageDeals.length
                                }
                              </span>
                            </div>

                            <p>
                              {
                                stage.probability
                              }
                              % probability
                            </p>
                          </div>

                          <span className="kanbanStageValue">
                            {formatMoney(
                              stageValue
                            )}
                          </span>
                        </div>

                        <div className="kanbanCards">
                          {stageDeals.length
                            === 0 && (
                            <div className="kanbanEmpty">
                              Drop opportunity here
                            </div>
                          )}

                          {stageDeals.map(
                            (
                              opportunity
                            ) => {
                              const account =
                                accountMap.get(
                                  opportunity
                                    .account_id
                                );

                              const isMoving =
                                movingId
                                === opportunity.id;

                              const isDragging =
                                draggingOpportunityId
                                === opportunity.id;

                              return (
                                <div
                                  key={
                                    opportunity.id
                                  }
                                  draggable={
                                    !isMoving
                                  }
                                  className={
                                    isDragging
                                      ? "kanbanCard dragging"
                                      : "kanbanCard"
                                  }
                                  onDragStart={(
                                    event
                                  ) =>
                                    handleDragStart(
                                      event,
                                      opportunity.id
                                    )
                                  }
                                  onDragEnd={
                                    handleDragEnd
                                  }
                                >
                                  <div className="kanbanCardTop">
                                    <GripVertical
                                      size={15}
                                    />

                                    {isMoving && (
                                      <Loader2
                                        size={14}
                                      />
                                    )}
                                  </div>

                                  <Link
                                    href={
                                      `/opportunities/${opportunity.id}`
                                    }
                                    className="kanbanDealName"
                                  >
                                    {
                                      opportunity.name
                                    }
                                  </Link>

                                  <div className="kanbanAccount">
                                    <Building2
                                      size={13}
                                    />

                                    {
                                      account?.name
                                      ?? "Unknown account"
                                    }
                                  </div>

                                  <div className="kanbanCardValue">
                                    {formatOpportunityMoney(
                                      opportunity.amount,
                                      opportunity.currency
                                    )}
                                  </div>

                                  <div className="kanbanCardMeta">
                                    <span>
                                      {
                                        opportunity.probability
                                      }
                                      %
                                    </span>

                                    <span>
                                      {formatDate(
                                        opportunity
                                          .expected_close_date
                                      )}
                                    </span>
                                  </div>

                                  <select
                                    value={
                                      opportunity.stage_id
                                    }
                                    disabled={
                                      isMoving
                                    }
                                    onChange={(
                                      event
                                    ) =>
                                      moveDeal(
                                        opportunity.id,
                                        event.target
                                          .value
                                      )
                                    }
                                  >
                                    {stages.map(
                                      (
                                        option
                                      ) => (
                                        <option
                                          key={
                                            option.id
                                          }
                                          value={
                                            option.id
                                          }
                                        >
                                          {
                                            option.name
                                          }
                                        </option>
                                      )
                                    )}
                                  </select>
                                </div>
                              );
                            }
                          )}
                        </div>
                      </div>
                    );
                  }
                )}
              </div>
            </div>
          )}
        </div>
      </main>

      <style jsx>{`
        .pipelinePageHeader {
          display: flex;
          align-items: flex-end;
          justify-content: space-between;
          gap: 20px;
          margin-bottom: 22px;
        }

        .pipelinePageHeader h1 {
          margin: 4px 0 5px;
          font-size: 26px;
          letter-spacing: -0.035em;
        }

        .pipelinePageHeader p {
          margin: 0;
          color: var(--muted);
          font-size: 13px;
        }

        .pipelineHeaderActions {
          display: flex;
          align-items: center;
          gap: 9px;
        }

        .pipelineHeaderActions select,
        .kanbanCard select {
          border: 1px solid var(--border);
          border-radius: 8px;
          background: white;
          color: var(--text);
        }

        .pipelineHeaderActions select {
          min-width: 190px;
          height: 38px;
          padding: 0 10px;
        }

        .pipelineRefresh,
        .pipelineNewDeal {
          height: 38px;
          display: inline-flex;
          align-items: center;
          gap: 7px;
          padding: 0 12px;
          border-radius: 8px;
          font-size: 12px;
          font-weight: 700;
          text-decoration: none;
        }

        .pipelineRefresh {
          border: 1px solid var(--border);
          background: white;
          color: var(--text);
        }

        .pipelineNewDeal {
          background: var(--primary);
          color: white;
        }

        .pipelineMetrics {
          display: grid;
          grid-template-columns:
            repeat(3, minmax(0, 1fr));
          gap: 12px;
          margin-bottom: 18px;
        }

        .pipelineMetrics > div {
          display: grid;
          grid-template-columns:
            auto 1fr auto;
          gap: 9px;
          align-items: center;
          padding: 15px 16px;
          border: 1px solid var(--border);
          border-radius: 12px;
          background: white;
        }

        .pipelineMetrics span {
          color: var(--muted);
          font-size: 11px;
        }

        .pipelineMetrics strong {
          font-size: 15px;
        }

        .pipelineLoading {
          min-height: 240px;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 8px;
          border: 1px solid var(--border);
          border-radius: 14px;
          background: white;
          color: var(--muted);
        }

        .kanbanScroller {
          width: 100%;
          overflow-x: auto;
          padding-bottom: 14px;
        }

        .kanbanBoard {
          min-width: 1960px;
          display: grid;
          grid-template-columns:
            repeat(7, minmax(250px, 1fr));
          gap: 12px;
          align-items: start;
        }

        .kanbanColumn {
          min-height: 520px;
          padding: 10px;
          border: 1px solid var(--border);
          border-radius: 12px;
          background: #f8f9fc;
          transition: 0.16s ease;
        }

        .kanbanColumn.dragOver {
          border-color: var(--primary);
          background: #f3f0ff;
        }

        .kanbanColumnHeader {
          display: flex;
          justify-content: space-between;
          gap: 8px;
          padding: 6px 4px 12px;
        }

        .kanbanStageTitle {
          display: flex;
          align-items: center;
          gap: 7px;
        }

        .kanbanStageTitle strong {
          font-size: 12px;
        }

        .kanbanColumnHeader p {
          margin: 5px 0 0 17px;
          color: var(--muted);
          font-size: 9px;
        }

        .stageDot {
          width: 9px;
          height: 9px;
          border-radius: 50%;
          background: #8b91a0;
        }

        .stage-open {
          background: var(--primary);
        }

        .stage-won {
          background: var(--success);
        }

        .stage-lost {
          background: var(--danger);
        }

        .kanbanCount {
          min-width: 20px;
          height: 20px;
          display: grid;
          place-items: center;
          border-radius: 999px;
          background: #e9eaf0;
          color: #636978;
          font-size: 9px;
          font-weight: 800;
        }

        .kanbanStageValue {
          color: var(--muted);
          font-size: 10px;
          font-weight: 700;
        }

        .kanbanCards {
          display: flex;
          flex-direction: column;
          gap: 9px;
        }

        .kanbanEmpty {
          padding: 24px 10px;
          border: 1px dashed #d8dbe5;
          border-radius: 9px;
          color: #9aa0ae;
          font-size: 10px;
          text-align: center;
        }

        .kanbanCard {
          padding: 12px;
          border: 1px solid var(--border);
          border-radius: 10px;
          background: white;
          cursor: grab;
          transition:
            transform 0.14s ease,
            box-shadow 0.14s ease,
            opacity 0.14s ease;
        }

        .kanbanCard:hover {
          transform: translateY(-1px);
          box-shadow:
            0 8px 22px
            rgba(30, 34, 48, 0.07);
        }

        .kanbanCard.dragging {
          opacity: 0.45;
        }

        .kanbanCardTop {
          min-height: 16px;
          display: flex;
          justify-content: space-between;
          color: #a2a8b5;
        }

        .kanbanDealName {
          display: block;
          margin: 5px 0 7px;
          color: var(--text);
          font-size: 12px;
          font-weight: 750;
          line-height: 1.35;
          text-decoration: none;
        }

        .kanbanDealName:hover {
          color: var(--primary);
        }

        .kanbanAccount {
          display: flex;
          align-items: center;
          gap: 5px;
          color: var(--muted);
          font-size: 9px;
        }

        .kanbanCardValue {
          margin: 12px 0 8px;
          font-size: 18px;
          font-weight: 800;
          letter-spacing: -0.03em;
        }

        .kanbanCardMeta {
          display: flex;
          justify-content: space-between;
          gap: 8px;
          margin-bottom: 10px;
          color: var(--muted);
          font-size: 9px;
        }

        .kanbanCard select {
          width: 100%;
          height: 31px;
          padding: 0 7px;
          font-size: 10px;
        }

        @media (
          max-width: 900px
        ) {
          .pipelinePageHeader {
            align-items: flex-start;
            flex-direction: column;
          }

          .pipelineHeaderActions {
            width: 100%;
            flex-wrap: wrap;
          }

          .pipelineMetrics {
            grid-template-columns: 1fr;
          }
        }
      `}</style>
    </div>
  );
}
