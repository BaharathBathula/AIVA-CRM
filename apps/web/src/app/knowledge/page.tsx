"use client";

import Link from "next/link";
import { BookOpen, Eye, Plus, Search, ThumbsUp } from "lucide-react";
import { useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Alert, Badge, EmptyState, LoadingRows, PageHeader, Tabs } from "@/components/ui";
import { number, relativeTime } from "@/lib/format";
import type { ArticleSummary } from "@/lib/platform-types";
import { query, useApi, useDebounced } from "@/lib/use-api";

type KnowledgeList = {
  items: ArticleSummary[];
  categories: { name: string; count: number }[];
  can_write: boolean;
  can_publish: boolean;
};

const STATUS_TONE = { draft: "warning", published: "success", archived: "neutral" } as const;

export default function KnowledgePage() {
  return (
    <AppShell>
      <Knowledge />
    </AppShell>
  );
}

function Knowledge() {
  const [status, setStatus] = useState<"" | "published" | "draft" | "archived">("");
  const [category, setCategory] = useState("");
  const [search, setSearch] = useState("");
  const term = useDebounced(search.trim(), 250);
  const list = useApi<KnowledgeList>(`/knowledge${query({ status, category, search: term })}`);
  const data = list.data;

  return (
    <>
      <PageHeader
        title="Knowledge base"
        description="Answers your team writes once and reuses on every case. Agents see suggestions right on the case."
        actions={
          data?.can_write ? (
            <Link href="/knowledge/new" className="ui-btn ui-btn-primary">
              <Plus size={16} /> New article
            </Link>
          ) : null
        }
      />

      <div className="ui-toolbar">
        {data?.can_write ? (
          <Tabs
            label="Article status"
            value={status}
            onChange={setStatus}
            items={[
              { value: "", label: "All" },
              { value: "published", label: "Published" },
              { value: "draft", label: "Drafts" },
              { value: "archived", label: "Archived" },
            ]}
          />
        ) : null}
        <span className="ui-spacer" />
        <div className="ui-search" style={{ flexBasis: 360 }}>
          <Search size={15} />
          <input className="ui-input sm" placeholder="Search articles" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
      </div>

      {list.error ? <Alert>{list.error}</Alert> : null}

      <div className="ui-master-detail">
        <nav className="ui-card" style={{ padding: 8 }} aria-label="Categories">
          <button
            type="button"
            className={`ui-menu-item${!category ? " selected" : ""}`}
            onClick={() => setCategory("")}
          >
            <BookOpen size={15} /> All categories
          </button>
          {(data?.categories ?? []).map((item) => (
            <button
              key={item.name}
              type="button"
              className={`ui-menu-item${category === item.name ? " selected" : ""}`}
              onClick={() => setCategory(item.name)}
            >
              <span style={{ flex: 1 }}>{item.name}</span>
              <span className="ui-muted ui-small">{item.count}</span>
            </button>
          ))}
        </nav>

        <div className="ui-card">
          {list.loading && !data ? (
            <LoadingRows />
          ) : data?.items.length ? (
            <div className="ui-list">
              {data.items.map((article) => (
                <Link key={article.id} href={`/knowledge/${article.id}`} className="ui-list-item" style={{ alignItems: "flex-start", padding: "14px 18px" }}>
                  <span className="ui-avatar" style={{ borderRadius: 9, width: 34, height: 34, marginTop: 2 }}>
                    <BookOpen size={16} />
                  </span>
                  <div className="main">
                    <div className="title" style={{ fontSize: 14.5 }}>
                      {article.title}
                    </div>
                    {article.summary ? (
                      <div className="sub" style={{ fontSize: 13, color: "var(--text-2)", marginTop: 3 }}>
                        {article.summary}
                      </div>
                    ) : null}
                    <div className="sub" style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 6 }}>
                      <span>{article.category ?? "Uncategorized"}</span>
                      <span>
                        <Eye size={12} style={{ verticalAlign: -1 }} /> {number(article.view_count)}
                      </span>
                      <span>
                        <ThumbsUp size={12} style={{ verticalAlign: -1 }} /> {number(article.helpful_count)}
                      </span>
                      <span>Updated {relativeTime(article.updated_at)}</span>
                    </div>
                  </div>
                  {article.status !== "published" ? <Badge tone={STATUS_TONE[article.status]}>{article.status}</Badge> : null}
                </Link>
              ))}
            </div>
          ) : (
            <EmptyState
              icon={<BookOpen size={20} />}
              title={term || category || status ? "No articles match" : "No articles yet"}
              message={term ? "Try different words, or write the article you were looking for." : "Write the first article for a common question."}
            />
          )}
        </div>
      </div>
    </>
  );
}
