"use client";

import { AppShell } from "@/components/app-shell";
import { ArticleEditor } from "@/components/article-editor";

export default function NewArticlePage() {
  return (
    <AppShell permission="service.knowledge_write" what="knowledge articles">
      <ArticleEditor />
    </AppShell>
  );
}
