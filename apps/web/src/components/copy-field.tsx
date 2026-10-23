"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";

/** A read-only value with a copy button (links, embed codes, keys). */
export function CopyField({ value, label = "Copy", multiline }: { value: string; label?: string; multiline?: boolean }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      const area = document.createElement("textarea");
      area.value = value;
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  return (
    <div className="copy-field" style={multiline ? { alignItems: "flex-start" } : undefined}>
      {multiline ? (
        <pre className="code-block" style={{ flex: 1 }}>
          {value}
        </pre>
      ) : (
        <input className="ui-input" readOnly value={value} onFocus={(e) => e.target.select()} aria-label={label} />
      )}
      <button type="button" className="ui-btn" onClick={() => void copy()} aria-label={label}>
        {copied ? <Check size={15} /> : <Copy size={15} />} {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}
