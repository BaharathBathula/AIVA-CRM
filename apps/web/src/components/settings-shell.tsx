"use client";

import { ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { UpgradeGate } from "@/components/plan";
import { useSession } from "@/components/providers";

/** Frame for settings pages. Their menu lives in the top bar (Settings section). */
export function SettingsShell({
  children,
  permission,
  what,
  feature,
}: {
  children: ReactNode;
  permission?: string | string[];
  what?: string;
  /** Plan feature the page belongs to; shows the upgrade card when missing. */
  feature?: string;
}) {
  return (
    <AppShell>
      <div className="settings-page">
        <PermissionGate permission={permission} what={what}>
          {feature ? <UpgradeGate feature={feature}>{children}</UpgradeGate> : children}
        </PermissionGate>
      </div>
    </AppShell>
  );
}

function PermissionGate({ permission, what, children }: { permission?: string | string[]; what?: string; children: ReactNode }) {
  const { can } = useSession();
  const required = Array.isArray(permission) ? permission : permission ? [permission] : [];
  if (required.every((key) => can(key))) return <>{children}</>;
  return (
    <div className="ui-card">
      <div className="ui-empty">
        <div className="icon">
          <ShieldCheck size={20} />
        </div>
        <h3>You don&apos;t have access</h3>
        <p>Your role doesn&apos;t include permission to manage {what ?? "this setting"}. Ask a workspace admin if you need it.</p>
      </div>
    </div>
  );
}
