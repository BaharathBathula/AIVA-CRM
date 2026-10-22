"use client";

import type { ReactNode } from "react";

import { useSession } from "@/components/providers";
import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";
import { LoadingRows, NoAccess } from "@/components/ui";

/**
 * Standard page frame for platform screens: sidebar, top bar and a padded
 * content column. `permission` hides the page body from roles without it.
 */
export function AppShell({
  children,
  permission,
  what,
}: {
  children: ReactNode;
  permission?: string | string[];
  what?: string;
}) {
  const { session, can } = useSession();
  const required = Array.isArray(permission) ? permission : permission ? [permission] : [];
  const allowed = required.every((key) => can(key));

  return (
    <div className="appShell">
      <Sidebar />
      <main className="mainArea">
        <Topbar />
        <div className="ui-page">
          {!session ? (
            <div className="ui-card">
              <LoadingRows rows={8} />
            </div>
          ) : allowed ? (
            children
          ) : (
            <NoAccess what={what} />
          )}
        </div>
      </main>
    </div>
  );
}
