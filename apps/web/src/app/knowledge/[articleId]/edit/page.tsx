"use client";

import { useParams } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ArticleEditor } from "@/components/article-editor";

export default function EditArticlePage() {
  const { articleId } = useParams<{ articleId: string }>();
  return (
    <AppShell permission="service.knowledge_write" what="knowledge articles">
      <ArticleEditor articleId={articleId} />
    </AppShell>
  );
}
