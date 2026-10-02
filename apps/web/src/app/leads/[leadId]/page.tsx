"use client";

import {
  useEffect,
  useState,
} from "react";

import Link from "next/link";

import {
  ArrowLeft,
  ArrowRight,
  Building2,
  CheckCircle2,
  Mail,
  Phone,
  Target,
  UserCheck,
} from "lucide-react";

import {
  useParams,
} from "next/navigation";

import {
  ConvertLeadModal,
} from "@/components/leads/convert-lead-modal";

import {
  Sidebar,
} from "@/components/sidebar";

import {
  Topbar,
} from "@/components/topbar";

import {
  getLead,
  updateLead,
} from "@/lib/leads";

import type {
  Lead,
  LeadConvertResult,
} from "@/types/lead";


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
    new Date(value)
  );
}


export default function LeadDetailPage() {
  const params = useParams<{
    leadId: string;
  }>();

  const leadId =
    params.leadId;

  const [lead, setLead] =
    useState<Lead | null>(null);

  const [loading, setLoading] =
    useState(true);

  const [updating, setUpdating] =
    useState(false);

  const [error, setError] =
    useState<string | null>(null);

  const [
    convertOpen,
    setConvertOpen,
  ] = useState(false);

  const [
    conversion,
    setConversion,
  ] = useState<
    LeadConvertResult | null
  >(null);


  useEffect(() => {
    if (!leadId) {
      return;
    }

    async function load() {
      try {
        setLoading(true);
        setError(null);

        const result =
          await getLead(
            leadId
          );

        setLead(result);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Unable to load lead."
        );
      } finally {
        setLoading(false);
      }
    }

    load();
  }, [leadId]);


  async function qualifyLead() {
    if (!lead) {
      return;
    }

    try {
      setUpdating(true);
      setError(null);

      const result =
        await updateLead(
          lead.id,
          {
            status:
              "qualified",
          }
        );

      setLead(result);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to qualify lead."
      );
    } finally {
      setUpdating(false);
    }
  }


  async function handleConverted(
    result: LeadConvertResult
  ) {
    setConversion(result);

    try {
      const refreshed =
        await getLead(
          result.lead_id
        );

      setLead(refreshed);
    } catch {
      if (lead) {
        setLead({
          ...lead,
          status: "converted",
          converted_account_id:
            result.account_id,
          converted_contact_id:
            result.contact_id,
          converted_opportunity_id:
            result.opportunity_id,
          converted_at:
            new Date().toISOString(),
        });
      }
    }
  }


  if (loading) {
    return (
      <div className="appShell">
        <Sidebar active="Leads" />

        <main className="mainArea">
          <Topbar />

          <div className="pageContent">
            <div className="detailLoading">
              Loading lead...
            </div>
          </div>
        </main>
      </div>
    );
  }


  if (
    error &&
    !lead
  ) {
    return (
      <div className="appShell">
        <Sidebar active="Leads" />

        <main className="mainArea">
          <Topbar />

          <div className="pageContent">
            <Link
              href="/leads"
              className="backLink"
            >
              <ArrowLeft size={15} />
              Leads
            </Link>

            <div className="detailError">
              <strong>
                Lead unavailable
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


  if (!lead) {
    return null;
  }


  return (
    <div className="appShell">
      <Sidebar active="Leads" />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <Link
            href="/leads"
            className="backLink"
          >
            <ArrowLeft size={15} />
            Back to Leads
          </Link>

          <section className="accountHero">
            <div className="accountHeroIdentity">
              <div className="accountHeroLogo">
                {lead.first_name[0]
                  .toUpperCase()}
              </div>

              <div>
                <div className="accountHeroTitle">
                  <h1>
                    {lead.first_name}{" "}
                    {lead.last_name}
                  </h1>

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
                </div>

                <div className="accountHeroMeta">
                  {lead.company_name && (
                    <span>
                      <Building2
                        size={14}
                      />

                      {
                        lead.company_name
                      }
                    </span>
                  )}

                  {lead.email && (
                    <span>
                      <Mail size={14} />

                      {lead.email}
                    </span>
                  )}
                </div>
              </div>
            </div>

            <div className="detailActions">
              {lead.status !==
                "converted" &&
                lead.status !==
                  "qualified" && (
                  <button
                    type="button"
                    className="secondaryButton"
                    disabled={updating}
                    onClick={
                      qualifyLead
                    }
                  >
                    <UserCheck
                      size={15}
                    />

                    {updating
                      ? "Qualifying..."
                      : "Qualify Lead"}
                  </button>
                )}

              {lead.status ===
                "qualified" && (
                <button
                  type="button"
                  className="createButton"
                  onClick={() =>
                    setConvertOpen(
                      true
                    )
                  }
                >
                  <ArrowRight
                    size={15}
                  />
                  Convert Lead
                </button>
              )}

              {lead.status ===
                "converted" && (
                <span className="stageBadge stage-customer">
                  <CheckCircle2
                    size={14}
                  />
                  Conversion Complete
                </span>
              )}
            </div>
          </section>

          {error && (
            <div className="formError">
              {error}
            </div>
          )}

          {conversion && (
            <section className="detailPanel">
              <div className="detailPanelHeader">
                <div>
                  <h2>
                    Conversion Successful
                  </h2>

                  <p>
                    AIVA created the CRM
                    records atomically.
                  </p>
                </div>
              </div>

              <div className="detailFields">
                <div>
                  <span>
                    Account
                  </span>

                  <Link
                    href={
                      `/accounts/${conversion.account_id}`
                    }
                  >
                    Open Account
                  </Link>
                </div>

                <div>
                  <span>
                    Contact ID
                  </span>

                  <strong>
                    {
                      conversion.contact_id
                    }
                  </strong>
                </div>

                <div>
                  <span>
                    Opportunity ID
                  </span>

                  <strong>
                    {
                      conversion.opportunity_id
                    }
                  </strong>
                </div>

                <div>
                  <span>
                    Pipeline Stage
                  </span>

                  <strong>
                    Qualification
                  </strong>
                </div>
              </div>
            </section>
          )}

          <div className="accountDetailGrid">
            <div className="accountMainColumn">
              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Qualification
                    </h2>

                    <p>
                      Lead profile and
                      qualification context.
                    </p>
                  </div>
                </div>

                <div className="detailFields">
                  <div>
                    <span>
                      Lead Score
                    </span>

                    <strong>
                      {lead.score}/100
                    </strong>
                  </div>

                  <div>
                    <span>
                      Status
                    </span>

                    <strong>
                      {
                        displayLabel(
                          lead.status
                        )
                      }
                    </strong>
                  </div>

                  <div>
                    <span>
                      Source
                    </span>

                    <strong>
                      {
                        displayLabel(
                          lead.source
                        )
                      }
                    </strong>
                  </div>

                  <div>
                    <span>
                      Created
                    </span>

                    <strong>
                      {formatDate(
                        lead.created_at
                      )}
                    </strong>
                  </div>
                </div>

                <div className="aivaPlaceholder">
                  <span>
                    QUALIFICATION NOTES
                  </span>

                  <strong>
                    {lead.notes
                      || "No qualification notes yet."}
                  </strong>
                </div>
              </section>
            </div>

            <aside className="accountSideColumn">
              <section className="detailPanel">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      Contact Details
                    </h2>

                    <p>
                      Lead contact information.
                    </p>
                  </div>
                </div>

                <div className="detailFields">
                  <div>
                    <span>
                      Company
                    </span>

                    <strong>
                      {
                        lead.company_name
                        || "—"
                      }
                    </strong>
                  </div>

                  <div>
                    <span>
                      Job Title
                    </span>

                    <strong>
                      {
                        lead.job_title
                        || "—"
                      }
                    </strong>
                  </div>

                  <div>
                    <span>
                      Email
                    </span>

                    {lead.email ? (
                      <a
                        href={
                          `mailto:${lead.email}`
                        }
                      >
                        <Mail size={13} />
                        {lead.email}
                      </a>
                    ) : (
                      <strong>
                        —
                      </strong>
                    )}
                  </div>

                  <div>
                    <span>
                      Phone
                    </span>

                    {lead.phone ? (
                      <a
                        href={
                          `tel:${lead.phone}`
                        }
                      >
                        <Phone size={13} />
                        {lead.phone}
                      </a>
                    ) : (
                      <strong>
                        —
                      </strong>
                    )}
                  </div>
                </div>
              </section>

              {lead.status ===
                "converted" && (
                <section className="detailPanel">
                  <div className="detailPanelHeader">
                    <div>
                      <h2>
                        Converted Records
                      </h2>

                      <p>
                        CRM records created
                        from this lead.
                      </p>
                    </div>
                  </div>

                  <div className="detailFields">
                    <div>
                      <span>
                        Converted At
                      </span>

                      <strong>
                        {formatDate(
                          lead.converted_at
                        )}
                      </strong>
                    </div>

                    <div>
                      <span>
                        Account
                      </span>

                      {lead.converted_account_id ? (
                        <Link
                          href={
                            `/accounts/${lead.converted_account_id}`
                          }
                        >
                          Open Account
                        </Link>
                      ) : (
                        <strong>
                          —
                        </strong>
                      )}
                    </div>

                    <div>
                      <span>
                        Contact ID
                      </span>

                      <strong>
                        {
                          lead.converted_contact_id
                          || "—"
                        }
                      </strong>
                    </div>

                    <div>
                      <span>
                        Opportunity ID
                      </span>

                      <strong>
                        {
                          lead.converted_opportunity_id
                          || "—"
                        }
                      </strong>
                    </div>
                  </div>
                </section>
              )}

              <section className="detailPanel aiAccountCard">
                <div className="detailPanelHeader">
                  <div>
                    <h2>
                      AIVA Lead Intelligence
                    </h2>

                    <p>
                      AI qualification support.
                    </p>
                  </div>
                </div>

                <div className="aivaPlaceholder">
                  <Target size={20} />

                  <span>
                    AI COMING NEXT
                  </span>

                  <strong>
                    Autonomous lead scoring
                    and qualification.
                  </strong>

                  <p>
                    AIVA will analyze engagement,
                    intent and fit before
                    recommending conversion.
                  </p>
                </div>
              </section>
            </aside>
          </div>
        </div>
      </main>

      <ConvertLeadModal
        lead={lead}
        open={convertOpen}
        onClose={() =>
          setConvertOpen(false)
        }
        onConverted={
          handleConverted
        }
      />
    </div>
  );
}
