"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import Link from "next/link";

import {
  Filter,
  Plus,
  Search,
  Target,
  UserCheck,
  Users,
} from "lucide-react";

import {
  CreateLeadModal,
} from "@/components/leads/create-lead-modal";

import {
  Sidebar,
} from "@/components/sidebar";

import {
  Topbar,
} from "@/components/topbar";

import {
  getLeads,
} from "@/lib/leads";

import type {
  Lead,
  LeadStatus,
} from "@/types/lead";


const filters: Array<
  "all" | LeadStatus
> = [
  "all",
  "new",
  "contacted",
  "qualified",
  "nurture",
  "unqualified",
  "converted",
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


export default function LeadsPage() {
  const [leads, setLeads] =
    useState<Lead[]>([]);

  const [loading, setLoading] =
    useState(true);

  const [error, setError] =
    useState<string | null>(null);

  const [query, setQuery] =
    useState("");

  const [filter, setFilter] =
    useState<
      "all" | LeadStatus
    >("all");

  const [
    createOpen,
    setCreateOpen,
  ] = useState(false);


  useEffect(() => {
    async function load() {
      try {
        setLoading(true);
        setError(null);

        const data =
          await getLeads();

        setLeads(data);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Unable to load leads."
        );
      } finally {
        setLoading(false);
      }
    }

    load();
  }, []);


  const filteredLeads =
    useMemo(() => {
      const normalized =
        query.trim().toLowerCase();

      return leads.filter(
        (lead) => {
          const matchesFilter =
            filter === "all" ||
            lead.status === filter;

          const searchable = [
            lead.first_name,
            lead.last_name,
            lead.email,
            lead.company_name,
            lead.job_title,
          ]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();

          return (
            matchesFilter &&
            (
              !normalized ||
              searchable.includes(
                normalized
              )
            )
          );
        }
      );
    }, [
      leads,
      filter,
      query,
    ]);


  function addCreatedLead(
    lead: Lead
  ) {
    setLeads(
      (current) => [
        lead,
        ...current,
      ]
    );
  }


  return (
    <div className="appShell">
      <Sidebar active="Leads" />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <div className="pageHeading accountsHeading">
            <div>
              <p className="eyebrow">
                Sales
              </p>

              <h1>
                Leads
              </h1>

              <p>
                Capture, qualify and
                convert potential customers.
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
              New Lead
            </button>
          </div>

          <div className="accountSummary">
            <div>
              <Target size={18} />

              <span>
                Total Leads
              </span>

              <strong>
                {leads.length}
              </strong>
            </div>

            <div>
              <Users size={18} />

              <span>
                New
              </span>

              <strong>
                {
                  leads.filter(
                    (lead) =>
                      lead.status
                      === "new"
                  ).length
                }
              </strong>
            </div>

            <div>
              <UserCheck size={18} />

              <span>
                Qualified
              </span>

              <strong>
                {
                  leads.filter(
                    (lead) =>
                      lead.status
                      === "qualified"
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
                  placeholder="Search leads..."
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
                Loading leads...
              </div>
            )}

            {error && !loading && (
              <div className="tableState errorState">
                <strong>
                  Leads unavailable
                </strong>

                <span>
                  {error}
                </span>
              </div>
            )}

            {!loading &&
              !error &&
              filteredLeads.length
                === 0 && (
                <div className="emptyState">
                  <div className="emptyStateIcon">
                    <Target size={23} />
                  </div>

                  <h3>
                    No leads found
                  </h3>

                  <p>
                    Capture your first
                    potential customer
                    in AIVA CRM.
                  </p>

                  <button
                    type="button"
                    className="createButton"
                    onClick={() =>
                      setCreateOpen(true)
                    }
                  >
                    <Plus size={15} />
                    New Lead
                  </button>
                </div>
              )}

            {!loading &&
              !error &&
              filteredLeads.length
                > 0 && (
                <div className="accountTableWrapper">
                  <table className="accountTable">
                    <thead>
                      <tr>
                        <th>
                          Lead
                        </th>

                        <th>
                          Company
                        </th>

                        <th>
                          Status
                        </th>

                        <th>
                          Score
                        </th>

                        <th>
                          Source
                        </th>

                        <th>
                          Email
                        </th>
                      </tr>
                    </thead>

                    <tbody>
                      {filteredLeads.map(
                        (lead) => (
                          <tr
                            key={lead.id}
                          >
                            <td>
                              <Link
                                href={
                                  `/leads/${lead.id}`
                                }
                                className="accountIdentity"
                              >
                                <div className="accountLogo">
                                  {
                                    lead
                                      .first_name[0]
                                      .toUpperCase()
                                  }
                                </div>

                                <div>
                                  <strong>
                                    {
                                      lead.first_name
                                    }{" "}
                                    {
                                      lead.last_name
                                    }
                                  </strong>

                                  <span>
                                    {
                                      lead.job_title
                                      || "Lead"
                                    }
                                  </span>
                                </div>
                              </Link>
                            </td>

                            <td>
                              {
                                lead.company_name
                                || "—"
                              }
                            </td>

                            <td>
                              <span
                                className={
                                  `stageBadge stage-${lead.status}`
                                }
                              >
                                {
                                  displayLabel(
                                    lead.status
                                  )
                                }
                              </span>
                            </td>

                            <td>
                              <strong>
                                {lead.score}
                              </strong>
                              /100
                            </td>

                            <td>
                              {
                                displayLabel(
                                  lead.source
                                )
                              }
                            </td>

                            <td>
                              {
                                lead.email
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

      <CreateLeadModal
        open={createOpen}
        onClose={() =>
          setCreateOpen(false)
        }
        onCreated={
          addCreatedLead
        }
      />
    </div>
  );
}
