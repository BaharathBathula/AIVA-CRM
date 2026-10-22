"use client";

import { useParams } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ReportBuilder } from "@/components/report-builder";

export default function ReportPage() {
  const { reportId } = useParams<{ reportId: string }>();
  return (
    <AppShell>
      <ReportBuilder key={reportId} reportId={reportId} />
    </AppShell>
  );
}
