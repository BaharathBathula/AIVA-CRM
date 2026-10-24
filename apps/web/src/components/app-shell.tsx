"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ShieldAlert } from "lucide-react";
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
  const pathname = usePathname();
  const required = Array.isArray(permission) ? permission : permission ? [permission] : [];
  const allowed = required.every((key) => can(key));
  // The workspace requires two-step verification: until it's on, only the pages to set it up (and billing) work.
  const mfaBlocked =
    Boolean(session?.mfa_enforced && !session.user.mfa_enabled && !session.support_access) &&
    !["/settings/security", "/settings/profile", "/settings/billing"].some((path) => pathname === path || pathname.startsWith(`${path}/`));

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
          ) : mfaBlocked ? (
            <MfaRequired />
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

function MfaRequired() {
  return (
    <div className="ui-card">
      <div className="ui-empty">
        <div className="icon">
          <ShieldAlert size={20} />
        </div>
        <h3>Turn on two-step verification</h3>
        <p>Your workspace requires two-step verification for everyone. It takes a minute with an authenticator app, and then you can carry on.</p>
        <div style={{ marginTop: 6 }}>
          <Link href="/settings/security" className="ui-btn ui-btn-primary">
            Set it up now
          </Link>
        </div>
      </div>
    </div>
  );
}
