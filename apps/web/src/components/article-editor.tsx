"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { useSession, useToast } from "@/components/providers";
import { Alert, Card, Field, PageHeader, Tabs } from "@/components/ui";
import { renderMarkdown } from "@/lib/markdown";
import type { Article } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

const TEMPLATE = `## Overview
What this article helps with and who it's for.

## Steps
1. First step
2. Second step

## If that doesn't work
- Check …
- Contact **support** with …
`;

export function ArticleEditor({ articleId }: { articleId?: string }) {
  const router = useRouter();
  const toast = useToast();
  const { can } = useSession();
  const existing = useApi<Article>(articleId ? `/knowledge/${articleId}` : null);
  const categories = useApi<{ categories: { name: string }[] }>("/knowledge");
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [category, setCategory] = useState("");
  const [tags, setTags] = useState("");
  const [body, setBody] = useState(articleId ? "" : TEMPLATE);
  const [mode, setMode] = useState<"write" | "preview">("write");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const a = existing.data;
    if (!a) return;
    setTitle(a.title);
    setSummary(a.summary ?? "");
    setCategory(a.category ?? "");
    setTags(a.tags.join(", "));
    setBody(a.body);
  }, [existing.data]);

  async function save(event: FormEvent<HTMLFormElement>, publish = false) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const payload = {
      title: title.trim(),
      summary: summary.trim() || null,
      category: category.trim() || null,
      tags: tags
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean),
      body,
    };
    try {
      const saved = articleId ? await api.patch<Article>(`/knowledge/${articleId}`, payload) : await api.post<Article>("/knowledge", payload);
      if (publish) await api.post(`/knowledge/${saved.id}/publish`);
      toast(publish ? "Article published." : "Draft saved.");
      router.push(`/knowledge/${saved.id}`);
    } catch (caught) {
      setError(errorMessage(caught));
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader eyebrow="Knowledge base" title={articleId ? "Edit article" : "New article"} description="Write in Markdown: ## headings, - lists, **bold**, `code` and [links](https://…)." />
      <form onSubmit={(event) => void save(event)} className="ui-split">
        <Card
          title={
            <Tabs
              label="Editor mode"
              value={mode}
              onChange={setMode}
              items={[
                { value: "write", label: "Write" },
                { value: "preview", label: "Preview" },
              ]}
            />
          }
        >
          {error ? (
            <div style={{ marginBottom: 12 }}>
              <Alert>{error}</Alert>
            </div>
          ) : null}
          <div className="ui-stack">
            <input
              className="ui-input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Article title"
              required
              aria-label="Title"
              style={{ height: 46, fontSize: 18, fontWeight: 700 }}
            />
            {mode === "write" ? (
              <textarea
                className="ui-textarea ui-mono"
                value={body}
                onChange={(e) => setBody(e.target.value)}
                rows={22}
                aria-label="Article body"
                style={{ fontSize: 13, lineHeight: 1.6 }}
              />
            ) : (
              <div className="ui-prose" style={{ minHeight: 300 }} dangerouslySetInnerHTML={{ __html: renderMarkdown(body) }} />
            )}
          </div>
        </Card>
        <div className="ui-stack">
          <Card title="Details">
            <div className="ui-stack">
              <Field label="Summary" hint="Shown in search results and case suggestions.">
                <textarea className="ui-textarea" rows={3} value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={500} />
              </Field>
              <Field label="Category">
                <input className="ui-input" list="kb-categories" value={category} onChange={(e) => setCategory(e.target.value)} />
                <datalist id="kb-categories">
                  {(categories.data?.categories ?? []).map((item) => (
                    <option key={item.name} value={item.name} />
                  ))}
                </datalist>
              </Field>
              <Field label="Tags" hint="Comma separated.">
                <input className="ui-input" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="billing, sso" />
              </Field>
            </div>
          </Card>
          <div className="ui-stack" style={{ gap: 8 }}>
            {can("service.knowledge_publish") ? (
              <button
                type="button"
                className="ui-btn ui-btn-primary"
                disabled={busy}
                onClick={(event) => void save(event as unknown as FormEvent<HTMLFormElement>, true)}
              >
                Save and publish
              </button>
            ) : null}
            <button type="submit" className="ui-btn" disabled={busy}>
              {articleId ? "Save changes" : "Save draft"}
            </button>
            <Link href={articleId ? `/knowledge/${articleId}` : "/knowledge"} className="ui-btn ui-btn-ghost">
              Cancel
            </Link>
          </div>
        </div>
      </form>
    </>
  );
}
