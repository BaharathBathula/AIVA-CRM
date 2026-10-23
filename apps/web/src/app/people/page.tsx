"use client";

import Link from "next/link";
import { Mail, MessageSquare, Phone, Search, Users } from "lucide-react";
import { useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useSession } from "@/components/providers";
import { Alert, Avatar, EmptyState, LoadingRows, PageHeader } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import { useApi, useDebounced } from "@/lib/use-api";
import type { Person } from "@/lib/workspace";

export default function PeoplePage() {
  return (
    <AppShell>
      <People />
    </AppShell>
  );
}

function People() {
  const { has } = useSession();
  const [search, setSearch] = useState("");
  const debounced = useDebounced(search);
  const people = useApi<{ items: Person[] }>(`/people${debounced.trim() ? `?search=${encodeURIComponent(debounced.trim())}` : ""}`);
  const chat = has("team_chat");

  return (
    <>
      <PageHeader
        title="People"
        description="Everyone in your workspace: who they are, how to reach them and who they report to."
      />
      <div className="ui-toolbar">
        <div className="ui-search">
          <Search size={15} />
          <input className="ui-input sm" placeholder="Search name, email or title" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search people" />
        </div>
        {chat ? (
          <Link className="ui-btn" href="/chat">
            <MessageSquare size={15} /> Open chat
          </Link>
        ) : null}
      </div>
      {people.error ? <Alert>{people.error}</Alert> : null}
      {people.loading && !people.data ? (
        <div className="ui-card">
          <LoadingRows />
        </div>
      ) : people.data?.items.length ? (
        <div className="people-grid">
          {people.data.items.map((p) => (
            <article key={p.id} className="person-card">
              <div className="person-head">
                <Avatar name={p.full_name} size={44} />
                <div className="who">
                  <strong>
                    {p.full_name}
                    {p.is_me ? " (you)" : ""}
                  </strong>
                  <span>{p.job_title ?? p.role_name}</span>
                </div>
              </div>
              <dl className="person-facts">
                <div>
                  {p.role_name}
                  {p.team_name ? ` · ${p.team_name}` : ""}
                </div>
                {p.manager_name ? <div>Reports to {p.manager_name}</div> : null}
                {p.timezone ? <div>{p.timezone}</div> : null}
                <div className="ui-muted">{p.last_login_at ? `Last signed in ${relativeTime(p.last_login_at)}` : "Hasn't signed in yet"}</div>
              </dl>
              <div className="ui-row" style={{ gap: 6, flexWrap: "wrap" }}>
                {chat && !p.is_me ? (
                  <Link className="ui-btn ui-btn-sm ui-btn-primary" href={`/chat?dm=${p.id}`}>
                    <MessageSquare size={14} /> Message
                  </Link>
                ) : null}
                <a className="ui-btn ui-btn-sm" href={`mailto:${p.email}`}>
                  <Mail size={14} /> Email
                </a>
                {p.phone ? (
                  <a className="ui-btn ui-btn-sm" href={`tel:${p.phone}`}>
                    <Phone size={14} /> Call
                  </a>
                ) : null}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="ui-card">
          <EmptyState icon={<Users size={20} />} title="No one matches" />
        </div>
      )}
    </>
  );
}
