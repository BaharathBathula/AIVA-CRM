"use client";

import Link from "next/link";
import { Search } from "lucide-react";
import { useState } from "react";

import { PlanChip } from "@/components/plan";
import { useSession } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { PageHeader } from "@/components/ui";
import { SETTINGS_MENU, allowed } from "@/lib/settings-menu";

export default function SettingsHub() {
  const { can } = useSession();
  const [filter, setFilter] = useState("");
  const term = filter.trim().toLowerCase();
  return (
    <SettingsShell>
      <PageHeader
        title="Settings"
        description="Everything you can set up in AIVA, grouped the same way as the menu on the left."
        actions={
          <label className="settings-nav-search" style={{ width: 280, background: "var(--surface)" }}>
            <Search size={15} aria-hidden />
            <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Find a setting" aria-label="Find a setting" />
          </label>
        }
      />
      <div className="settings-hub">
        {SETTINGS_MENU.map((group) => {
          const tiles = group.items.filter(
            (item) =>
              allowed(item, can) &&
              (!term || `${item.label} ${item.description} ${group.label}`.toLowerCase().includes(term)),
          );
          if (!tiles.length) return null;
          return (
            <section key={group.key} className="settings-group">
              <h2>{group.label}</h2>
              <div className="settings-tiles">
                {tiles.map((tile) => {
                  const Icon = tile.icon;
                  return (
                    <Link key={tile.href} href={tile.href} className="settings-tile">
                      <span className="tile-icon">
                        <Icon size={19} />
                      </span>
                      <span>
                        <strong>
                          {tile.label} {tile.feature ? <PlanChip feature={tile.feature} /> : null}
                        </strong>
                        <span className="desc">{tile.description}</span>
                      </span>
                    </Link>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>
    </SettingsShell>
  );
}
