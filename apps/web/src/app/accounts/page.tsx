"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import Link from "next/link";

import {
  Building2,
  CircleDollarSign,
  Filter,
  Globe2,
  Plus,
  Search,
  Users,
} from "lucide-react";

import {
  CreateAccountModal,
} from "@/components/accounts/create-account-modal";

import {
  Sidebar,
} from "@/components/sidebar";

import {
  Topbar,
} from "@/components/topbar";

import {
  getAccounts,
} from "@/lib/accounts";

import type {
  Account,
  LifecycleStage,
} from "@/types/account";


type LifecycleFilter =
  | "all"
  | LifecycleStage;


type SortOption =
  | "name_asc"
  | "name_desc"
  | "revenue_desc"
  | "revenue_asc"
  | "employees_desc"
  | "updated_desc";


const lifecycleFilters:
  LifecycleFilter[] = [
    "all",
    "prospect",
    "lead",
    "customer",
    "partner",
    "inactive",
    "churned",
  ];


const PAGE_SIZE = 10;


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


function formatRevenue(
  revenue: string | null
) {
  if (!revenue) {
    return "—";
  }

  const value = Number(revenue);

  if (
    Number.isNaN(value)
  ) {
    return "—";
  }

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


function formatEmployeeCount(
  value: number | null
) {
  if (
    value === null
    ||
    value === undefined
  ) {
    return "—";
  }

  return new Intl.NumberFormat(
    "en-US"
  ).format(value);
}


function formatLocation(
  account: Account
) {
  const location = [
    account.billing_city,
    account.billing_state,
    account.billing_country,
  ]
    .filter(Boolean)
    .join(", ");

  return location || "—";
}


function accountOwnerLabel(
  account: Account
) {
  if (!account.owner_user_id) {
    return "Unassigned";
  }

  return "Assigned";
}


export default function AccountsPage() {
  const [
    accounts,
    setAccounts,
  ] = useState<Account[]>([]);

  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    error,
    setError,
  ] = useState<string | null>(
    null
  );

  const [
    query,
    setQuery,
  ] = useState("");

  const [
    lifecycleFilter,
    setLifecycleFilter,
  ] = useState<
    LifecycleFilter
  >("all");

  const [
    industryFilter,
    setIndustryFilter,
  ] = useState("all");

  const [
    sortOption,
    setSortOption,
  ] = useState<SortOption>(
    "name_asc"
  );

  const [
    currentPage,
    setCurrentPage,
  ] = useState(1);

  const [
    createOpen,
    setCreateOpen,
  ] = useState(false);


  useEffect(() => {
    async function loadAccounts() {
      try {
        setLoading(true);
        setError(null);

        const data =
          await getAccounts();

        setAccounts(data);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Unable to load accounts."
        );
      } finally {
        setLoading(false);
      }
    }

    void loadAccounts();
  }, []);


  useEffect(() => {
    setCurrentPage(1);
  }, [
    query,
    lifecycleFilter,
    industryFilter,
    sortOption,
  ]);


  const industries =
    useMemo(() => {
      return Array.from(
        new Set(
          accounts
            .map(
              (account) =>
                account.industry
                  ?.trim()
            )
            .filter(
              (
                value
              ): value is string =>
                Boolean(value)
            )
        )
      ).sort(
        (a, b) =>
          a.localeCompare(b)
      );
    }, [accounts]);


  const totalRevenue =
    useMemo(() => {
      return accounts.reduce(
        (total, account) => {
          const revenue =
            Number(
              account.annual_revenue
              ?? 0
            );

          return (
            total
            +
            (
              Number.isFinite(revenue)
                ? revenue
                : 0
            )
          );
        },
        0
      );
    }, [accounts]);


  const customerCount =
    useMemo(
      () =>
        accounts.filter(
          (account) =>
            account.lifecycle_stage
            === "customer"
        ).length,
      [accounts]
    );


  const prospectCount =
    useMemo(
      () =>
        accounts.filter(
          (account) =>
            account.lifecycle_stage
            === "prospect"
        ).length,
      [accounts]
    );


  const filteredAccounts =
    useMemo(() => {
      const normalizedQuery =
        query
          .trim()
          .toLowerCase();

      const filtered =
        accounts.filter(
          (account) => {
            const lifecycleMatches =
              lifecycleFilter
                === "all"
              ||
              account.lifecycle_stage
                === lifecycleFilter;

            const industryMatches =
              industryFilter
                === "all"
              ||
              account.industry
                === industryFilter;

            const searchable = [
              account.name,
              account.domain,
              account.website,
              account.industry,
              account.phone,
              account.billing_city,
              account.billing_state,
              account.billing_country,
            ]
              .filter(Boolean)
              .join(" ")
              .toLowerCase();

            const searchMatches =
              !normalizedQuery
              ||
              searchable.includes(
                normalizedQuery
              );

            return (
              lifecycleMatches
              &&
              industryMatches
              &&
              searchMatches
            );
          }
        );

      return [
        ...filtered,
      ].sort(
        (a, b) => {
          if (
            sortOption
            === "name_asc"
          ) {
            return a.name.localeCompare(
              b.name
            );
          }

          if (
            sortOption
            === "name_desc"
          ) {
            return b.name.localeCompare(
              a.name
            );
          }

          if (
            sortOption
            === "revenue_desc"
          ) {
            return (
              Number(
                b.annual_revenue
                ?? 0
              )
              -
              Number(
                a.annual_revenue
                ?? 0
              )
            );
          }

          if (
            sortOption
            === "revenue_asc"
          ) {
            return (
              Number(
                a.annual_revenue
                ?? 0
              )
              -
              Number(
                b.annual_revenue
                ?? 0
              )
            );
          }

          if (
            sortOption
            === "employees_desc"
          ) {
            return (
              (
                b.employee_count
                ?? 0
              )
              -
              (
                a.employee_count
                ?? 0
              )
            );
          }

          return (
            new Date(
              b.updated_at
            ).getTime()
            -
            new Date(
              a.updated_at
            ).getTime()
          );
        }
      );
    }, [
      accounts,
      query,
      lifecycleFilter,
      industryFilter,
      sortOption,
    ]);


  const totalPages =
    Math.max(
      1,
      Math.ceil(
        filteredAccounts.length
        /
        PAGE_SIZE
      )
    );


  const paginatedAccounts =
    useMemo(() => {
      const start =
        (
          currentPage - 1
        )
        *
        PAGE_SIZE;

      return filteredAccounts.slice(
        start,
        start + PAGE_SIZE
      );
    }, [
      filteredAccounts,
      currentPage,
    ]);


  function addCreatedAccount(
    account: Account
  ) {
    setAccounts(
      (current) => [
        account,
        ...current,
      ]
    );

    setCurrentPage(1);
  }


  function clearFilters() {
    setQuery("");
    setLifecycleFilter("all");
    setIndustryFilter("all");
    setSortOption("name_asc");
    setCurrentPage(1);
  }


  const hasActiveFilters =
    Boolean(query.trim())
    ||
    lifecycleFilter !== "all"
    ||
    industryFilter !== "all";


  return (
    <div className="appShell">
      <Sidebar active="Accounts" />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <div className="pageHeading accountsHeading">
            <div>
              <p className="eyebrow">
                Customer Data
              </p>

              <h1>
                Accounts
              </h1>

              <p>
                Manage companies,
                customers and organizations
                across AIVA CRM.
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
              New Account
            </button>
          </div>


          <div className="statsGrid">
            <div className="statCard">
              <div className="statLabel">
                Total Accounts
              </div>

              <div className="statValue">
                {accounts.length}
              </div>

              <div className="statMeta">
                <Building2
                  size={14}
                />

                Companies in CRM
              </div>
            </div>


            <div className="statCard">
              <div className="statLabel">
                Customers
              </div>

              <div className="statValue">
                {customerCount}
              </div>

              <div className="statMeta">
                <Users
                  size={14}
                />

                Active customer accounts
              </div>
            </div>


            <div className="statCard">
              <div className="statLabel">
                Prospects
              </div>

              <div className="statValue">
                {prospectCount}
              </div>

              <div className="statMeta">
                <Globe2
                  size={14}
                />

                Potential customers
              </div>
            </div>


            <div className="statCard">
              <div className="statLabel">
                Account Revenue
              </div>

              <div className="statValue">
                {formatRevenue(
                  String(
                    totalRevenue
                  )
                )}
              </div>

              <div className="statMeta">
                <CircleDollarSign
                  size={14}
                />

                Recorded annual revenue
              </div>
            </div>
          </div>


          <section
            className="accountsPanel"
            style={{
              marginTop: "16px",
            }}
          >
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
                  placeholder="Search name, domain, industry, city..."
                />
              </div>

              <div className="accountFilters">
                <Filter size={15} />

                <select
                  className="filterChip"
                  value={industryFilter}
                  onChange={(event) =>
                    setIndustryFilter(
                      event.target.value
                    )
                  }
                  aria-label="Filter by industry"
                >
                  <option value="all">
                    All industries
                  </option>

                  {industries.map(
                    (industry) => (
                      <option
                        key={industry}
                        value={industry}
                      >
                        {industry}
                      </option>
                    )
                  )}
                </select>

                <select
                  className="filterChip"
                  value={sortOption}
                  onChange={(event) =>
                    setSortOption(
                      event.target
                        .value as SortOption
                    )
                  }
                  aria-label="Sort accounts"
                >
                  <option value="name_asc">
                    Name A–Z
                  </option>

                  <option value="name_desc">
                    Name Z–A
                  </option>

                  <option value="revenue_desc">
                    Revenue high–low
                  </option>

                  <option value="revenue_asc">
                    Revenue low–high
                  </option>

                  <option value="employees_desc">
                    Employees high–low
                  </option>

                  <option value="updated_desc">
                    Recently updated
                  </option>
                </select>

                {hasActiveFilters && (
                  <button
                    type="button"
                    className="filterChip"
                    onClick={
                      clearFilters
                    }
                  >
                    Clear
                  </button>
                )}
              </div>
            </div>


            <div
              className="accountToolbar"
              style={{
                justifyContent:
                  "flex-start",
                overflowX: "auto",
              }}
            >
              {lifecycleFilters.map(
                (item) => (
                  <button
                    key={item}
                    type="button"
                    className={
                      lifecycleFilter
                        === item
                        ? "filterChip active"
                        : "filterChip"
                    }
                    onClick={() =>
                      setLifecycleFilter(
                        item
                      )
                    }
                  >
                    {displayLabel(item)}
                  </button>
                )
              )}
            </div>


            {loading && (
              <div className="tableState">
                Loading accounts...
              </div>
            )}


            {error && !loading && (
              <div className="tableState errorState">
                <strong>
                  Accounts unavailable
                </strong>

                <span>
                  {error}
                </span>
              </div>
            )}


            {!loading
              &&
              !error
              &&
              filteredAccounts.length
                === 0
              && (
                <div className="emptyState">
                  <div className="emptyStateIcon">
                    <Building2
                      size={23}
                    />
                  </div>

                  <h3>
                    No accounts found
                  </h3>

                  <p>
                    {hasActiveFilters
                      ? "No accounts match the current filters."
                      : "Create your first account in AIVA CRM."}
                  </p>

                  {hasActiveFilters ? (
                    <button
                      type="button"
                      className="secondaryButton"
                      onClick={
                        clearFilters
                      }
                    >
                      Clear Filters
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="createButton"
                      onClick={() =>
                        setCreateOpen(
                          true
                        )
                      }
                    >
                      <Plus
                        size={15}
                      />
                      New Account
                    </button>
                  )}
                </div>
              )}


            {!loading
              &&
              !error
              &&
              filteredAccounts.length
                > 0
              && (
                <>
                  <div className="accountTableWrapper">
                    <table className="accountTable">
                      <thead>
                        <tr>
                          <th>
                            Account
                          </th>

                          <th>
                            Industry
                          </th>

                          <th>
                            Lifecycle
                          </th>

                          <th>
                            Owner
                          </th>

                          <th>
                            Employees
                          </th>

                          <th>
                            Revenue
                          </th>

                          <th>
                            Location
                          </th>
                        </tr>
                      </thead>

                      <tbody>
                        {paginatedAccounts.map(
                          (
                            account
                          ) => (
                            <tr
                              key={
                                account.id
                              }
                            >
                              <td>
                                <Link
                                  className="accountIdentity"
                                  href={
                                    `/accounts/${account.id}`
                                  }
                                >
                                  <div className="accountLogo">
                                    {
                                      account
                                        .name
                                        .charAt(
                                          0
                                        )
                                        .toUpperCase()
                                    }
                                  </div>

                                  <div>
                                    <strong>
                                      {
                                        account.name
                                      }
                                    </strong>

                                    <span>
                                      {
                                        account.domain
                                        ||
                                        "No domain"
                                      }
                                    </span>
                                  </div>
                                </Link>
                              </td>

                              <td>
                                {
                                  account.industry
                                  ||
                                  "—"
                                }
                              </td>

                              <td>
                                <span
                                  className={
                                    `stageBadge stage-${account.lifecycle_stage}`
                                  }
                                >
                                  {
                                    displayLabel(
                                      account.lifecycle_stage
                                    )
                                  }
                                </span>
                              </td>

                              <td>
                                {
                                  accountOwnerLabel(
                                    account
                                  )
                                }
                              </td>

                              <td>
                                {
                                  formatEmployeeCount(
                                    account.employee_count
                                  )
                                }
                              </td>

                              <td>
                                {
                                  formatRevenue(
                                    account.annual_revenue
                                  )
                                }
                              </td>

                              <td>
                                {
                                  formatLocation(
                                    account
                                  )
                                }
                              </td>
                            </tr>
                          )
                        )}
                      </tbody>
                    </table>
                  </div>


                  <div className="accountToolbar">
                    <span
                      style={{
                        color:
                          "var(--muted)",
                        fontSize:
                          "10px",
                      }}
                    >
                      Showing{" "}
                      {
                        (
                          currentPage
                          - 1
                        )
                        *
                        PAGE_SIZE
                        +
                        1
                      }
                      {"–"}
                      {
                        Math.min(
                          currentPage
                            *
                            PAGE_SIZE,
                          filteredAccounts.length
                        )
                      }
                      {" of "}
                      {
                        filteredAccounts.length
                      }
                      {" accounts"}
                    </span>

                    <div className="accountFilters">
                      <button
                        type="button"
                        className="secondaryButton"
                        disabled={
                          currentPage
                          <= 1
                        }
                        onClick={() =>
                          setCurrentPage(
                            (
                              current
                            ) =>
                              Math.max(
                                1,
                                current
                                  - 1
                              )
                          )
                        }
                      >
                        Previous
                      </button>

                      <span
                        className="filterChip active"
                      >
                        Page{" "}
                        {currentPage}
                        {" of "}
                        {totalPages}
                      </span>

                      <button
                        type="button"
                        className="secondaryButton"
                        disabled={
                          currentPage
                          >= totalPages
                        }
                        onClick={() =>
                          setCurrentPage(
                            (
                              current
                            ) =>
                              Math.min(
                                totalPages,
                                current
                                  + 1
                              )
                          )
                        }
                      >
                        Next
                      </button>
                    </div>
                  </div>
                </>
              )}
          </section>
        </div>
      </main>


      <CreateAccountModal
        open={createOpen}
        onClose={() =>
          setCreateOpen(false)
        }
        onCreated={
          addCreatedAccount
        }
      />
    </div>
  );
}
