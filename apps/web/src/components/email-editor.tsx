"use client";

import { useEffect, useRef, useState } from "react";

import { Field } from "@/components/ui";
import { api, useApi, useDebounced } from "@/lib/use-api";

type MergeField = { key: string; label: string; token: string };

/** Subject + body editor with merge-field chips and a live preview. */
export function EmailEditor({
  subject,
  body,
  onSubject,
  onBody,
  disabled,
}: {
  subject: string;
  body: string;
  onSubject: (value: string) => void;
  onBody: (value: string) => void;
  disabled?: boolean;
}) {
  const fields = useApi<MergeField[]>("/email-templates/merge-fields");
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const [target, setTarget] = useState<"subject" | "body">("body");
  const [preview, setPreview] = useState<{ subject: string; body: string } | null>(null);
  const debouncedSubject = useDebounced(subject, 400);
  const debouncedBody = useDebounced(body, 400);

  useEffect(() => {
    let active = true;
    api
      .post<{ subject: string; body: string }>("/email-templates/preview", { subject: debouncedSubject, body: debouncedBody })
      .then((result) => active && setPreview(result))
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [debouncedSubject, debouncedBody]);

  function insert(token: string) {
    if (target === "subject") {
      onSubject(`${subject}${subject.endsWith(" ") || !subject ? "" : " "}${token}`);
      return;
    }
    const element = bodyRef.current;
    if (!element) {
      onBody(body + token);
      return;
    }
    const start = element.selectionStart ?? body.length;
    const end = element.selectionEnd ?? body.length;
    onBody(body.slice(0, start) + token + body.slice(end));
    requestAnimationFrame(() => {
      element.focus();
      element.setSelectionRange(start + token.length, start + token.length);
    });
  }

  return (
    <div className="ui-split" style={{ alignItems: "start" }}>
      <div className="ui-stack">
        <Field label="Subject" required>
          <input className="ui-input" value={subject} disabled={disabled} onFocus={() => setTarget("subject")} onChange={(e) => onSubject(e.target.value)} />
        </Field>
        <Field label="Message" required hint="Plain text. Blank lines start new paragraphs. Links become clickable.">
          <textarea
            ref={bodyRef}
            className="ui-textarea"
            rows={12}
            value={body}
            disabled={disabled}
            onFocus={() => setTarget("body")}
            onChange={(e) => onBody(e.target.value)}
          />
        </Field>
        {disabled ? null : (
          <div>
            <div className="ui-muted ui-small" style={{ marginBottom: 6 }}>
              Insert into the {target}:
            </div>
            <div className="chip-list">
              {(fields.data ?? []).map((field) => (
                <button key={field.key} type="button" className="chip" title={field.token} onClick={() => insert(field.token)}>
                  {field.label}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
      <div className="ui-card" style={{ boxShadow: "none", border: "1px solid var(--border)" }}>
        <div className="ui-card-header">
          <h2>Preview</h2>
          <span className="ui-muted ui-small">with sample data</span>
        </div>
        <div className="ui-card-body">
          <div style={{ fontWeight: 750, marginBottom: 12 }}>{preview?.subject || <span className="ui-muted">(no subject)</span>}</div>
          <div style={{ whiteSpace: "pre-wrap", fontSize: 14, lineHeight: 1.6, color: "var(--text-2)" }}>{preview?.body || <span className="ui-muted">Your message appears here.</span>}</div>
        </div>
      </div>
    </div>
  );
}
