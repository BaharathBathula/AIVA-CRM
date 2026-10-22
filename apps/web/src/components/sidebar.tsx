"use client";

import { Rail } from "@/components/shell";

type SidebarProps = {
  /** Kept for older pages; the active section is derived from the URL. */
  active?: string;
};

/** Left navigation rail used by every page (older pages import it by this name). */
export function Sidebar(_props: SidebarProps) {
  return <Rail />;
}
