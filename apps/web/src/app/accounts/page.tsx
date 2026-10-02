"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import Link from "next/link";

import {
  Building2,
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


const filters: Array<
  "all" | LifecycleStage
> = [
  "all",
  "prospect",
  "lead",
  "customer",
  "partner",
];


function formatRevenue(
  revenue: string | null
) {
  if (!revenue) {
    return "—";
  }

  const value = Number(revenue);

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


export default function AccountsPage() {
  const [
    accounts,
    setAccounts,
  ] = useState<Account[]>([]);

  const [loading, setLoading] =
    useState(true);

  const [error, setError] =
    useState<string | null>(null);

  const [query, setQuery] =
    useState("");

  const [filter, setFilter] =
    useState<
      "all" | LifecycleStage
    >("all");

  const [
    createOpen,
    setCreateOpen,
  ] = useState(false);


  useEffect(() => {
    async function load() {
      try {
        setLoading(true);

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

    load();
  }, []);


  const filteredAccounts =
    useMemo(() => {
      const normalized =
        query.trim().toLowerCase();

      return accounts.filter(
        (account) => {
          const matchesFilter =
            filter === "all" ||
            account.lifecycle_stage
              === filter;

          const matchesSearch =
            !normalized ||
            account.name
              .toLowerCase()
              .includes(normalized) ||
            account.domain
              ?.toLowerCase()
              .includes(normalized) ||
            account.industry
              ?.toLowerCase()
              .includes(normalized);

          return (
            matchesFilter &&
            Boolean(matchesSearch)
          );
        }
      );
    }, [
      accounts,
      filter,
      query,
    ]);


  function addCreatedAccount(
    account: Account
  ) {
    setAccounts(
      (current) =>
        [
          ...current,
          account,
        ].sort(
          (a, b) =>
            a.name.localeCompare(
              b.name
            )
        )
    );
  }


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
                Companies and organizations
                managed across AIVA CRM.
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

          <div className="accountSummary">
            <div>
              <Building2 size={18} />

              <span>
                Total Accounts
              </span>

              <strong>
                {accounts.length}
              </strong>
            </div>

            <div>
              <Users size={18} />

              <span>
                Customers
              </span>

              <strong>
                {
                  accounts.filter(
                    (account) =>
                      account.lifecycle_stage
                      === "customer"
                  ).length
                }
              </strong>
            </div>

            <div>
              <Globe2 size={18} />

              <span>
                Prospects
              </span>

              <strong>
                {
                  accounts.filter(
                    (account) =>
                      account.lifecycle_stage
                      === "prospect"
                  ).length
                }
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
                  placeholder="Search accounts..."
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
                      {item}
                    </button>
                  )
                )}
              </div>
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

            {!loading &&
              !error &&
              filteredAccounts.length
                === 0 && (
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
                    Create your first customer
                    account in AIVA CRM.
                  </p>

                  <button
                    type="button"
                    className="createButton"
                    onClick={() =>
                      setCreateOpen(true)
                    }
                  >
                    <Plus size={15} />
                    New Account
                  </button>
                </div>
              )}

            {!loading &&
              !error &&
              filteredAccounts.length
                > 0 && (
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
                          Stage
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
                      {filteredAccounts.map(
                        (account) => (
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
                                      .name[0]
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
                                      || "No domain"
                                    }
                                  </span>
                                </div>
                              </Link>
                            </td>

                            <td>
                              {
                                account.industry
                                || "—"
                              }
                            </td>

                            <td>
                              <span
                                className={
                                  `stageBadge stage-${account.lifecycle_stage}`
                                }
                              >
                                {
                                  account.lifecycle_stage
                                }
                              </span>
                            </td>

                            <td>
                              {
                                account.employee_count
                                ?? "—"
                              }
                            </td>

                            <td>
                              {formatRevenue(
                                account.annual_revenue
                              )}
                            </td>

                            <td>
                              {
                                [
                                  account.billing_city,
                                  account.billing_state,
                                ]
                                  .filter(
                                    Boolean
                                  )
                                  .join(", ")
                                || "—"
                              }
                            </td>
                          </tr>
                        )
                      )}
                    </tbody>
                  </table>
                </div>
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
