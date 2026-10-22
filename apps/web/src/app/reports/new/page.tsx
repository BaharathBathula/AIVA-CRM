"use client";

import { AppShell } from "@/components/app-shell";
import { ReportBuilder } from "@/components/report-builder";

export default function NewReportPage() {
  return (
    <AppShell permission="analytics.reports" what="the report builder">
      <ReportBuilder />
    </AppShell>
  );
}
