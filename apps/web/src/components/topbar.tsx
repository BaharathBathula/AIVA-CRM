"use client";

import { PlanBanner } from "@/components/plan";
import { TopBar } from "@/components/shell";

/** Top bar used by every page (older pages import it by this name), plus the plan banner. */
export function Topbar() {
  return (
    <>
      <TopBar />
      <PlanBanner />
    </>
  );
}
