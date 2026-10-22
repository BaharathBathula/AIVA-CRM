"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Archive, ArrowLeft, Pencil, Send, ThumbsDown, ThumbsUp, Trash2 } from "lucide-react";
import { useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, ConfirmDialog, LoadingRows, PageHeader } from "@/components/ui";
import { date, number } from "@/lib/format";
import { renderMarkdown } from "@/lib/markdown";
import type { Article } from "@/lib/platform-types";
import { api, errorMessage, useApi } from "@/lib/use-api";

export default function ArticlePage() {
  return (
    <AppShell>
      <ArticleView />
    </AppShell>
  );
}

function ArticleView() {
  const { articleId } = useParams<{ articleId: string }>();
  const router = useRouter();
  const toast = useToast();
  const { can } = useSession();
  const article = useApi<Article>(`/knowledge/${articleId}?track=true`);
  const [voted, setVoted] = useState<boolean | null>(null);
  const [deleting, setDeleting] = useState(false);
  const a = article.data;
  const canWrite = can("service.knowledge_write") || can("service.knowledge_publish");
  const canPublish = can("service.knowledge_publish");

  async function feedback(helpful: boolean) {
    try {
      await api.post(`/knowledge/${articleId}/feedback`, { helpful });
      setVoted(helpful);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function action(kind: "publish" | "archive") {
    try {
      const result = await api.post<Article>(`/knowledge/${articleId}/${kind}`);
      article.setData({ ...result, body: result.body ?? a?.body ?? "" });
      toast(kind === "publish" ? "Published. Agents can now find it on cases." : "Archived. It no longer appears in search.");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  async function remove() {
    try {
      await api.del(`/knowledge/${articleId}`);
      toast("Article deleted.");
      router.replace("/knowledge");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  if (article.error) return <Alert>{article.error}</Alert>;
  if (!a) return <div className="ui-card"><LoadingRows rows={10} /></div>;

  return (
    <>
      <div style={{ marginBottom: 10 }}>
        <Link href="/knowledge" className="ui-btn ui-btn-ghost ui-btn-sm">
          <ArrowLeft size={14} /> Knowledge base
        </Link>
      </div>
      <PageHeader
        eyebrow={
          <>
            {a.category ?? "Uncategorized"}{" "}
            {a.status !== "published" ? <Badge tone={a.status === "draft" ? "warning" : "neutral"}>{a.status}</Badge> : null}
          </>
        }
        title={a.title}
        description={a.summary ?? undefined}
        actions={
          canWrite ? (
            <>
              {canPublish && a.status !== "published" ? (
                <button type="button" className="ui-btn ui-btn-primary" onClick={() => void action("publish")}>
                  <Send size={15} /> Publish
                </button>
              ) : null}
              {canPublish && a.status === "published" ? (
                <button type="button" className="ui-btn" onClick={() => void action("archive")}>
                  <Archive size={15} /> Archive
                </button>
              ) : null}
              <Link href={`/knowledge/${a.id}/edit`} className="ui-btn">
                <Pencil size={15} /> Edit
              </Link>
              {canPublish ? (
                <button type="button" className="ui-btn ui-btn-danger ui-btn-icon" aria-label="Delete article" onClick={() => setDeleting(true)}>
                  <Trash2 size={15} />
                </button>
              ) : null}
            </>
          ) : null
        }
      />

      <div className="ui-split">
        <Card>
          <article className="ui-prose" dangerouslySetInnerHTML={{ __html: renderMarkdown(a.body) }} />
          <div style={{ marginTop: 26, paddingTop: 16, borderTop: "1px solid var(--border)" }} className="ui-row">
            {voted === null ? (
              <>
                <span className="ui-muted ui-small">Was this article helpful?</span>
                <button type="button" className="ui-btn ui-btn-sm" onClick={() => void feedback(true)}>
                  <ThumbsUp size={14} /> Yes
                </button>
                <button type="button" className="ui-btn ui-btn-sm" onClick={() => void feedback(false)}>
                  <ThumbsDown size={14} /> No
                </button>
              </>
            ) : (
              <span className="ui-muted ui-small">Thanks — your feedback helps keep articles accurate.</span>
            )}
          </div>
        </Card>
        <Card title="About this article">
          <dl className="ui-dl">
            <dt>Author</dt>
            <dd>{a.author_name ?? "—"}</dd>
            <dt>Published</dt>
            <dd>{a.published_at ? date(a.published_at) : "Not yet"}</dd>
            <dt>Updated</dt>
            <dd>{date(a.updated_at)}</dd>
            <dt>Views</dt>
            <dd>{number(a.view_count)}</dd>
            <dt>Helpful</dt>
            <dd>
              {number(a.helpful_count + (voted === true ? 1 : 0))} yes · {number(a.not_helpful_count + (voted === false ? 1 : 0))} no
            </dd>
            {a.tags.length ? (
              <>
                <dt>Tags</dt>
                <dd style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {a.tags.map((tag) => (
                    <Badge key={tag}>{tag}</Badge>
                  ))}
                </dd>
              </>
            ) : null}
          </dl>
        </Card>
      </div>

      <ConfirmDialog
        open={deleting}
        title="Delete this article?"
        message="It will be unlinked from any cases. Archiving keeps it for reference instead."
        onConfirm={() => void remove()}
        onClose={() => setDeleting(false)}
      />
    </>
  );
}
