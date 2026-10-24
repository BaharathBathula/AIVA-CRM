"use client";

import Link from "next/link";
import { AlertTriangle, ArrowRight, Check, RefreshCw, X } from "lucide-react";
import { useMemo, useState } from "react";

import { SettingsShell } from "@/components/settings-shell";
import { Alert, Card, LoadingRows, PageHeader, Tabs } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { useApi } from "@/lib/use-api";

type DiagnoseCheck = {
  area: string;
  label: string;
  status: "pass" | "warn" | "fail";
  detail: string;
  fix: string | null;
  href: string | null;
};

type Diagnosis = {
  checks: DiagnoseCheck[];
  summary: { pass: number; warn: number; fail: number };
  score: number;
  checked_at: string;
};

type View = "attention" | "all" | "pass";

const ORDER = { fail: 0, warn: 1, pass: 2 } as const;

/** Older links point at a settings path that has since moved. */
function fixHref(href: string | null) {
  if (!href) return null;
  if (href === "/settings/icp/personas") return "/settings/personas";
  return href;
}

export default function DiagnosePage() {
  return (
    <SettingsShell>
      <Diagnose />
    </SettingsShell>
  );
}

function Diagnose() {
  const diagnosis = useApi<Diagnosis>("/outbound/diagnose");
  const [view, setView] = useState<View>("attention");
  const data = diagnosis.data;

  const groups = useMemo(() => {
    const shown = (data?.checks ?? []).filter((check) =>
      view === "all" ? true : view === "pass" ? check.status === "pass" : check.status !== "pass",
    );
    const byArea = new Map<string, DiagnoseCheck[]>();
    for (const check of shown) byArea.set(check.area, [...(byArea.get(check.area) ?? []), check]);
    return Array.from(byArea.entries())
      .map(([area, checks]) => ({ area, checks: checks.sort((a, b) => ORDER[a.status] - ORDER[b.status]) }))
      .sort((a, b) => ORDER[a.checks[0].status] - ORDER[b.checks[0].status] || a.area.localeCompare(b.area));
  }, [data, view]);

  const scoreTone = !data ? "" : data.score >= 85 ? "good" : data.score >= 60 ? "ok" : "bad";
  const attention = data ? data.summary.fail + data.summary.warn : 0;

  return (
    <>
      <PageHeader
        title="Diagnose"
        description="AIVA checks your domains, mailboxes, sending limits, tracking and sequence content for anything that hurts deliverability, and tells you how to fix it."
        actions={
          <button type="button" className="ui-btn" disabled={diagnosis.loading} onClick={() => void diagnosis.reload()}>
            {diagnosis.loading ? <span className="ui-spinner" /> : <RefreshCw size={15} />} Run again
          </button>
        }
      />
      {diagnosis.error ? <Alert>{diagnosis.error}</Alert> : null}
      {!data ? (
        <div className="ui-card">
          <LoadingRows rows={8} />
        </div>
      ) : (
        <div className="ui-stack">
          <Card>
            <div className="outbound-score">
              <div className={`outbound-score-ring ${scoreTone}`} role="img" aria-label={`Deliverability score ${data.score} out of 100`}>
                <strong>{data.score}</strong>
                <span>of 100</span>
              </div>
              <div className="outbound-score-text">
                <h2>
                  {data.summary.fail
                    ? `${data.summary.fail} problem${data.summary.fail === 1 ? "" : "s"} to fix`
                    : data.summary.warn
                      ? `${data.summary.warn} thing${data.summary.warn === 1 ? "" : "s"} worth improving`
                      : "Everything looks good"}
                </h2>
                <div className="outbound-score-counts">
                  <span>
                    <span className="settings-check-icon fail" aria-hidden>
                      <X size={12} />
                    </span>
                    {data.summary.fail} failing
                  </span>
                  <span>
                    <span className="settings-check-icon warn" aria-hidden>
                      <AlertTriangle size={12} />
                    </span>
                    {data.summary.warn} warnings
                  </span>
                  <span>
                    <span className="settings-check-icon pass" aria-hidden>
                      <Check size={12} />
                    </span>
                    {data.summary.pass} passing
                  </span>
                </div>
                <p className="ui-muted ui-small" style={{ margin: 0 }}>
                  Checked {relativeTime(data.checked_at)}. Domain records are looked up again if they&apos;re more than a day old.
                </p>
              </div>
            </div>
          </Card>

          <Tabs<View>
            label="Show"
            value={view}
            onChange={setView}
            items={[
              { value: "attention", label: "Needs attention", count: attention, hot: true },
              { value: "all", label: "All checks", count: data.checks.length },
              { value: "pass", label: "Passing", count: data.summary.pass },
            ]}
          />

          {groups.length ? (
            groups.map((group) => (
              <Card key={group.area} title={group.area}>
                <div className="settings-checks">
                  {group.checks.map((check, index) => {
                    const Icon = check.status === "pass" ? Check : check.status === "warn" ? AlertTriangle : X;
                    const href = fixHref(check.href);
                    return (
                      <div key={`${check.label}-${index}`} className="settings-check">
                        <span
                          className={`settings-check-icon ${check.status}`}
                          aria-label={check.status === "pass" ? "Passing" : check.status === "warn" ? "Warning" : "Failing"}
                        >
                          <Icon size={13} />
                        </span>
                        <div style={{ minWidth: 0 }}>
                          <strong>{check.label}</strong>
                          <p>{check.detail}</p>
                          {check.status !== "pass" && (check.fix || href) ? (
                            <p className="outbound-fix">
                              {check.fix ? (
                                <span>
                                  <strong>Fix:</strong> {check.fix}
                                </span>
                              ) : null}
                              {href ? (
                                <Link href={href} className="ui-btn ui-btn-sm">
                                  Go there <ArrowRight size={13} />
                                </Link>
                              ) : null}
                            </p>
                          ) : null}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </Card>
            ))
          ) : (
            <div className="ui-card">
              <div className="ui-empty">
                <div className="icon">
                  <Check size={20} />
                </div>
                <h3>{view === "attention" ? "Nothing needs attention" : "No checks to show"}</h3>
                <p>{view === "attention" ? "Every check passed. Run it again after you change DNS records or connect new mailboxes." : "Try another filter."}</p>
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );
}
