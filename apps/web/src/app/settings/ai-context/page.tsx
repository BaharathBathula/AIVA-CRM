"use client";

import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { SettingsShell } from "@/components/settings-shell";
import { SettingRow, SettingsBody, TagsInput, useWorkspaceSettings } from "@/components/settings-kit";
import { Card, Field, PageHeader } from "@/components/ui";

type Use = "drafts" | "summaries" | "agents" | "ask" | "conversations";

type AiContext = {
  company_overview: string;
  products: string;
  value_props: string;
  ideal_customer: string;
  competitors: { name: string; differentiator: string }[];
  tone: string;
  words_to_avoid: string[];
  signature: string;
  faqs: { question: string; answer: string }[];
  use_for: Use[];
};

const USES: { key: Use; label: string; hint: string }[] = [
  { key: "drafts", label: "Email drafts", hint: "Write with AI on records and in sequences" },
  { key: "summaries", label: "Record summaries", hint: "The summary card on leads, contacts and deals" },
  { key: "agents", label: "AI agents", hint: "Suggestions from the agents in Insights → AI agents" },
  { key: "ask", label: "Ask AIVA", hint: "Questions about your CRM data" },
  { key: "conversations", label: "Conversation notes", hint: "Call and meeting summaries and field answers" },
];

const TEXT_FIELDS: { key: "company_overview" | "products" | "value_props" | "ideal_customer"; label: string; hint: string; placeholder: string }[] = [
  {
    key: "company_overview",
    label: "Company overview",
    hint: "Who you are and what you do, in a few sentences.",
    placeholder: "We install and service heating and cooling systems for commercial buildings across Texas…",
  },
  { key: "products", label: "Products and services", hint: "What you sell, with plans or prices if you share them.", placeholder: "Maintenance plans from $199/month, emergency repairs, new installs…" },
  { key: "value_props", label: "Why customers choose you", hint: "The outcomes and proof points reps should lean on.", placeholder: "4-hour response guarantee, 98% first-visit fix rate…" },
  { key: "ideal_customer", label: "Ideal customer", hint: "Who buys, their problems and what they care about.", placeholder: "Facility managers at 50–500 person offices, schools and clinics…" },
];

export default function AiContextPage() {
  return (
    <SettingsShell>
      <AiContextCenter />
    </SettingsShell>
  );
}

function AiContextCenter() {
  const state = useWorkspaceSettings<AiContext>("ai_context");
  return (
    <>
      <PageHeader
        title="AI Context Center"
        description="Teach AIVA's AI about your business once. It uses this in every draft, summary and suggestion so the words sound like your company — not a generic assistant."
      />
      <SettingsBody state={state}>
        {(draft) => {
          const off = !state.canEdit;
          return (
            <div className="ui-stack">
              <Card title="About your company">
                <div className="ui-stack" style={{ gap: 14 }}>
                  {TEXT_FIELDS.map((field) => (
                    <Field key={field.key} label={field.label} hint={`${field.hint} ${draft[field.key].length}/4000`}>
                      <textarea
                        className="ui-textarea"
                        rows={4}
                        maxLength={4000}
                        disabled={off}
                        placeholder={field.placeholder}
                        value={draft[field.key]}
                        onChange={(event) => state.set(field.key, event.target.value)}
                      />
                    </Field>
                  ))}
                </div>
              </Card>

              <Card title="Competitors">
                <p className="ui-muted ui-small" style={{ marginTop: 0 }}>
                  When a prospect mentions one, AIVA knows how you compare.
                </p>
                {draft.competitors.length ? (
                  <div className="settings-list">
                    {draft.competitors.map((competitor, index) => (
                      <div key={index} className="dm-pair">
                        <input
                          className="ui-input sm"
                          aria-label="Competitor name"
                          placeholder="Competitor"
                          maxLength={120}
                          disabled={off}
                          value={competitor.name}
                          onChange={(event) => state.set("competitors", draft.competitors.map((c, i) => (i === index ? { ...c, name: event.target.value } : c)))}
                        />
                        <input
                          className="ui-input sm"
                          aria-label="How you're different"
                          placeholder="How you're different"
                          maxLength={1000}
                          disabled={off}
                          value={competitor.differentiator}
                          onChange={(event) =>
                            state.set("competitors", draft.competitors.map((c, i) => (i === index ? { ...c, differentiator: event.target.value } : c)))
                          }
                        />
                        {!off ? (
                          <button
                            type="button"
                            className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm"
                            aria-label="Remove competitor"
                            onClick={() => state.set("competitors", draft.competitors.filter((_, i) => i !== index))}
                          >
                            <Trash2 size={14} />
                          </button>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : null}
                {!off && draft.competitors.length < 30 ? (
                  <PairAdder
                    first="Competitor"
                    second="How you're different"
                    onAdd={(name, differentiator) => state.set("competitors", [...draft.competitors, { name, differentiator }])}
                  />
                ) : null}
                {off && !draft.competitors.length ? <p className="ui-muted ui-small" style={{ margin: 0 }}>None added.</p> : null}
              </Card>

              <Card title="How AIVA writes">
                <div className="settings-rows">
                  <SettingRow label="Tone of voice" hint="A short description, like “Warm, direct, no jargon”." stacked>
                    <input className="ui-input" maxLength={500} disabled={off} value={draft.tone} onChange={(event) => state.set("tone", event.target.value)} />
                  </SettingRow>
                  <SettingRow label="Words to avoid" hint="AIVA won't use these in anything it writes." stacked>
                    <TagsInput label="Words to avoid" value={draft.words_to_avoid} disabled={off} placeholder="synergy, leverage" onChange={(value) => state.set("words_to_avoid", value)} />
                  </SettingRow>
                  <SettingRow label="Email signature" hint="Added to the end of AI email drafts." stacked>
                    <textarea className="ui-textarea" rows={3} maxLength={1000} disabled={off} value={draft.signature} onChange={(event) => state.set("signature", event.target.value)} />
                  </SettingRow>
                </div>
              </Card>

              <Card title="Common questions">
                <p className="ui-muted ui-small" style={{ marginTop: 0 }}>
                  Answers to questions prospects often ask, so drafts get them right.
                </p>
                {draft.faqs.length ? (
                  <div className="settings-list">
                    {draft.faqs.map((faq, index) => (
                      <div key={index} className="dm-faq">
                        <div className="dm-faq-fields">
                          <input
                            className="ui-input sm"
                            aria-label="Question"
                            placeholder="Do you offer a contract-free plan?"
                            maxLength={300}
                            disabled={off}
                            value={faq.question}
                            onChange={(event) => state.set("faqs", draft.faqs.map((f, i) => (i === index ? { ...f, question: event.target.value } : f)))}
                          />
                          <textarea
                            className="ui-textarea"
                            aria-label="Answer"
                            rows={2}
                            placeholder="Yes — month-to-month plans start at…"
                            maxLength={2000}
                            disabled={off}
                            value={faq.answer}
                            onChange={(event) => state.set("faqs", draft.faqs.map((f, i) => (i === index ? { ...f, answer: event.target.value } : f)))}
                          />
                        </div>
                        {!off ? (
                          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label="Remove question" onClick={() => state.set("faqs", draft.faqs.filter((_, i) => i !== index))}>
                            <Trash2 size={14} />
                          </button>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : null}
                {!off && draft.faqs.length < 50 ? (
                  <PairAdder first="Question" second="Answer" multiline onAdd={(question, answer) => state.set("faqs", [...draft.faqs, { question, answer }])} />
                ) : null}
                {off && !draft.faqs.length ? <p className="ui-muted ui-small" style={{ margin: 0 }}>None added.</p> : null}
              </Card>

              <Card title="Where AIVA uses this">
                <div className="dm-checks wide">
                  {USES.map((use) => (
                    <label key={use.key} className="ui-check dm-use">
                      <input
                        type="checkbox"
                        disabled={off}
                        checked={draft.use_for.includes(use.key)}
                        onChange={(event) => state.set("use_for", event.target.checked ? [...draft.use_for, use.key] : draft.use_for.filter((u) => u !== use.key))}
                      />
                      <span>
                        <strong>{use.label}</strong>
                        <span className="ui-muted ui-small" style={{ display: "block" }}>
                          {use.hint}
                        </span>
                      </span>
                    </label>
                  ))}
                </div>
              </Card>
            </div>
          );
        }}
      </SettingsBody>
    </>
  );
}

/** A blank row for adding a competitor or a question; only adds once the first box is filled. */
function PairAdder({ first, second, multiline, onAdd }: { first: string; second: string; multiline?: boolean; onAdd: (a: string, b: string) => void }) {
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  function add() {
    if (!a.trim()) return;
    onAdd(a.trim(), b.trim());
    setA("");
    setB("");
  }
  return (
    <div className={`dm-adder${multiline ? " multiline" : ""}`}>
      <input className="ui-input sm" aria-label={`New ${first.toLowerCase()}`} placeholder={first} value={a} maxLength={multiline ? 300 : 120} onChange={(event) => setA(event.target.value)} onKeyDown={(event) => event.key === "Enter" && (event.preventDefault(), add())} />
      {multiline ? (
        <textarea className="ui-textarea" rows={2} aria-label={`New ${second.toLowerCase()}`} placeholder={second} value={b} maxLength={2000} onChange={(event) => setB(event.target.value)} />
      ) : (
        <input className="ui-input sm" aria-label={`New ${second.toLowerCase()}`} placeholder={second} value={b} maxLength={1000} onChange={(event) => setB(event.target.value)} onKeyDown={(event) => event.key === "Enter" && (event.preventDefault(), add())} />
      )}
      <button type="button" className="ui-btn ui-btn-sm" onClick={add} disabled={!a.trim()}>
        <Plus size={14} /> Add
      </button>
    </div>
  );
}
