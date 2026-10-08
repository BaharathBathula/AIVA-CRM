"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  ArrowUpDown,
  CheckSquare2,
  CircleDollarSign,
  Filter,
  Handshake,
  Plus,
  Search,
  Target,
  Trophy,
  X,
} from "lucide-react";

import {
  CreateOpportunityModal,
} from "@/components/opportunities/create-opportunity-modal";

import {
  OpportunityTable,
} from "@/components/opportunities/opportunity-table";

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
  ForecastCategory,
  Opportunity,
  OpportunityPriority,
  OpportunityType,
} from "@/types/opportunity";

import type {
  Pipeline,
  PipelineStage,
  PipelineStageCategory,
} from "@/types/pipeline";


import {
  OpportunitySavedViews,
  type OpportunityViewId,
} from "@/components/opportunities/opportunity-saved-views";

import {
  OpportunitySaveViewDialog,
} from "@/components/opportunities/opportunity-save-view-dialog";

import {
  loadOpportunitySavedViews,
  saveOpportunitySavedViews,
  type OpportunityCustomSavedView,
  type OpportunitySavedViewFilters,
} from "@/lib/opportunities/saved-views";

import {
  OpportunityCustomViews,
} from "@/components/opportunities/opportunity-custom-views";

import {
  OpportunityBulkToolbar,
} from "@/components/opportunities/opportunity-bulk-toolbar";

import type {
  OpportunityBulkActionType,
} from "@/lib/opportunities/bulk-actions";

import {
  OpportunityBulkOwnerDialog,
} from "@/components/opportunities/opportunity-bulk-owner-dialog";

import type {
  BulkOwnerOption,
} from "@/components/opportunities/opportunity-bulk-owner-dialog";

import {
  bulkChangeOpportunityOwner,
  getEligibleOpportunityOwners,
} from "@/lib/opportunity-bulk-owner";

import {
  OpportunityBulkStageDialog,
} from "@/components/opportunities/opportunity-bulk-stage-dialog";

import type {
  BulkPipelineOption,
} from "@/components/opportunities/opportunity-bulk-stage-dialog";

import {
  bulkChangeOpportunityStage,
} from "@/lib/opportunity-bulk-stage";



type StageFilter =
  | "all"
  | PipelineStageCategory;

type PriorityFilter =
  | "all"
  | OpportunityPriority;

type ForecastFilter =
  | "all"
  | ForecastCategory;

type OpportunityTypeFilter =
  | "all"
  | OpportunityType;

type CloseDateFilter =
  | "all"
  | "overdue"
  | "this_week"
  | "this_month"
  | "next_30_days"
  | "next_90_days";

type SortOption =
  | "updated_desc"
  | "amount_desc"
  | "amount_asc"
  | "close_asc"
  | "probability_desc";

const forecastFilters:
  ForecastFilter[] = [
    "all",
    "pipeline",
    "best_case",
    "commit",
    "closed",
    "omitted",
  ];

const opportunityTypeFilters:
  OpportunityTypeFilter[] = [
    "all",
    "new_business",
    "renewal",
    "upsell",
    "cross_sell",
    "expansion",
  ];

const closeDateFilters:
  CloseDateFilter[] = [
    "all",
    "overdue",
    "this_week",
    "this_month",
    "next_30_days",
    "next_90_days",
  ];

const stageFilters:
  StageFilter[] = [
    "all",
    "open",
    "won",
    "lost",
  ];


const priorityFilters:
  PriorityFilter[] = [
    "all",
    "critical",
    "high",
    "medium",
    "low",
  ];


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


function formatCompactMoney(
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
  ).format(
    value
  );
}


export default function OpportunitiesPage() {
  const [
    opportunities,
    setOpportunities,
  ] = useState<
    Opportunity[]
  >([]);

  const [
    accounts,
    setAccounts,
  ] = useState<
    Account[]
  >([]);

  const [
    pipelines,
    setPipelines,
  ] = useState<
    Pipeline[]
  >([]);

  const [
    stages,
    setStages,
  ] = useState<
    PipelineStage[]
  >([]);

  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    error,
    setError,
  ] = useState<
    string | null
  >(null);

  const [activeView, setActiveView] =
    useState<OpportunityViewId | null>("all_opportunities");

  const [
    query,
    setQuery,
  ] = useState("");

  const [
    stageFilter,
    setStageFilter,
  ] = useState<
    StageFilter
  >("all");

  const [
    priorityFilter,
    setPriorityFilter,
  ] = useState<
    PriorityFilter
  >("all");

  const [
    pipelineFilter,
    setPipelineFilter,
  ] = useState("all");

  const [
  forecastFilter,
  setForecastFilter,
] = useState<
  ForecastFilter
>("all");

const [
  opportunityTypeFilter,
  setOpportunityTypeFilter,
] = useState<
  OpportunityTypeFilter
>("all");

const [
  closeDateFilter,
  setCloseDateFilter,
] = useState<
  CloseDateFilter
>("all");

  const [
    sortOption,
    setSortOption,
  ] = useState<
    SortOption
  >(
    "updated_desc"
  );

  const [
    createOpen,
    setCreateOpen,
  ] = useState(false);

  const [
    selectedOpportunityIds,
    setSelectedOpportunityIds,
  ] = useState<
    Set<string>
  >(
    () => new Set()
  );




  // O2.6.4: Bulk Change Stage
  const [bulkStageOpen, setBulkStageOpen] = useState(false);
  const [bulkStageLoading, setBulkStageLoading] = useState(false);
  const [bulkStageError, setBulkStageError] = useState("");

  const bulkStagePipelines: BulkPipelineOption[] =
    pipelines.map((pipeline) => ({
      id: pipeline.id,
      name: pipeline.name,
      is_active: pipeline.is_active,
      stages: stages
        .filter((stage) => stage.pipeline_id === pipeline.id)
        .map((stage) => ({
          id: stage.id,
          name: stage.name,
          pipeline_id: stage.pipeline_id,
          is_active: stage.is_active,
        })),
    }));

  function openBulkStageDialog() {
    setBulkStageError("");
    setBulkStageOpen(true);
  }

  async function confirmBulkStageChange(
    pipelineId: string,
    stageId: string
  ) {
    setBulkStageLoading(true);
    setBulkStageError("");

    try {
      await bulkChangeOpportunityStage(
        Array.from(selectedOpportunityIds),
        pipelineId,
        stageId
      );

      const [
        updatedOpportunities,
        updatedPipelines,
      ] = await Promise.all([
        getOpportunities(),
        getPipelines(),
      ]);

      const updatedStageSets = await Promise.all(
        updatedPipelines.map((pipeline) =>
          getPipelineStages(pipeline.id)
        )
      );

      setOpportunities(updatedOpportunities);
      setPipelines(updatedPipelines);
      setStages(updatedStageSets.flat());
      setSelectedOpportunityIds(new Set<string>());
      setBulkStageOpen(false);
    } catch (error) {
      setBulkStageError(
        error instanceof Error
          ? error.message
          : "Unable to change opportunity stages."
      );
    } finally {
      setBulkStageLoading(false);
    }
  }

  const [bulkOwnerOpen, setBulkOwnerOpen] = useState(false);
  const [bulkOwnerOptions, setBulkOwnerOptions] = useState<BulkOwnerOption[]>([]);
  const [bulkOwnerLoading, setBulkOwnerLoading] = useState(false);
  const [bulkOwnerError, setBulkOwnerError] = useState("");

  async function openBulkOwnerDialog() {
    setBulkOwnerError("");
    setBulkOwnerLoading(true);

    try {
      const owners = await getEligibleOpportunityOwners();
      setBulkOwnerOptions(owners);
      setBulkOwnerOpen(true);
    } catch (error) {
      setBulkOwnerError(
        error instanceof Error ? error.message : "Unable to load owners."
      );
    } finally {
      setBulkOwnerLoading(false);
    }
  }

  async function confirmBulkOwnerChange(ownerUserId: string) {
    setBulkOwnerLoading(true);
    setBulkOwnerError("");

    try {
      await bulkChangeOpportunityOwner(
        Array.from(selectedOpportunityIds),
        ownerUserId
      );

      const updatedOpportunities = await getOpportunities();
      setOpportunities(updatedOpportunities);
      setSelectedOpportunityIds(new Set());
      setBulkOwnerOpen(false);
      setBulkOwnerOptions([]);
    } catch (error) {
      setBulkOwnerError(
        error instanceof Error
          ? error.message
          : "Unable to update opportunity owners."
      );
    } finally {
      setBulkOwnerLoading(false);
    }
  }

  useEffect(() => {
    async function load() {
      try {
        setLoading(true);
        setError(null);

        const [
          opportunityData,
          accountData,
          pipelineData,
        ] =
          await Promise.all([
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
      } catch (
        loadError
      ) {
        console.error(
          loadError
        );

        setError(
          "Unable to load opportunities."
        );
      } finally {
        setLoading(false);
      }
    }

    void load();
  }, []);


  const accountMap =
    useMemo(
      () =>
        new Map(
          accounts.map(
            (account) => [
              account.id,
              account,
            ]
          )
        ),
      [
        accounts,
      ]
    );


  const pipelineMap =
    useMemo(
      () =>
        new Map(
          pipelines.map(
            (pipeline) => [
              pipeline.id,
              pipeline,
            ]
          )
        ),
      [
        pipelines,
      ]
    );


  const stageMap =
    useMemo(
      () =>
        new Map(
          stages.map(
            (stage) => [
              stage.id,
              stage,
            ]
          )
        ),
      [
        stages,
      ]
    );


  const visibleOpportunities =
    useMemo(() => {
      const normalizedQuery =
        query
          .trim()
          .toLowerCase();

      const filtered =
        opportunities.filter(
          (
            opportunity
          ) => {
            const account =
              accountMap.get(
                opportunity.account_id
              );

            const stage =
              stageMap.get(
                opportunity.stage_id
              );

            const pipeline =
              pipelineMap.get(
                opportunity.pipeline_id
              );

            if (
              normalizedQuery
            ) {
              const searchable =
                [
                  opportunity.name,
                  opportunity.description,
                  opportunity.lead_source,
                  opportunity.next_step,
                  opportunity.competitor,
                  account?.name,
                  stage?.name,
                  pipeline?.name,
                ]
                  .filter(
                    Boolean
                  )
                  .join(" ")
                  .toLowerCase();

              if (
                !searchable.includes(
                  normalizedQuery
                )
              ) {
                return false;
              }
            }

            if (
              stageFilter !==
              "all"
            ) {
              if (
                stage?.category !==
                stageFilter
              ) {
                return false;
              }
            }

            if (
              priorityFilter !==
                "all" &&
              opportunity.priority !==
                priorityFilter
            ) {
              return false;
            }

            if (
  pipelineFilter !== "all" &&
  opportunity.pipeline_id !==
    pipelineFilter
) {
  return false;
}

if (
  forecastFilter !== "all" &&
  opportunity.forecast_category !==
    forecastFilter
) {
  return false;
}

if (
  opportunityTypeFilter !== "all" &&
  opportunity.opportunity_type !==
    opportunityTypeFilter
) {
  return false;
}

if (
  closeDateFilter !== "all"
) {
  if (
    !opportunity.expected_close_date
  ) {
    return false;
  }

  const today =
    new Date();

  today.setHours(
    0,
    0,
    0,
    0
  );

  const closeDate =
    new Date(
      `${opportunity.expected_close_date}T00:00:00`
    );

  /*
   * Closed deals should not be classified
   * as overdue.
   */
  if (
    closeDateFilter ===
    "overdue"
  ) {
    if (
      opportunity.closed_at ||
      closeDate >= today
    ) {
      return false;
    }
  }

  if (
    closeDateFilter ===
    "this_week"
  ) {
    const endOfWeek =
      new Date(today);

    const daysUntilSunday =
      7 - today.getDay();

    endOfWeek.setDate(
      today.getDate() +
        daysUntilSunday
    );

    endOfWeek.setHours(
      23,
      59,
      59,
      999
    );

    if (
      closeDate < today ||
      closeDate > endOfWeek
    ) {
      return false;
    }
  }

  if (
    closeDateFilter ===
    "this_month"
  ) {
    const monthEnd =
      new Date(
        today.getFullYear(),
        today.getMonth() + 1,
        0,
        23,
        59,
        59,
        999
      );

    if (
      closeDate < today ||
      closeDate > monthEnd
    ) {
      return false;
    }
  }

  if (
    closeDateFilter ===
    "next_30_days"
  ) {
    const endDate =
      new Date(today);

    endDate.setDate(
      today.getDate() + 30
    );

    endDate.setHours(
      23,
      59,
      59,
      999
    );

    if (
      closeDate < today ||
      closeDate > endDate
    ) {
      return false;
    }
  }

  if (
    closeDateFilter ===
    "next_90_days"
  ) {
    const endDate =
      new Date(today);

    endDate.setDate(
      today.getDate() + 90
    );

    endDate.setHours(
      23,
      59,
      59,
      999
    );

    if (
      closeDate < today ||
      closeDate > endDate
    ) {
      return false;
    }
  }
}

return true;
          }
        );

      return [
        ...filtered,
      ].sort(
        (
          left,
          right
        ) => {
          if (
            sortOption ===
            "amount_desc"
          ) {
            return (
              Number(
                right.amount ??
                  0
              ) -
              Number(
                left.amount ??
                  0
              )
            );
          }

          if (
            sortOption ===
            "amount_asc"
          ) {
            return (
              Number(
                left.amount ??
                  0
              ) -
              Number(
                right.amount ??
                  0
              )
            );
          }

          if (
            sortOption ===
            "probability_desc"
          ) {
            return (
              right.probability -
              left.probability
            );
          }

          if (
            sortOption ===
            "close_asc"
          ) {
            const leftDate =
              left.expected_close_date
                ? new Date(
                    `${left.expected_close_date}T00:00:00`
                  ).getTime()
                : Number.MAX_SAFE_INTEGER;

            const rightDate =
              right.expected_close_date
                ? new Date(
                    `${right.expected_close_date}T00:00:00`
                  ).getTime()
                : Number.MAX_SAFE_INTEGER;

            return (
              leftDate -
              rightDate
            );
          }

          return (
            new Date(
              right.updated_at
            ).getTime() -
            new Date(
              left.updated_at
            ).getTime()
          );
        }
      );
    }, [
      opportunities,
      accountMap,
      stageMap,
      pipelineMap,
      query,
      stageFilter,
      priorityFilter,
      pipelineFilter,
      sortOption,
    ]);


  const metrics =
    useMemo(() => {
      const open =
        opportunities.filter(
          (
            opportunity
          ) =>
            stageMap.get(
              opportunity.stage_id
            )?.category ===
            "open"
        );

      const won =
        opportunities.filter(
          (
            opportunity
          ) =>
            stageMap.get(
              opportunity.stage_id
            )?.category ===
            "won"
        );

      const lost =
        opportunities.filter(
          (
            opportunity
          ) =>
            stageMap.get(
              opportunity.stage_id
            )?.category ===
            "lost"
        );

      const pipelineValue =
        open.reduce(
          (
            total,
            opportunity
          ) =>
            total +
            Number(
              opportunity.amount ??
                0
            ),
          0
        );

      const weightedValue =
        open.reduce(
          (
            total,
            opportunity
          ) =>
            total +
            Number(
              opportunity.weighted_amount ??
                (
                  Number(
                    opportunity.amount ??
                      0
                  ) *
                  opportunity.probability
                ) /
                  100
            ),
          0
        );

      const wonRevenue =
        won.reduce(
          (
            total,
            opportunity
          ) =>
            total +
            Number(
              opportunity.amount ??
                0
            ),
          0
        );

      const closedCount =
        won.length +
        lost.length;

      const winRate =
        closedCount > 0
          ? (
              won.length /
              closedCount
            ) *
            100
          : 0;

      const openWithAmount =
        open.filter(
          (
            opportunity
          ) =>
            opportunity.amount !==
            null
        );

      const averageDealSize =
        openWithAmount.length >
        0
          ? pipelineValue /
            openWithAmount.length
          : 0;

      const now =
        new Date();

      const currentYear =
        now.getFullYear();

      const currentMonth =
        now.getMonth();

      const closingThisMonth =
        open.filter(
          (
            opportunity
          ) => {
            if (
              !opportunity.expected_close_date
            ) {
              return false;
            }

            const closeDate =
              new Date(
                `${opportunity.expected_close_date}T00:00:00`
              );

            return (
              closeDate.getFullYear() ===
                currentYear &&
              closeDate.getMonth() ===
                currentMonth
            );
          }
        );

      const closingThisMonthValue =
        closingThisMonth.reduce(
          (
            total,
            opportunity
          ) =>
            total +
            Number(
              opportunity.amount ??
                0
            ),
          0
        );

      return {
        openCount:
          open.length,

        wonCount:
          won.length,

        lostCount:
          lost.length,

        pipelineValue,

        weightedValue,

        wonRevenue,

        winRate,

        averageDealSize,

        closingThisMonthCount:
          closingThisMonth.length,

        closingThisMonthValue,
      };
    }, [
      opportunities,
      stageMap,
    ]);


  const visibleOpportunityIds =
    useMemo(
      () =>
        visibleOpportunities.map(
          (
            opportunity
          ) =>
            opportunity.id
        ),
      [
        visibleOpportunities,
      ]
    );


  const visibleSelectedCount =
    useMemo(
      () =>
        visibleOpportunityIds.filter(
          (
            opportunityId
          ) =>
            selectedOpportunityIds.has(
              opportunityId
            )
        ).length,
      [
        visibleOpportunityIds,
        selectedOpportunityIds,
      ]
    );


  const allVisibleSelected =
    visibleOpportunityIds.length >
      0 &&
    visibleSelectedCount ===
      visibleOpportunityIds.length;


  const someVisibleSelected =
    visibleSelectedCount >
      0 &&
    !allVisibleSelected;


  function toggleOpportunitySelection(
    opportunityId:
      string
  ) {
    setSelectedOpportunityIds(
      (
        current
      ) => {
        const next =
          new Set(
            current
          );

        if (
          next.has(
            opportunityId
          )
        ) {
          next.delete(
            opportunityId
          );
        } else {
          next.add(
            opportunityId
          );
        }

        return next;
      }
    );
  }


  function selectAllVisible() {
    setSelectedOpportunityIds(
      (
        current
      ) => {
        const next =
          new Set(
            current
          );

        visibleOpportunityIds.forEach(
          (
            opportunityId
          ) => {
            next.add(
              opportunityId
            );
          }
        );

        return next;
      }
    );
  }


  function toggleAllVisible() {
    setSelectedOpportunityIds(
      (
        current
      ) => {
        const next =
          new Set(
            current
          );

        if (
          allVisibleSelected
        ) {
          visibleOpportunityIds.forEach(
            (
              opportunityId
            ) => {
              next.delete(
                opportunityId
              );
            }
          );
        } else {
          visibleOpportunityIds.forEach(
            (
              opportunityId
            ) => {
              next.add(
                opportunityId
              );
            }
          );
        }

        return next;
      }
    );
  }


  // O2.6.1C: Enterprise bulk toolbar
  function handleBulkAction(
    action: OpportunityBulkActionType
  ) {
    if (action === "change_owner") {
      void openBulkOwnerDialog();
      return;
    }
    if (action === "change_stage") {
      openBulkStageDialog();
      return;
    }


    // Database mutations will be implemented in O2.6.2+
    const labels: Record<OpportunityBulkActionType, string> = {
      change_owner: "Change Owner",
      change_stage: "Change Stage",
      change_priority: "Change Priority",
      change_forecast: "Update Forecast",
      delete: "Delete",
    };

    window.alert(
      `${labels[action]} will be implemented in the next phase.`
    );
  }

  function clearSelection() {
    setSelectedOpportunityIds(
      new Set()
    );
  }


  // O2.5.1F: Synchronize built-in views
  useEffect(() => {
    const defaults =
      query.trim() === "" &&
      pipelineFilter === "all" &&
      opportunityTypeFilter === "all" &&
      sortOption === "updated_desc";

    const stageAll = stageFilter === "all";
    const priorityAll = priorityFilter === "all";
    const forecastAll = forecastFilter === "all";
    const closeAll = closeDateFilter === "all";

    let matched: OpportunityViewId | null = null;

    if (defaults) {
      if (
        stageAll &&
        priorityAll &&
        forecastAll &&
        closeAll
      ) {
        matched = "all_opportunities";
      } else if (
        stageAll &&
        priorityAll &&
        forecastAll &&
        closeDateFilter === "this_month"
      ) {
        matched = "closing_this_month";
      } else if (
        stageAll &&
        priorityFilter === "high" &&
        forecastAll &&
        closeAll
      ) {
        matched = "high_priority";
      } else if (
        stageAll &&
        priorityAll &&
        forecastFilter === "commit" &&
        closeAll
      ) {
        matched = "commit_forecast";
      } else if (
        stageFilter === "won" &&
        priorityAll &&
        forecastAll &&
        closeAll
      ) {
        matched = "won_deals";
      }
    }

    setActiveView(matched);
  }, [
    query,
    stageFilter,
    priorityFilter,
    pipelineFilter,
    forecastFilter,
    opportunityTypeFilter,
    closeDateFilter,
    sortOption,
  ]);

  // O2.5.2E: Custom view selection
  const [activeCustomViewId, setActiveCustomViewId] =
    useState<string | null>(null);

  // O2.5.2C: Custom saved views
  const [customViews, setCustomViews] =
    useState<OpportunityCustomSavedView[]>([]);

  const [saveViewOpen, setSaveViewOpen] =
    useState(false);

  useEffect(() => {
    setCustomViews(loadOpportunitySavedViews());
  }, []);

  function handleSaveCustomView(name: string) {
    const filters: OpportunitySavedViewFilters = {
      query,
      stageFilter,
      priorityFilter,
      pipelineFilter,
      forecastFilter,
      opportunityTypeFilter,
      closeDateFilter,
      sortOption,
    };

    const timestamp = new Date().toISOString();

    const newView: OpportunityCustomSavedView = {
      id: crypto.randomUUID(),
      name,
      filters,
      createdAt: timestamp,
      updatedAt: timestamp,
    };

    const nextViews = [...customViews, newView];

    try {
      saveOpportunitySavedViews(nextViews);
      setCustomViews(nextViews);
      setSaveViewOpen(false);
    } catch (error) {
      console.error("Unable to save custom view:", error);
      window.alert(
        "Unable to save this view. Check browser storage permissions."
      );
    }
  }

  // O2.5.2F: Synchronize custom saved views
  useEffect(() => {
    const matchingView = customViews.find((view) => {
      const filters = view.filters;

      return (
        filters.query === query &&
        filters.stageFilter === stageFilter &&
        filters.priorityFilter === priorityFilter &&
        filters.pipelineFilter === pipelineFilter &&
        filters.forecastFilter === forecastFilter &&
        filters.opportunityTypeFilter === opportunityTypeFilter &&
        filters.closeDateFilter === closeDateFilter &&
        filters.sortOption === sortOption
      );
    });

    setActiveCustomViewId(matchingView?.id ?? null);
  }, [
    customViews,
    query,
    stageFilter,
    priorityFilter,
    pipelineFilter,
    forecastFilter,
    opportunityTypeFilter,
    closeDateFilter,
    sortOption,
  ]);

  function applyCustomView(
    view: OpportunityCustomSavedView
  ) {
    const filters = view.filters;

    setQuery(filters.query);
    setStageFilter(filters.stageFilter as StageFilter);
    setPriorityFilter(filters.priorityFilter as PriorityFilter);
    setPipelineFilter(filters.pipelineFilter);
    setForecastFilter(filters.forecastFilter as ForecastFilter);
    setOpportunityTypeFilter(
      filters.opportunityTypeFilter as OpportunityTypeFilter
    );
    setCloseDateFilter(
      filters.closeDateFilter as CloseDateFilter
    );
    setSortOption(filters.sortOption as SortOption);

    setActiveCustomViewId(view.id);
    setSelectedOpportunityIds(new Set());
  }

  function deleteCustomView(
    view: OpportunityCustomSavedView
  ) {
    if (
      !window.confirm(
        `Delete saved view "${view.name}"?`
      )
    ) {
      return;
    }

    const nextViews = customViews.filter(
      (item) => item.id !== view.id
    );

    try {
      saveOpportunitySavedViews(nextViews);
      setCustomViews(nextViews);

      if (activeCustomViewId === view.id) {
        setActiveCustomViewId(null);
      }
    } catch (error) {
      console.error("Unable to delete custom view:", error);
      window.alert("Unable to delete the saved view.");
    }
  }

  function applyBuiltInView(view: OpportunityViewId) {
    setQuery("");
    setStageFilter("all");
    setPriorityFilter("all");
    setPipelineFilter("all");
    setForecastFilter("all");
    setOpportunityTypeFilter("all");
    setCloseDateFilter("all");
    setSortOption("updated_desc");

    if (view === "closing_this_month") {
      setCloseDateFilter("this_month");
    } else if (view === "high_priority") {
      setPriorityFilter("high");
    } else if (view === "commit_forecast") {
      setForecastFilter("commit");
    } else if (view === "won_deals") {
      setStageFilter("won");
    }

    setActiveCustomViewId(null);
    setActiveView(view);
    setSelectedOpportunityIds(new Set());
  }

  function clearFilters() {
    setQuery("");
    setStageFilter(
      "all"
    );
    setPriorityFilter(
      "all"
    );
    setPipelineFilter(
      "all"
    );
    setSortOption(
      "updated_desc"
    );
  }


  const hasFilters =
    query.trim() !== "" ||
    stageFilter !== "all" ||
    priorityFilter !==
      "all" ||
    pipelineFilter !==
      "all" ||
    sortOption !==
      "updated_desc";


  const selectedCount =
    selectedOpportunityIds.size;


  return (
    <div className="appShell">
      <Sidebar />

      <div className="appMain">
        <Topbar />

        <main className="pageContent">
          <div className="opportunityPageHeading">
            <div>
              <div className="pageEyebrow">
                Sales
              </div>

              <h1>
                Opportunities
              </h1>

              <p>
                Manage pipeline,
                forecasting and deal
                progression across your
                sales organization.
              </p>
            </div>

            <button
  type="button"
  className="createButton"
  onClick={() =>
    setCreateOpen(
      true
    )
  }
>
  <Plus size={17} />
  New Opportunity
</button>
          </div>


          <section className="opportunityKpiGrid">
            <article className="opportunityKpiCard">
              <div className="opportunityKpiIcon">
                <Handshake
                  size={18}
                />
              </div>

              <div className="opportunityKpiContent">
                <span>
                  Open Pipeline
                </span>

                <strong>
                  {formatCompactMoney(
                    metrics.pipelineValue
                  )}
                </strong>

                <small>
                  {
                    metrics.openCount
                  }{" "}
                  open{" "}
                  {metrics.openCount ===
                  1
                    ? "deal"
                    : "deals"}
                </small>
              </div>
            </article>


            <article className="opportunityKpiCard">
              <div className="opportunityKpiIcon">
                <Target
                  size={18}
                />
              </div>

              <div className="opportunityKpiContent">
                <span>
                  Weighted Pipeline
                </span>

                <strong>
                  {formatCompactMoney(
                    metrics.weightedValue
                  )}
                </strong>

                <small>
                  Probability adjusted
                </small>
              </div>
            </article>


            <article className="opportunityKpiCard">
              <div className="opportunityKpiIcon">
                <Trophy
                  size={18}
                />
              </div>

              <div className="opportunityKpiContent">
                <span>
                  Won Revenue
                </span>

                <strong>
                  {formatCompactMoney(
                    metrics.wonRevenue
                  )}
                </strong>

                <small>
                  {
                    metrics.wonCount
                  }{" "}
                  won{" "}
                  {metrics.wonCount ===
                  1
                    ? "deal"
                    : "deals"}
                </small>
              </div>
            </article>


            <article className="opportunityKpiCard">
              <div className="opportunityKpiIcon">
                <CircleDollarSign
                  size={18}
                />
              </div>

              <div className="opportunityKpiContent">
                <span>
                  Win Rate
                </span>

                <strong>
                  {metrics.winRate.toFixed(
                    1
                  )}
                  %
                </strong>

                <small>
                  {
                    metrics.wonCount
                  }{" "}
                  won /{" "}
                  {
                    metrics.lostCount
                  }{" "}
                  lost
                </small>
              </div>
            </article>


            <article className="opportunityKpiCard">
              <div className="opportunityKpiIcon">
                <CircleDollarSign
                  size={18}
                />
              </div>

              <div className="opportunityKpiContent">
                <span>
                  Average Deal Size
                </span>

                <strong>
                  {formatCompactMoney(
                    metrics.averageDealSize
                  )}
                </strong>

                <small>
                  Open opportunities
                </small>
              </div>
            </article>


            <article className="opportunityKpiCard">
              <div className="opportunityKpiIcon">
                <Target
                  size={18}
                />
              </div>

              <div className="opportunityKpiContent">
                <span>
                  Closing This Month
                </span>

                <strong>
                  {formatCompactMoney(
                    metrics.closingThisMonthValue
                  )}
                </strong>

                <small>
                  {
                    metrics.closingThisMonthCount
                  }{" "}
                  {metrics.closingThisMonthCount ===
                  1
                    ? "deal"
                    : "deals"}
                </small>
              </div>
            </article>
          </section>


          <section className="accountListCard opportunityListCard">
            <OpportunitySavedViews
                activeView={activeView}
                onSelectView={applyBuiltInView}
              />

              <div style={{
                display: "flex",
                justifyContent: "flex-end",
                padding: "12px 18px",
              }}>
                <button
                  type="button"
                  onClick={() => setSaveViewOpen(true)}
                  style={{
                    padding: "9px 14px",
                    borderRadius: 8,
                    border: "1px solid #6366f1",
                    color: "#6366f1",
                    background: "transparent",
                    cursor: "pointer",
                    fontWeight: 600,
                  }}
                >
                  + Save Current View
                </button>
              </div>

              <OpportunityCustomViews
                views={customViews}
                activeViewId={activeCustomViewId}
                onSelect={applyCustomView}
                onDelete={deleteCustomView}
              />

              <div className="opportunityToolbar">
  <div className="opportunityToolbarSearch">
    <Search size={16} />

    <input
      type="search"
      value={query}
      onChange={(event) =>
        setQuery(event.target.value)
      }
      placeholder="Search deals, accounts, stages..."
      aria-label="Search opportunities"
    />
  </div>

  <div className="opportunityToolbarControls">
    <div className="opportunityToolbarControl">
      <Filter size={15} />

      <select
        className="opportunityToolbarSelect"
        value={pipelineFilter}
        onChange={(event) =>
          setPipelineFilter(
            event.target.value
          )
        }
        aria-label="Filter by pipeline"
      >
        <option value="all">
          All Pipelines
        </option>

        {pipelines.map((pipeline) => (
          <option
            key={pipeline.id}
            value={pipeline.id}
          >
            {pipeline.name}
          </option>
        ))}
      </select>
    </div>

    <div className="opportunityToolbarControl">
      <select
        className="opportunityToolbarSelect"
        value={priorityFilter}
        onChange={(event) =>
          setPriorityFilter(
            event.target.value as PriorityFilter
          )
        }
        aria-label="Filter by priority"
      >
        <option value="all">
          All Priorities
        </option>

        {priorityFilters
          .filter(
            (priority) =>
              priority !== "all"
          )
          .map((priority) => (
            <option
              key={priority}
              value={priority}
            >
              {displayLabel(priority)}
            </option>
          ))}
      </select>
    </div>

    <div className="opportunityToolbarControl">
      <ArrowUpDown size={15} />

      <select
        className="opportunityToolbarSelect opportunityToolbarSort"
        value={sortOption}
        onChange={(event) =>
          setSortOption(
            event.target.value as SortOption
          )
        }
        aria-label="Sort opportunities"
      >
        <option value="updated_desc">
          Recently Updated
        </option>

        <option value="amount_desc">
          Amount: High to Low
        </option>

        <option value="amount_asc">
          Amount: Low to High
        </option>

        <option value="probability_desc">
          Highest Probability
        </option>

        <option value="close_asc">
          Close Date
        </option>
      </select>
    </div>
  </div>
</div>


            <div className="opportunityStageFilters">
              <div className="opportunityStageFilterGroup">
                <Filter
                  size={14}
                />

                {stageFilters.map(
                  (
                    filter
                  ) => (
                    <button
                      type="button"
                      key={
                        filter
                      }
                      className={
                        stageFilter ===
                        filter
                          ? "accountFilterButton active"
                          : "accountFilterButton"
                      }
                      onClick={() =>
                        setStageFilter(
                          filter
                        )
                      }
                    >
                      {displayLabel(
                        filter
                      )}
                    </button>
                  )
                )}
              </div>

              {hasFilters && (
                <button
                  type="button"
                  className="opportunityClearFilters"
                  onClick={
                    clearFilters
                  }
                >
                  Clear filters
                </button>
              )}
            </div>


            {/* Enterprise bulk action toolbar */}

              {bulkOwnerError && (
                <div role="alert" style={{
                  marginBottom: 12,
                  color: "#b91c1c",
                  fontSize: 13,
                }}>
                  {bulkOwnerError}
                </div>
              )}

              <OpportunityBulkOwnerDialog
                open={bulkOwnerOpen}
                selectedCount={selectedOpportunityIds.size}
                owners={bulkOwnerOptions}
                loading={bulkOwnerLoading}
                onClose={() => {
                  if (!bulkOwnerLoading) {
                    setBulkOwnerOpen(false);
                    setBulkOwnerError("");
                  }
                }}
                onConfirm={confirmBulkOwnerChange}
              />


              <OpportunityBulkStageDialog
                open={bulkStageOpen}
                selectedCount={selectedOpportunityIds.size}
                pipelines={bulkStagePipelines}
                loading={bulkStageLoading}
                error={bulkStageError}
                onClose={() => {
                  if (!bulkStageLoading) {
                    setBulkStageOpen(false);
                    setBulkStageError("");
                  }
                }}
                onConfirm={confirmBulkStageChange}
              />

              <OpportunityBulkToolbar
                selectedCount={selectedCount}
                visibleSelectedCount={visibleSelectedCount}
                visibleCount={visibleOpportunityIds.length}
                allVisibleSelected={allVisibleSelected}
                onSelectAllVisible={selectAllVisible}
                onClearSelection={clearSelection}
                onAction={handleBulkAction}
              />

              <div className="opportunityResultCount">
              {loading
                ? "Loading opportunities..."
                : `${visibleOpportunities.length} ${
                    visibleOpportunities.length ===
                    1
                      ? "opportunity"
                      : "opportunities"
                  }`}
            </div>


            {error && (
              <div className="accountState accountStateError">
                {error}
              </div>
            )}


            {!loading &&
              !error &&
              visibleOpportunities.length ===
                0 && (
                <div className="accountState">
                  <strong>
                    No opportunities found
                  </strong>

                  <span>
                    {hasFilters
                      ? "Try changing or clearing your filters."
                      : "Create your first opportunity to begin building your sales pipeline."}
                  </span>
                </div>
              )}


            {!loading &&
              !error &&
              visibleOpportunities.length >
                0 && (
                <OpportunityTable
                  opportunities={
                    visibleOpportunities
                  }
                  accountMap={
                    accountMap
                  }
                  pipelineMap={
                    pipelineMap
                  }
                  stageMap={
                    stageMap
                  }
                  selectedOpportunityIds={
                    selectedOpportunityIds
                  }
                  allVisibleSelected={
                    allVisibleSelected
                  }
                  someVisibleSelected={
                    someVisibleSelected
                  }
                  onToggleAllVisible={
                    toggleAllVisible
                  }
                  onToggleOpportunitySelection={
                    toggleOpportunitySelection
                  }
                />
              )}
          </section>
        </main>
      </div>


            <OpportunitySaveViewDialog
        open={saveViewOpen}
        onClose={() => setSaveViewOpen(false)}
        onSave={handleSaveCustomView}
        existingNames={customViews.map((view) => view.name)}
      />

      <CreateOpportunityModal
        open={createOpen}
        onClose={() =>
          setCreateOpen(false)
        }
        onCreated={(opportunity) => {
          setOpportunities(
            (current) => [
              opportunity,
              ...current,
            ]
          );

          setCreateOpen(false);
        }}
      />
    </div>
  );
}