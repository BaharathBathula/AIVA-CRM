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
  CreateStageDialog,
} from "@/components/pipeline/create-stage-dialog";

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
  createPipeline,
  getPipelines,
  getPipelineStages,
  renamePipeline,
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

  // AIVA_P42_CREATE_PIPELINE
  const [showCreatePipeline, setShowCreatePipeline] =
    useState(false);
  const [newPipelineName, setNewPipelineName] =
    useState("");
  const [creatingPipeline, setCreatingPipeline] =
    useState(false);
  const [createPipelineError, setCreatePipelineError] =
    useState<string | null>(null);

  // AIVA_P44_RENAME_PIPELINE
  const [showRenamePipeline, setShowRenamePipeline] =
    useState(false);
  const [renamePipelineName, setRenamePipelineName] =
    useState("");
  const [renamingPipeline, setRenamingPipeline] =
    useState(false);
  const [renamePipelineError, setRenamePipelineError] =
    useState<string | null>(null);

  // AIVA_P52_ADD_STAGE_INTEGRATION
  const [showCreateStage, setShowCreateStage] =
    useState(false);

  function openRenamePipeline() {
    const selected = pipelines.find(
      (pipeline) => pipeline.id === selectedPipelineId
    );

    if (!selected) return;

    setRenamePipelineName(selected.name);
    setRenamePipelineError(null);
    setShowRenamePipeline(true);
  }

  async function handleRenamePipeline(
    event: React.FormEvent<HTMLFormElement>
  ) {
    event.preventDefault();

    if (renamingPipeline || !selectedPipelineId) return;

    const name = renamePipelineName.trim();

    if (!name || name.length > 150) {
      setRenamePipelineError(
        "Enter a pipeline name between 1 and 150 characters."
      );
      return;
    }

    try {
      setRenamingPipeline(true);
      setRenamePipelineError(null);

      const updated = await renamePipeline(
        selectedPipelineId,
        name
      );

      setPipelines((current) =>
        current.map((pipeline) =>
          pipeline.id === updated.id ? updated : pipeline
        )
      );

      setShowRenamePipeline(false);
      await loadPipeline(updated.id);
    } catch (err) {
      setRenamePipelineError(
        err instanceof Error
          ? err.message
          : "Unable to rename pipeline."
      );
    } finally {
      setRenamingPipeline(false);
    }
  }

  async function handleCreatePipeline(
    event: React.FormEvent<HTMLFormElement>
  ) {
    event.preventDefault();

    if (creatingPipeline) return;

    const name = newPipelineName.trim();

    if (!name || name.length > 150) {
      setCreatePipelineError(
        "Enter a pipeline name between 1 and 150 characters."
      );
      return;
    }

    try {
      setCreatingPipeline(true);
      setCreatePipelineError(null);

      const created = await createPipeline(name);

      setShowCreatePipeline(false);
      setNewPipelineName("");

      await loadPipeline(created.id);
    } catch (err) {
      setCreatePipelineError(
        err instanceof Error
          ? err.message
          : "Unable to create pipeline."
      );
    } finally {
      setCreatingPipeline(false);
    }
  }


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
              <button
                type="button"
                className="pipelineCreateButton"
                onClick={() => {
                  setCreatePipelineError(null);
                  setShowCreatePipeline(true);
                }}
              >
                <Plus size={15} />
                New Pipeline
              </button>

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
                className="pipelineCreateButton"
                onClick={openRenamePipeline}
                disabled={!selectedPipelineId || renamingPipeline}
              >
                Rename
              </button>

              <button
                type="button"
                className="pipelineCreateButton"
                disabled={!selectedPipelineId || loading}
                onClick={() => setShowCreateStage(true)}
              >
                <Plus size={15} />
                Add Stage
              </button>

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


            </div>
          </div>

          {showCreateStage && selectedPipelineId && (
            <CreateStageDialog
              pipelineId={selectedPipelineId}
              onClose={() => setShowCreateStage(false)}
              onCreated={(stage) => {
                setStages((current) =>
                  [...current, stage].sort(
                    (a, b) => a.position - b.position
                  )
                );
                setShowCreateStage(false);
                void loadPipeline(selectedPipelineId);
              }}
            />
          )}

          {/* AIVA_P44_RENAME_PIPELINE_DIALOG */}
          {showRenamePipeline && (
            <div
              className="pipelineCreateOverlay"
              role="presentation"
              onMouseDown={(event) => {
                if (
                  event.target === event.currentTarget &&
                  !renamingPipeline
                ) {
                  setShowRenamePipeline(false);
                }
              }}
            >
              <div
                className="pipelineCreateDialog"
                role="dialog"
                aria-modal="true"
                aria-labelledby="rename-pipeline-title"
              >
                <h2 id="rename-pipeline-title">
                  Rename Pipeline
                </h2>
                <p>
                  Update the selected pipeline name.
                  Existing stages and opportunities will remain unchanged.
                </p>

                <form onSubmit={handleRenamePipeline}>
                  <label htmlFor="rename-pipeline-name">
                    Pipeline name
                  </label>
                  <input
                    id="rename-pipeline-name"
                    type="text"
                    autoFocus
                    maxLength={150}
                    required
                    value={renamePipelineName}
                    onChange={(event) =>
                      setRenamePipelineName(event.target.value)
                    }
                    disabled={renamingPipeline}
                  />

                  {renamePipelineError && (
                    <p
                      className="pipelineCreateError"
                      role="alert"
                    >
                      {renamePipelineError}
                    </p>
                  )}

                  <div className="pipelineCreateActions">
                    <button
                      type="button"
                      onClick={() =>
                        setShowRenamePipeline(false)
                      }
                      disabled={renamingPipeline}
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      disabled={renamingPipeline}
                    >
                      {renamingPipeline
                        ? "Saving..."
                        : "Save Changes"}
                    </button>
                  </div>
                </form>
              </div>
            </div>
          )}

          {showCreatePipeline && (
            <div
              className="pipelineCreateOverlay"
              role="presentation"
              onMouseDown={(event) => {
                if (
                  event.target === event.currentTarget &&
                  !creatingPipeline
                ) {
                  setShowCreatePipeline(false);
                }
              }}
            >
              <div
                className="pipelineCreateDialog"
                role="dialog"
                aria-modal="true"
                aria-labelledby="create-pipeline-title"
              >
                <h2 id="create-pipeline-title">
                  Create New Pipeline
                </h2>
                <p>
                  Create a sales pipeline with seven default stages.
                </p>

                <form onSubmit={handleCreatePipeline}>
                  <label htmlFor="new-pipeline-name">
                    Pipeline name
                  </label>
                  <input
                    id="new-pipeline-name"
                    type="text"
                    autoFocus
                    maxLength={150}
                    required
                    value={newPipelineName}
                    onChange={(event) =>
                      setNewPipelineName(event.target.value)
                    }
                    placeholder="e.g. Enterprise Sales"
                    disabled={creatingPipeline}
                  />

                  {createPipelineError && (
                    <p
                      className="pipelineCreateError"
                      role="alert"
                    >
                      {createPipelineError}
                    </p>
                  )}

                  <div className="pipelineCreateActions">
                    <button
                      type="button"
                      onClick={() =>
                        setShowCreatePipeline(false)
                      }
                      disabled={creatingPipeline}
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      disabled={creatingPipeline}
                    >
                      {creatingPipeline
                        ? "Creating..."
                        : "Create Pipeline"}
                    </button>
                  </div>
                </form>
              </div>
            </div>
          )}

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
              <div
                  className="kanbanBoard"
                  style={{
                    gridTemplateColumns:
                      `repeat(${Math.max(stages.length, 1)}, minmax(250px, 1fr))`,
                    minWidth: `${Math.max(stages.length, 1) * 264}px`,
                  }}
                >
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

        /* AIVA_P42_CREATE_PIPELINE_STYLES */
        .pipelineCreateButton {
          height: 38px;
          display: inline-flex;
          align-items: center;
          gap: 7px;
          padding: 0 12px;
          border: 1px solid var(--border);
          border-radius: 8px;
          background: white;
          color: var(--text);
          font-size: 12px;
          font-weight: 700;
          cursor: pointer;
        }

        .pipelineCreateOverlay {
          position: fixed;
          inset: 0;
          z-index: 1000;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 20px;
          background: rgba(15, 23, 42, 0.55);
        }

        .pipelineCreateDialog {
          width: 100%;
          max-width: 440px;
          padding: 26px;
          border-radius: 14px;
          background: white;
          color: #172033;
          box-shadow: 0 20px 70px rgba(0, 0, 0, 0.2);
        }

        .pipelineCreateDialog h2 {
          margin: 0 0 8px;
          font-size: 21px;
        }

        .pipelineCreateDialog p {
          margin: 0 0 20px;
          color: #64748b;
          font-size: 13px;
        }

        .pipelineCreateDialog form {
          display: flex;
          flex-direction: column;
          gap: 12px;
        }

        .pipelineCreateDialog label {
          font-size: 13px;
          font-weight: 700;
        }

        .pipelineCreateDialog input {
          width: 100%;
          height: 42px;
          padding: 0 12px;
          border: 1px solid #cbd5e1;
          border-radius: 8px;
          font-size: 14px;
        }

        .pipelineCreateDialog .pipelineCreateError {
          margin: 0;
          color: #b91c1c;
        }

        .pipelineCreateActions {
          display: flex;
          justify-content: flex-end;
          gap: 10px;
          margin-top: 12px;
        }

        .pipelineCreateActions button {
          padding: 10px 15px;
          border: 1px solid #cbd5e1;
          border-radius: 8px;
          background: white;
          color: #172033;
          font-weight: 700;
          cursor: pointer;
        }

        .pipelineCreateActions button[type="submit"] {
          border-color: var(--primary);
          background: var(--primary);
          color: white;
        }

        .pipelineCreateActions button:disabled {
          opacity: 0.6;
          cursor: not-allowed;
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

        /* AIVA_PIPELINE_P22_VISUAL_ENHANCEMENTS */

        .pipelinePageHeader h1 {
          font-size: 29px;
          font-weight: 800;
          line-height: 1.2;
        }

        .pipelinePageHeader p {
          font-size: 13px;
          line-height: 1.6;
        }

        .pipelineMetrics > div {
          min-height: 76px;
          padding: 18px;
          border-radius: 14px;
        }

        .pipelineMetrics span {
          font-size: 12px;
          font-weight: 500;
        }

        .pipelineMetrics strong {
          font-size: 19px;
          font-weight: 800;
        }

        .kanbanBoard {
          gap: 14px;
        }

        .kanbanColumn {
          padding: 12px;
          border-radius: 14px;
          background: #f7f8fc;
        }

        .kanbanColumnHeader {
          padding: 8px 5px 16px;
          align-items: flex-start;
        }

        .kanbanStageTitle strong {
          font-size: 14px;
          font-weight: 750;
          line-height: 1.4;
        }

        .kanbanColumnHeader p {
          font-size: 11px;
          margin-top: 7px;
        }

        .kanbanStageValue {
          font-size: 12px;
          font-weight: 750;
        }

        .kanbanCount {
          min-width: 23px;
          height: 23px;
          padding: 0 5px;
          font-size: 11px;
        }

        .kanbanCards {
          gap: 12px;
        }

        .kanbanCard {
          padding: 16px;
          border-radius: 12px;
          border-color: #e0e5ef;
          box-shadow: 0 2px 6px rgba(20, 30, 60, 0.025);
        }

        .kanbanCard:hover {
          transform: translateY(-2px);
          border-color: #b8b0f5;
          box-shadow: 0 10px 26px rgba(40, 35, 100, 0.09);
        }

        .kanbanDealName {
          margin: 8px 0;
          font-size: 14px;
          font-weight: 750;
          line-height: 1.45;
          text-decoration: none;
          overflow-wrap: anywhere;
        }

        .kanbanDealName:hover {
          text-decoration: underline;
          text-underline-offset: 3px;
        }

        .kanbanAccount {
          gap: 7px;
          font-size: 11px;
          line-height: 1.5;
        }

        .kanbanCardValue {
          margin: 15px 0 10px;
          font-size: 21px;
          font-weight: 800;
        }

        .kanbanCardMeta {
          margin-bottom: 13px;
          font-size: 11px;
          line-height: 1.4;
        }

        .kanbanCard select {
          height: 38px;
          padding: 0 10px;
          font-size: 12px;
          border-radius: 8px;
        }

        .kanbanEmpty {
          padding: 28px 12px;
          font-size: 12px;
          line-height: 1.5;
        }

        .kanbanColumn.dragOver {
          border-color: var(--primary);
          background: #f0edff;
          box-shadow: inset 0 0 0 1px var(--primary);
        }

        .pipelineRefresh:hover {
          border-color: var(--primary);
        }

        .pipelineNewDeal:hover {
          filter: brightness(0.96);
        }

        .kanbanCard select:focus-visible,
        .pipelineHeaderActions select:focus-visible,
        .pipelineRefresh:focus-visible,
        .pipelineNewDeal:focus-visible,
        .kanbanDealName:focus-visible {
          outline: 2px solid var(--primary);
          outline-offset: 2px;
        }

        @media (prefers-reduced-motion: reduce) {
          .kanbanCard,
          .kanbanColumn {
            transition: none;
          }

          .kanbanCard:hover {
            transform: none;
          }
        }

      `}</style>
    </div>
  );
}
