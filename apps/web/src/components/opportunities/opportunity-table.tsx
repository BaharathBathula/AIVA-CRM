"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import Link from "next/link";

import {
  AlertTriangle,
  CalendarClock,
  CalendarDays,
  CheckCircle2,
  Circle,
  Clock3,
  ExternalLink,
  MinusCircle,
  MoreHorizontal,
  Pencil,
  TrendingUp,
  XCircle,
} from "lucide-react";

import type {
  Account,
} from "@/types/account";

import type {
  ForecastCategory,
  Opportunity,
  OpportunityPriority,
} from "@/types/opportunity";

import type {
  Pipeline,
  PipelineStage,
  PipelineStageCategory,
} from "@/types/pipeline";


interface OpportunityTableProps {
  opportunities: Opportunity[];

  accountMap: Map<
    string,
    Account
  >;

  pipelineMap: Map<
    string,
    Pipeline
  >;

  stageMap: Map<
    string,
    PipelineStage
  >;

  selectedOpportunityIds:
    Set<string>;

  allVisibleSelected:
    boolean;

  someVisibleSelected:
    boolean;

  onToggleAllVisible:
    () => void;

  onToggleOpportunitySelection:
    (
      opportunityId: string
    ) => void;
}


type CloseDateStatus =
  | "closed"
  | "missing"
  | "overdue"
  | "today"
  | "soon"
  | "on_track";


interface CloseDateIntelligence {
  status:
    CloseDateStatus;

  label:
    string;

  detail:
    string;

  daysUntilClose:
    number | null;
}


interface OpportunityRowActionsProps {
  opportunity:
    Opportunity;
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


function formatMoney(
  value:
    | string
    | number
    | null,
  currency = "USD"
) {
  if (
    value === null ||
    value === undefined
  ) {
    return "—";
  }

  const numericValue =
    Number(value);

  if (
    Number.isNaN(
      numericValue
    )
  ) {
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
    numericValue
  );
}


function formatDate(
  value:
    string | null
) {
  if (!value) {
    return "No date";
  }

  const date =
    new Date(
      `${value}T00:00:00`
    );

  if (
    Number.isNaN(
      date.getTime()
    )
  ) {
    return "Invalid date";
  }

  return new Intl.DateTimeFormat(
    "en-US",
    {
      month: "short",
      day: "numeric",
      year: "numeric",
    }
  ).format(
    date
  );
}


function startOfLocalDay(
  date: Date
) {
  return new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate()
  );
}


function getCloseDateIntelligence(
  expectedCloseDate:
    string | null,
  stageCategory:
    PipelineStageCategory
): CloseDateIntelligence {
  if (
    stageCategory === "won" ||
    stageCategory === "lost"
  ) {
    return {
      status:
        "closed",

      label:
        "Closed",

      detail:
        expectedCloseDate
          ? formatDate(
              expectedCloseDate
            )
          : "Deal closed",

      daysUntilClose:
        null,
    };
  }

  if (
    !expectedCloseDate
  ) {
    return {
      status:
        "missing",

      label:
        "No Close Date",

      detail:
        "Close date required",

      daysUntilClose:
        null,
    };
  }

  const closeDate =
    new Date(
      `${expectedCloseDate}T00:00:00`
    );

  if (
    Number.isNaN(
      closeDate.getTime()
    )
  ) {
    return {
      status:
        "missing",

      label:
        "No Close Date",

      detail:
        "Invalid close date",

      daysUntilClose:
        null,
    };
  }

  const today =
    startOfLocalDay(
      new Date()
    );

  const normalizedCloseDate =
    startOfLocalDay(
      closeDate
    );

  const millisecondsPerDay =
    24 *
    60 *
    60 *
    1000;

  const daysUntilClose =
    Math.round(
      (
        normalizedCloseDate.getTime() -
        today.getTime()
      ) /
        millisecondsPerDay
    );

  if (
    daysUntilClose < 0
  ) {
    const daysOverdue =
      Math.abs(
        daysUntilClose
      );

    return {
      status:
        "overdue",

      label:
        "Overdue",

      detail:
        daysOverdue === 1
          ? "1 day overdue"
          : `${daysOverdue} days overdue`,

      daysUntilClose,
    };
  }

  if (
    daysUntilClose === 0
  ) {
    return {
      status:
        "today",

      label:
        "Due Today",

      detail:
        "Expected to close today",

      daysUntilClose,
    };
  }

  if (
    daysUntilClose <= 7
  ) {
    return {
      status:
        "soon",

      label:
        "Due Soon",

      detail:
        daysUntilClose === 1
          ? "Closes tomorrow"
          : `Closes in ${daysUntilClose} days`,

      daysUntilClose,
    };
  }

  return {
    status:
      "on_track",

    label:
      "On Track",

    detail:
      `Closes in ${daysUntilClose} days`,

    daysUntilClose,
  };
}


function getPriorityClass(
  priority:
    OpportunityPriority
) {
  return (
    "opportunityPriority " +
    `opportunityPriority-${priority}`
  );
}


function getStageClass(
  category:
    PipelineStageCategory
) {
  return (
    "opportunityStageBadge " +
    `opportunityStageBadge-${category}`
  );
}


function getForecastClass(
  category:
    ForecastCategory
) {
  return (
    "opportunityForecast " +
    `opportunityForecast-${category}`
  );
}


function getProbabilityClass(
  probability:
    number
) {
  if (
    probability >= 80
  ) {
    return (
      "opportunityProbability-high"
    );
  }

  if (
    probability >= 50
  ) {
    return (
      "opportunityProbability-medium"
    );
  }

  return (
    "opportunityProbability-low"
  );
}


function StageIcon({
  category,
}: {
  category:
    PipelineStageCategory;
}) {
  if (
    category === "won"
  ) {
    return (
      <CheckCircle2
        size={13}
      />
    );
  }

  if (
    category === "lost"
  ) {
    return (
      <XCircle
        size={13}
      />
    );
  }

  return (
    <Clock3
      size={13}
    />
  );
}


function PriorityIcon({
  priority,
}: {
  priority:
    OpportunityPriority;
}) {
  if (
    priority ===
    "critical"
  ) {
    return (
      <AlertTriangle
        size={12}
      />
    );
  }

  if (
    priority === "high"
  ) {
    return (
      <TrendingUp
        size={12}
      />
    );
  }

  return (
    <Circle
      size={9}
    />
  );
}


function ForecastIcon({
  category,
}: {
  category:
    ForecastCategory;
}) {
  if (
    category ===
    "closed"
  ) {
    return (
      <CheckCircle2
        size={12}
      />
    );
  }

  if (
    category ===
      "commit" ||
    category ===
      "best_case"
  ) {
    return (
      <TrendingUp
        size={12}
      />
    );
  }

  if (
    category ===
    "omitted"
  ) {
    return (
      <MinusCircle
        size={12}
      />
    );
  }

  return (
    <Circle
      size={9}
    />
  );
}


function CloseDateIcon({
  status,
}: {
  status:
    CloseDateStatus;
}) {
  if (
    status ===
    "overdue"
  ) {
    return (
      <AlertTriangle
        size={12}
      />
    );
  }

  if (
    status ===
      "today" ||
    status ===
      "soon"
  ) {
    return (
      <CalendarClock
        size={12}
      />
    );
  }

  if (
    status ===
    "closed"
  ) {
    return (
      <CheckCircle2
        size={12}
      />
    );
  }

  if (
    status ===
    "missing"
  ) {
    return (
      <MinusCircle
        size={12}
      />
    );
  }

  return (
    <CalendarDays
      size={12}
    />
  );
}


function OpportunityRowActions({
  opportunity,
}: OpportunityRowActionsProps) {
  const [
    open,
    setOpen,
  ] = useState(
    false
  );

  const menuRef =
    useRef<
      HTMLDivElement
    >(null);


  useEffect(() => {
    if (!open) {
      return;
    }

    function handlePointerDown(
      event:
        MouseEvent
    ) {
      const target =
        event.target;

      if (
        target instanceof Node &&
        !menuRef.current?.contains(
          target
        )
      ) {
        setOpen(
          false
        );
      }
    }


    function handleKeyDown(
      event:
        KeyboardEvent
    ) {
      if (
        event.key ===
        "Escape"
      ) {
        setOpen(
          false
        );
      }
    }


    document.addEventListener(
      "mousedown",
      handlePointerDown
    );

    document.addEventListener(
      "keydown",
      handleKeyDown
    );


    return () => {
      document.removeEventListener(
        "mousedown",
        handlePointerDown
      );

      document.removeEventListener(
        "keydown",
        handleKeyDown
      );
    };
  }, [
    open,
  ]);


  const opportunityUrl =
    `/opportunities/${opportunity.id}`;


  return (
    <div
      className="opportunityRowActions"
      ref={
        menuRef
      }
    >
      <button
        type="button"
        className={
          open
            ? "opportunityRowActionsTrigger active"
            : "opportunityRowActionsTrigger"
        }
        onClick={() =>
          setOpen(
            (current) =>
              !current
          )
        }
        aria-label={
          `Actions for ${opportunity.name}`
        }
        aria-haspopup="menu"
        aria-expanded={
          open
        }
      >
        <MoreHorizontal
          size={17}
        />
      </button>

      {open && (
        <div
          className="opportunityRowActionsMenu"
          role="menu"
        >
          <div className="opportunityRowActionsHeader">
            <strong>
              {
                opportunity.name
              }
            </strong>

            <span>
              {displayLabel(
                opportunity.opportunity_type
              )}
            </span>
          </div>

          <div className="opportunityRowActionsDivider" />

          <Link
            href={
              opportunityUrl
            }
            className="opportunityRowActionItem"
            role="menuitem"
            onClick={() =>
              setOpen(
                false
              )
            }
          >
            <ExternalLink
              size={15}
            />

            <span>
              Open Opportunity
            </span>
          </Link>

          <Link
            href={
              opportunityUrl
            }
            className="opportunityRowActionItem"
            role="menuitem"
            onClick={() =>
              setOpen(
                false
              )
            }
          >
            <Pencil
              size={15}
            />

            <span>
              View / Edit Details
            </span>
          </Link>
        </div>
      )}
    </div>
  );
}



type OpportunitySortKey =
  | "name"
  | "account"
  | "stage"
  | "priority"
  | "amount"
  | "weighted"
  | "probability"
  | "forecast"
  | "closeDate"
  | "nextStep";

const priorityRank: Record<string, number> = {
  low: 1,
  medium: 2,
  high: 3,
  critical: 4,
};

function compareSortValues(
  a: string | number | null | undefined,
  b: string | number | null | undefined
): number {
  if (a == null || a === "") return b == null || b === "" ? 0 : 1;
  if (b == null || b === "") return -1;

  if (typeof a === "number" && typeof b === "number") {
    return a - b;
  }

  return String(a).localeCompare(String(b), undefined, {
    numeric: true,
    sensitivity: "base",
  });
}

export function OpportunityTable({
  opportunities,
  accountMap,
  pipelineMap,
  stageMap,
  selectedOpportunityIds,
  allVisibleSelected,
  someVisibleSelected,
  onToggleAllVisible,
  onToggleOpportunitySelection,
}: OpportunityTableProps) {
  const [sortKey, setSortKey] = useState<OpportunitySortKey | null>(null);
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("asc");

  const sortedOpportunities = useMemo(() => {
    if (!sortKey) return opportunities;

    function valueFor(opportunity: Opportunity): string | number | null {
      switch (sortKey) {
        case "name":
          return opportunity.name;
        case "account":
          return accountMap.get(opportunity.account_id)?.name ?? null;
        case "stage":
          return stageMap.get(opportunity.stage_id)?.name ?? null;
        case "priority":
          return priorityRank[opportunity.priority] ?? 0;
        case "amount":
          return opportunity.amount == null ? null : Number(opportunity.amount);
        case "weighted":
          return opportunity.weighted_amount == null
            ? null
            : Number(opportunity.weighted_amount);
        case "probability":
          return opportunity.probability;
        case "forecast":
          return opportunity.forecast_category;
        case "closeDate":
          return opportunity.expected_close_date;
        case "nextStep":
          return opportunity.next_step;
        default:
          return null;
      }
    }

    return [...opportunities].sort((a, b) => {
      const comparison = compareSortValues(valueFor(a), valueFor(b));
      return sortDirection === "asc" ? comparison : -comparison;
    });
  }, [opportunities, sortKey, sortDirection, accountMap, stageMap]);

  function toggleSort(key: OpportunitySortKey) {
    if (sortKey === key) {
      setSortDirection(current => current === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDirection("asc");
    }
  }

  function sortHeader(label: string, key: OpportunitySortKey) {
    const active = sortKey === key;

    return (
      <th
        aria-sort={
          active
            ? sortDirection === "asc"
              ? "ascending"
              : "descending"
            : "none"
        }
      >
        <button
          type="button"
          onClick={() => toggleSort(key)}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: 0,
            border: 0,
            background: "transparent",
            color: "inherit",
            font: "inherit",
            fontWeight: "inherit",
            cursor: "pointer",
          }}
        >
          {label}
          <span aria-hidden="true">
            {active ? (sortDirection === "asc" ? "↑" : "↓") : "↕"}
          </span>
        </button>
      </th>
    );
  }

  return (
    <div className="accountTableWrapper opportunityTableWrapper aiva-opportunities-table-wrapper">
      <table className="accountTable opportunityTable aiva-opportunities-table">
        <thead>
          <tr>
            <th className="opportunityCheckboxCell">
              <input
                type="checkbox"
                checked={
                  allVisibleSelected
                }
                ref={(
                  element
                ) => {
                  if (
                    element
                  ) {
                    element.indeterminate =
                      someVisibleSelected &&
                      !allVisibleSelected;
                  }
                }}
                onChange={
                  onToggleAllVisible
                }
                aria-label="Select all visible opportunities"
              />
            </th>

            {sortHeader("Opportunity", "name")}

            {sortHeader("Account", "account")}

            {sortHeader("Stage", "stage")}

            {sortHeader("Priority", "priority")}

            {sortHeader("Amount", "amount")}

            {sortHeader("Weighted", "weighted")}

            {sortHeader("Probability", "probability")}

            {sortHeader("Forecast", "forecast")}

            {sortHeader("Close Date", "closeDate")}

            {sortHeader("Next Step", "nextStep")}

            <th
              className="opportunityActionsColumn"
              aria-label="Actions"
            />
          </tr>
        </thead>

        <tbody>
          {sortedOpportunities.map(
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

              const isSelected =
                selectedOpportunityIds.has(
                  opportunity.id
                );

              const initial =
                opportunity.name
                  .trim()
                  .charAt(0)
                  .toUpperCase() ||
                "O";

              const stageCategory =
                stage?.category ??
                "open";

              const probabilityClass =
                getProbabilityClass(
                  opportunity.probability
                );

              const closeDate =
                getCloseDateIntelligence(
                  opportunity.expected_close_date,
                  stageCategory
                );


              return (
                <tr
                  key={
                    opportunity.id
                  }
                  className={[
                    isSelected
                      ? "opportunityRowSelected"
                      : "",

                    `opportunityRow-${stageCategory}`,

                    closeDate.status ===
                    "overdue"
                      ? "opportunityRow-overdue"
                      : "",
                  ]
                    .filter(
                      Boolean
                    )
                    .join(
                      " "
                    )}
                >
                  <td className="opportunityCheckboxCell">
                    <input
                      type="checkbox"
                      checked={
                        isSelected
                      }
                      onChange={() =>
                        onToggleOpportunitySelection(
                          opportunity.id
                        )
                      }
                      aria-label={
                        `Select ${opportunity.name}`
                      }
                    />
                  </td>

                  <td style={{ width: 360, minWidth: 360 }}>
                    <Link
                      href={
                        `/opportunities/${opportunity.id}`
                      }
                      className="opportunityIdentity" style={{ width: "100%", maxWidth: "none" }}
                    >
                      <div className="opportunityIdentityAvatar">
                        {
                          initial
                        }
                      </div>

                      <div className="opportunityIdentityContent aiva-opportunity-name" style={{ width: "100%", maxWidth: "none" }}>
                        <strong
                          style={{
                            width: "100%",
                            maxWidth: "none",
                            whiteSpace: "normal",
                            overflow: "visible",
                            textOverflow: "clip",
                            overflowWrap: "anywhere",
                          }}
                        >
                          {opportunity.name}
                        </strong>

                        <div className="opportunityIdentityMeta">
                          <span className="opportunityTypeBadge">
                            {displayLabel(
                              opportunity.opportunity_type
                            )}
                          </span>

                          {opportunity.lead_source && (
                            <span>
                              {
                                opportunity.lead_source
                              }
                            </span>
                          )}
                        </div>
                      </div>
                    </Link>
                  </td>

                  <td>
                    <div className="opportunityAccountIdentity">
                      <strong>
                        {account?.name ??
                          "Unknown Account"}
                      </strong>

                      <span>
                        {pipeline?.name ??
                          "No pipeline"}
                      </span>
                    </div>
                  </td>

                  <td>
                    <div className="opportunityStageIdentity">
                      <span
                        className={
                          getStageClass(
                            stageCategory
                          )
                        }
                      >
                        <StageIcon
                          category={
                            stageCategory
                          }
                        />

                        {stage?.name ??
                          "Unknown"}
                      </span>

                      <small>
                        {pipeline?.name ??
                          "Pipeline"}
                      </small>
                    </div>
                  </td>

                  <td>
                    <span
                      className={
                        getPriorityClass(
                          opportunity.priority
                        )
                      }
                    >
                      <PriorityIcon
                        priority={
                          opportunity.priority
                        }
                      />

                      {displayLabel(
                        opportunity.priority
                      )}
                    </span>
                  </td>

                  <td>
                    <strong className="opportunityAmount">
                      {formatMoney(
                        opportunity.amount,
                        opportunity.currency
                      )}
                    </strong>
                  </td>

                  <td>
                    <span className="opportunityWeightedAmount">
                      {formatMoney(
                        opportunity.weighted_amount,
                        opportunity.currency
                      )}
                    </span>
                  </td>

                  <td>
                    <div
                      className={
                        `opportunityProbability ${probabilityClass}`
                      }
                    >
                      <div className="opportunityProbabilityHeader">
                        <span>
                          {
                            opportunity.probability
                          }
                          %
                        </span>
                      </div>

                      <div className="opportunityProbabilityTrack">
                        <div
                          className="opportunityProbabilityFill"
                          style={{
                            width:
                              `${Math.min(
                                Math.max(
                                  opportunity.probability,
                                  0
                                ),
                                100
                              )}%`,
                          }}
                        />
                      </div>
                    </div>
                  </td>

                  <td>
                    <span
                      className={
                        getForecastClass(
                          opportunity.forecast_category
                        )
                      }
                    >
                      <ForecastIcon
                        category={
                          opportunity.forecast_category
                        }
                      />

                      {displayLabel(
                        opportunity.forecast_category
                      )}
                    </span>
                  </td>

                  <td>
                    <div className="opportunityCloseDateIntelligence">
                      <span
                        className={
                          `opportunityCloseDateStatus opportunityCloseDateStatus-${closeDate.status}`
                        }
                      >
                        <CloseDateIcon
                          status={
                            closeDate.status
                          }
                        />

                        {
                          closeDate.label
                        }
                      </span>

                      <span className="opportunityCloseDateValue">
                        {opportunity.expected_close_date
                          ? formatDate(
                              opportunity.expected_close_date
                            )
                          : "Not set"}
                      </span>

                      <small
                        className={
                          `opportunityCloseDateDetail opportunityCloseDateDetail-${closeDate.status}`
                        }
                      >
                        {
                          closeDate.detail
                        }
                      </small>
                    </div>
                  </td>

                  <td>
                    <span
                      className="opportunityNextStep"
                      title={
                        opportunity.next_step ??
                        undefined
                      }
                    >
                      {opportunity.next_step ??
                        "—"}
                    </span>
                  </td>

                  <td className="opportunityActionsColumn">
                    <OpportunityRowActions
                      opportunity={
                        opportunity
                      }
                    />
                  </td>
                </tr>
              );
            }
          )}
        </tbody>
      </table>
    </div>
  );
}