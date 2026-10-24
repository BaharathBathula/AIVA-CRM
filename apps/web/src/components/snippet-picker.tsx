"use client";

import Link from "next/link";
import { TextQuote } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useToast } from "@/components/providers";
import { api, errorMessage, useApi, useDebounced } from "@/lib/use-api";

export type Snippet = {
  id: string;
  name: string;
  shortcut: string | null;
  body: string;
  category: string | null;
  is_shared: boolean;
  owner_name: string | null;
  usage_count: number;
  updated_at: string;
  can_edit: boolean;
};

/**
 * "Insert snippet" button for any message box. With a record, merge fields
 * are filled in for that person; without one the tokens are inserted as-is.
 */
export function SnippetPicker({
  onInsert,
  recordType,
  recordId,
  disabled,
}: {
  onInsert: (text: string) => void;
  recordType?: "lead" | "contact";
  recordId?: string;
  disabled?: boolean;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const debounced = useDebounced(term, 200);
  const snippets = useApi<{ items: Snippet[] }>(open ? `/snippets${debounced ? `?search=${encodeURIComponent(debounced)}` : ""}` : null);
  const ref = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    function onDown(event: MouseEvent) {
      if (!ref.current?.contains(event.target as Node)) close();
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") close();
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  async function pick(snippet: Snippet) {
    try {
      const result = await api.post<{ text: string }>(`/snippets/${snippet.id}/use`, recordType && recordId ? { record_type: recordType, record_id: recordId } : {});
      onInsert(result.text);
      setOpen(false);
      setTerm("");
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <div className="snippet-picker" ref={ref}>
      <button type="button" className="ui-btn ui-btn-sm" disabled={disabled} aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <TextQuote size={14} /> Snippet
      </button>
      {open ? (
        <div className="ui-popover snippet-popover" role="dialog" aria-label="Insert a snippet">
          <input className="ui-input sm" autoFocus placeholder="Search snippets" aria-label="Search snippets" value={term} onChange={(event) => setTerm(event.target.value)} />
          <div className="snippet-list">
            {snippets.loading && !snippets.data ? <p className="ui-muted ui-small">Loading…</p> : null}
            {(snippets.data?.items ?? []).map((snippet) => (
              <button key={snippet.id} type="button" className="snippet-item" onClick={() => void pick(snippet)}>
                <span className="snippet-item-top">
                  <strong>{snippet.name}</strong>
                  {snippet.shortcut ? <span className="ui-mono ui-muted">/{snippet.shortcut}</span> : null}
                </span>
                <span className="snippet-item-body">{snippet.body}</span>
              </button>
            ))}
            {snippets.data && !snippets.data.items.length ? (
              <p className="ui-muted ui-small">
                No snippets yet. <Link href="/settings/outbound/snippets">Create one</Link>
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
