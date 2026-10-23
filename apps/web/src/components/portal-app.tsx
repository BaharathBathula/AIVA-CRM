"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, BookOpen, Check, FileText, Handshake, LifeBuoy, LogOut, Mail, Plus, Receipt, Search, Send, X } from "lucide-react";
import { useEffect, useState, type CSSProperties, type FormEvent } from "react";

import { Alert, Badge, CASE_STATUS_TONE, EmptyState, Field, LoadingRows, Modal, PRIORITY_TONE, type Tone } from "@/components/ui";
import { AivaApiError } from "@/lib/api";
import { date, dateTime, moneyExact, relativeTime, titleCase } from "@/lib/format";
import { renderMarkdown } from "@/lib/markdown";
import { api, errorMessage, useApi, useDebounced } from "@/lib/use-api";

export interface PortalInfo {
  name: string;
  audience: "customer" | "partner";
  organization_name: string;
  accent: string | null;
  welcome: string | null;
  logo_text: string;
  support_email: string | null;
  modules: string[];
}

interface Me {
  name: string;
  email: string;
  account_name: string | null;
  modules: string[];
  audience: "customer" | "partner";
}

type Module = "cases" | "knowledge" | "quotes" | "invoices" | "deal_registration";

const MODULE_META: Record<Module, { label: string; icon: typeof LifeBuoy }> = {
  cases: { label: "Support", icon: LifeBuoy },
  knowledge: { label: "Help articles", icon: BookOpen },
  quotes: { label: "Quotes", icon: FileText },
  invoices: { label: "Invoices", icon: Receipt },
  deal_registration: { label: "Deal registration", icon: Handshake },
};

function accentStyle(info: PortalInfo | null): CSSProperties | undefined {
  return info?.accent ? ({ "--portal-accent": info.accent } as CSSProperties) : undefined;
}

/** The whole portal: sign-in when there's no session, the portal app otherwise. */
export function PortalApp({ slug }: { slug: string }) {
  const info = useApi<PortalInfo>(`/portal-api/${slug}`);
  const me = useApi<Me>(`/portal-api/${slug}/me`);

  useEffect(() => {
    if (info.data) document.title = info.data.name;
  }, [info.data]);

  if (info.error) {
    return (
      <div className="public-shell">
        <div className="public-card">
          <h1>Portal unavailable</h1>
          <p>{info.error}</p>
        </div>
      </div>
    );
  }
  if (!info.data || (me.loading && !me.data && !me.error)) {
    return (
      <div className="public-shell">
        <div className="public-card">
          <LoadingRows rows={4} />
        </div>
      </div>
    );
  }
  if (!me.data) return <PortalSignIn slug={slug} info={info.data} />;
  return <PortalHome slug={slug} info={info.data} me={me.data} />;
}

function PortalSignIn({ slug, info }: { slug: string; info: PortalInfo }) {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<{ message: string; login_url?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setSent(await api.post<{ message: string; login_url?: string }>(`/portal-api/${slug}/login`, { email: email.trim() }));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="public-shell portal-shell" style={accentStyle(info)}>
      <div className="public-card portal-signin">
        <div className="portal-brand">{info.logo_text}</div>
        <h1>{info.name}</h1>
        <p>{info.welcome || (info.audience === "partner" ? "Register deals and find partner resources." : "Track your support requests, quotes and invoices in one place.")}</p>
        {sent ? (
          <div className="ui-stack">
            <Alert tone="success">
              <Mail size={15} /> {sent.message}
            </Alert>
            {sent.login_url ? (
              <a className="ui-btn portal-btn" href={sent.login_url}>
                Open the sign-in link (demo)
              </a>
            ) : null}
            <button type="button" className="ui-btn ui-btn-ghost" onClick={() => setSent(null)}>
              Use a different email
            </button>
          </div>
        ) : (
          <form className="ui-stack" onSubmit={submit}>
            {error ? <Alert>{error}</Alert> : null}
            <Field label="Your email address">
              <input className="ui-input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" autoFocus />
            </Field>
            <button type="submit" className="ui-btn portal-btn" disabled={busy}>
              Email me a sign-in link
            </button>
            <span className="ui-muted ui-small">No password needed. The link works once and expires in 30 minutes.</span>
          </form>
        )}
        <div className="public-foot">
          {info.support_email ? (
            <>
              Need access? <a href={`mailto:${info.support_email}`}>{info.support_email}</a> ·{" "}
            </>
          ) : null}
          Powered by AIVA CRM
        </div>
      </div>
    </div>
  );
}

function PortalHome({ slug, info, me }: { slug: string; info: PortalInfo; me: Me }) {
  const params = useSearchParams();
  const router = useRouter();
  const modules = me.modules.filter((m): m is Module => m in MODULE_META);
  const tab = (params.get("tab") as Module | null) && modules.includes(params.get("tab") as Module) ? (params.get("tab") as Module) : modules[0];
  const go = (next: string) => router.push(`/portal/${slug}?${next}`);

  async function signOut() {
    await api.post(`/portal-api/${slug}/logout`).catch(() => undefined);
    window.location.assign(`/portal/${slug}`);
  }

  return (
    <div className="portal-app" style={accentStyle(info)}>
      <header className="portal-top">
        <div className="portal-brand">{info.logo_text}</div>
        <nav className="portal-tabs" aria-label="Portal">
          {modules.map((m) => {
            const Icon = MODULE_META[m].icon;
            return (
              <button key={m} type="button" className={tab === m ? "active" : undefined} onClick={() => go(`tab=${m}`)}>
                <Icon size={15} /> {MODULE_META[m].label}
              </button>
            );
          })}
        </nav>
        <div className="portal-user">
          <span>
            <strong>{me.name}</strong>
            {me.account_name ? <span className="ui-muted ui-small">{me.account_name}</span> : null}
          </span>
          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon" aria-label="Sign out" title="Sign out" onClick={() => void signOut()}>
            <LogOut size={16} />
          </button>
        </div>
      </header>
      <main className="portal-main">
        {tab === "cases" ? <Cases slug={slug} caseId={params.get("case")} go={go} /> : null}
        {tab === "knowledge" ? <Knowledge slug={slug} articleId={params.get("article")} go={go} /> : null}
        {tab === "quotes" ? <Quotes slug={slug} /> : null}
        {tab === "invoices" ? <Invoices slug={slug} /> : null}
        {tab === "deal_registration" ? <Registrations slug={slug} /> : null}
      </main>
      <footer className="public-foot">
        {info.organization_name}
        {info.support_email ? (
          <>
            {" "}
            · <a href={`mailto:${info.support_email}`}>{info.support_email}</a>
          </>
        ) : null}{" "}
        · Powered by AIVA CRM
      </footer>
    </div>
  );
}

// ----------------------------------------------------------------- Cases

interface PortalCase {
  id: string;
  number: string;
  subject: string;
  description: string | null;
  status: string;
  priority: string;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  comments?: { id: string; body: string; from_us: boolean; author: string; created_at: string }[];
}

function Cases({ slug, caseId, go }: { slug: string; caseId: string | null; go: (q: string) => void }) {
  const cases = useApi<PortalCase[]>(caseId ? null : `/portal-api/${slug}/cases`);
  const [creating, setCreating] = useState(false);
  if (caseId) return <CaseDetail slug={slug} caseId={caseId} back={() => go("tab=cases")} />;
  return (
    <>
      <div className="portal-head">
        <div>
          <h1>Support</h1>
          <p className="ui-muted">Your requests and our replies. We email you whenever there&apos;s an update.</p>
        </div>
        <button type="button" className="ui-btn portal-btn" onClick={() => setCreating(true)}>
          <Plus size={15} /> New request
        </button>
      </div>
      {cases.error ? <Alert>{cases.error}</Alert> : null}
      <section className="ui-card">
        {!cases.data ? (
          <LoadingRows />
        ) : cases.data.length ? (
          <ul className="portal-list">
            {cases.data.map((c) => (
              <li key={c.id}>
                <button type="button" onClick={() => go(`tab=cases&case=${c.id}`)}>
                  <span style={{ minWidth: 0, flex: 1 }}>
                    <strong className="ui-truncate" style={{ display: "block" }}>
                      {c.subject}
                    </strong>
                    <span className="ui-muted ui-small">
                      {c.number} · opened {date(c.created_at)} · updated {relativeTime(c.updated_at)}
                    </span>
                  </span>
                  <Badge tone={CASE_STATUS_TONE[c.status] ?? "neutral"}>{titleCase(c.status)}</Badge>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState icon={<LifeBuoy size={20} />} title="No requests yet" message="Open a request and our team will get back to you." />
        )}
      </section>
      <NewCase
        slug={slug}
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(id) => {
          setCreating(false);
          go(`tab=cases&case=${id}`);
        }}
      />
    </>
  );
}

function NewCase({ slug, open, onClose, onCreated }: { slug: string; open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState("medium");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.post<PortalCase>(`/portal-api/${slug}/cases`, { subject: subject.trim(), description: description.trim(), priority });
      setSubject("");
      setDescription("");
      onCreated(created.id);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title="New support request"
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn portal-btn" disabled={busy || subject.trim().length < 3 || description.trim().length < 3} onClick={() => void submit()}>
            Send request
          </button>
        </>
      }
    >
      <div className="ui-stack">
        {error ? <Alert>{error}</Alert> : null}
        <Field label="Subject" required>
          <input className="ui-input" value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Can't export last month's report" />
        </Field>
        <Field label="What's happening?" required>
          <textarea className="ui-textarea" rows={6} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Field label="How urgent is it?">
          <select className="ui-select" value={priority} onChange={(e) => setPriority(e.target.value)}>
            <option value="low">Low — a question</option>
            <option value="medium">Medium — something isn&apos;t working right</option>
            <option value="high">High — it&apos;s blocking our work</option>
            <option value="urgent">Urgent — everything is down</option>
          </select>
        </Field>
      </div>
    </Modal>
  );
}

function CaseDetail({ slug, caseId, back }: { slug: string; caseId: string; back: () => void }) {
  const detail = useApi<PortalCase>(`/portal-api/${slug}/cases/${caseId}`);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const c = detail.data;

  async function send(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      detail.setData(await api.post<PortalCase>(`/portal-api/${slug}/cases/${caseId}/comments`, { body: reply.trim() }));
      setReply("");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={back} style={{ marginBottom: 12 }}>
        <ArrowLeft size={14} /> All requests
      </button>
      {detail.error ? <Alert>{detail.error}</Alert> : null}
      {!c ? (
        <section className="ui-card">
          <LoadingRows />
        </section>
      ) : (
        <section className="ui-card portal-case">
          <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
            <span className="ui-muted ui-small">{c.number}</span>
            <Badge tone={CASE_STATUS_TONE[c.status] ?? "neutral"}>{titleCase(c.status)}</Badge>
            <Badge tone={PRIORITY_TONE[c.priority] ?? "neutral"}>{titleCase(c.priority)}</Badge>
          </div>
          <h1>{c.subject}</h1>
          <div className="portal-thread">
            <div className="portal-msg mine">
              <span className="ui-muted ui-small">You · {dateTime(c.created_at)}</span>
              <p>{c.description}</p>
            </div>
            {(c.comments ?? []).map((m) => (
              <div key={m.id} className={`portal-msg${m.from_us ? "" : " mine"}`}>
                <span className="ui-muted ui-small">
                  {m.from_us ? m.author : "You"} · {dateTime(m.created_at)}
                </span>
                <p>{m.body}</p>
              </div>
            ))}
          </div>
          {c.status === "closed" ? (
            <Alert tone="info">This request is closed. Open a new one if you still need help.</Alert>
          ) : (
            <form className="ui-stack" onSubmit={send}>
              {error ? <Alert>{error}</Alert> : null}
              <textarea className="ui-textarea" rows={3} value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Write a reply" aria-label="Reply" />
              <button type="submit" className="ui-btn portal-btn" style={{ width: "fit-content" }} disabled={busy || !reply.trim()}>
                <Send size={14} /> Send reply
              </button>
            </form>
          )}
        </section>
      )}
    </>
  );
}

// ------------------------------------------------------------- Knowledge

function Knowledge({ slug, articleId, go }: { slug: string; articleId: string | null; go: (q: string) => void }) {
  const [search, setSearch] = useState("");
  const q = useDebounced(search);
  const articles = useApi<{ id: string; title: string; summary: string | null; category: string | null }[]>(
    articleId ? null : `/portal-api/${slug}/articles${q ? `?q=${encodeURIComponent(q)}` : ""}`,
  );
  const article = useApi<{ id: string; title: string; summary: string | null; body: string; category: string | null }>(articleId ? `/portal-api/${slug}/articles/${articleId}` : null);

  if (articleId) {
    return (
      <>
        <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => go("tab=knowledge")} style={{ marginBottom: 12 }}>
          <ArrowLeft size={14} /> All articles
        </button>
        <section className="ui-card portal-case">
          {article.error ? <Alert>{article.error}</Alert> : null}
          {!article.data ? (
            <LoadingRows />
          ) : (
            <>
              {article.data.category ? <span className="ui-muted ui-small">{article.data.category}</span> : null}
              <h1>{article.data.title}</h1>
              <article className="ui-prose" dangerouslySetInnerHTML={{ __html: renderMarkdown(article.data.body) }} />
            </>
          )}
        </section>
      </>
    );
  }

  return (
    <>
      <div className="portal-head">
        <div>
          <h1>Help articles</h1>
          <p className="ui-muted">Answers to common questions.</p>
        </div>
        <div className="search-input">
          <Search size={15} />
          <input className="ui-input" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search articles" aria-label="Search articles" />
        </div>
      </div>
      <section className="ui-card">
        {!articles.data ? (
          <LoadingRows />
        ) : articles.data.length ? (
          <ul className="portal-list">
            {articles.data.map((a) => (
              <li key={a.id}>
                <button type="button" onClick={() => go(`tab=knowledge&article=${a.id}`)}>
                  <span style={{ minWidth: 0, flex: 1 }}>
                    <strong style={{ display: "block" }}>{a.title}</strong>
                    {a.summary ? <span className="ui-muted ui-small">{a.summary}</span> : null}
                  </span>
                  {a.category ? <Badge>{a.category}</Badge> : null}
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState icon={<BookOpen size={20} />} title={q ? "No matching articles" : "No articles yet"} />
        )}
      </section>
    </>
  );
}

// ---------------------------------------------------------------- Quotes

interface PortalQuote {
  id: string;
  number: string;
  name: string;
  status: "sent" | "accepted" | "declined";
  total: number;
  currency: string;
  valid_until: string | null;
  sent_at: string | null;
  lines: { name: string; description: string | null; quantity: number; unit_price: number; discount_percent: number; line_total: number }[];
  subtotal: number;
  discount_total: number;
  tax_total: number;
  terms: string | null;
}

const QUOTE_TONE: Record<string, Tone> = { sent: "info", accepted: "success", declined: "danger" };

function Quotes({ slug }: { slug: string }) {
  const quotes = useApi<PortalQuote[]>(`/portal-api/${slug}/quotes`);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function decide(quote: PortalQuote, accept: boolean) {
    setBusy(quote.id);
    setError(null);
    try {
      const next = await api.post<PortalQuote>(`/portal-api/${slug}/quotes/${quote.id}/decision`, { accept });
      quotes.setData((current) => (current ?? []).map((q) => (q.id === next.id ? next : q)));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="portal-head">
        <div>
          <h1>Quotes</h1>
          <p className="ui-muted">Review the quotes we&apos;ve sent you and accept them online.</p>
        </div>
      </div>
      {error ? <Alert>{error}</Alert> : null}
      {!quotes.data ? (
        <section className="ui-card">
          <LoadingRows />
        </section>
      ) : quotes.data.length ? (
        <div className="ui-stack">
          {quotes.data.map((quote) => (
            <section key={quote.id} className="ui-card portal-quote">
              <div className="ui-row" style={{ gap: 10, flexWrap: "wrap" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <strong>{quote.name}</strong>
                  <div className="ui-muted ui-small">
                    {quote.number}
                    {quote.valid_until ? ` · valid until ${date(quote.valid_until)}` : ""}
                  </div>
                </div>
                <Badge tone={QUOTE_TONE[quote.status]}>{titleCase(quote.status)}</Badge>
                <strong className="ui-num" style={{ fontSize: 18 }}>
                  {moneyExact(quote.total, quote.currency)}
                </strong>
              </div>
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" style={{ width: "fit-content" }} onClick={() => setOpen(open === quote.id ? null : quote.id)}>
                {open === quote.id ? "Hide details" : "View details"}
              </button>
              {open === quote.id ? (
                <>
                  <div className="ui-table-wrap">
                    <table className="ui-table">
                      <thead>
                        <tr>
                          <th>Item</th>
                          <th className="ui-num">Qty</th>
                          <th className="ui-num">Price</th>
                          <th className="ui-num">Amount</th>
                        </tr>
                      </thead>
                      <tbody>
                        {quote.lines.map((line, i) => (
                          <tr key={`${line.name}-${i}`}>
                            <td>
                              {line.name}
                              {line.description ? <div className="ui-muted ui-small">{line.description}</div> : null}
                            </td>
                            <td className="ui-num">{line.quantity}</td>
                            <td className="ui-num">
                              {moneyExact(line.unit_price, quote.currency)}
                              {line.discount_percent ? <div className="ui-muted ui-small">−{line.discount_percent}%</div> : null}
                            </td>
                            <td className="ui-num">{moneyExact(line.line_total, quote.currency)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="quote-totals">
                    <span className="ui-muted">Subtotal</span>
                    <span className="ui-num">{moneyExact(quote.subtotal, quote.currency)}</span>
                    <span className="ui-muted">Discounts</span>
                    <span className="ui-num">−{moneyExact(quote.discount_total, quote.currency)}</span>
                    <span className="ui-muted">Tax</span>
                    <span className="ui-num">{moneyExact(quote.tax_total, quote.currency)}</span>
                    <span className="grand">Total</span>
                    <span className="grand ui-num">{moneyExact(quote.total, quote.currency)}</span>
                  </div>
                  {quote.terms ? <p className="ui-small" style={{ whiteSpace: "pre-wrap" }}>{quote.terms}</p> : null}
                </>
              ) : null}
              {quote.status === "sent" ? (
                <div className="ui-row" style={{ gap: 8 }}>
                  <button type="button" className="ui-btn" disabled={busy === quote.id} onClick={() => void decide(quote, false)}>
                    <X size={14} /> Decline
                  </button>
                  <button type="button" className="ui-btn portal-btn" disabled={busy === quote.id} onClick={() => void decide(quote, true)}>
                    <Check size={14} /> Accept quote
                  </button>
                </div>
              ) : null}
            </section>
          ))}
        </div>
      ) : (
        <section className="ui-card">
          <EmptyState icon={<FileText size={20} />} title="No quotes yet" />
        </section>
      )}
    </>
  );
}

// -------------------------------------------------------------- Invoices

function Invoices({ slug }: { slug: string }) {
  const invoices = useApi<{ id: string; number: string; status: string; issue_date: string; due_date: string | null; total: number; balance: number; currency: string }[]>(
    `/portal-api/${slug}/invoices`,
  );
  return (
    <>
      <div className="portal-head">
        <div>
          <h1>Invoices</h1>
          <p className="ui-muted">What you&apos;ve been billed and what&apos;s still open.</p>
        </div>
      </div>
      <section className="ui-card">
        {!invoices.data ? (
          <LoadingRows />
        ) : invoices.data.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Invoice</th>
                  <th>Issued</th>
                  <th>Due</th>
                  <th>Status</th>
                  <th className="ui-num">Total</th>
                  <th className="ui-num">Balance</th>
                </tr>
              </thead>
              <tbody>
                {invoices.data.map((invoice) => (
                  <tr key={invoice.id}>
                    <td className="cell-title">{invoice.number}</td>
                    <td>{date(invoice.issue_date)}</td>
                    <td>{date(invoice.due_date)}</td>
                    <td>
                      <Badge tone={invoice.status === "paid" ? "success" : "info"}>{invoice.status === "paid" ? "Paid" : "Open"}</Badge>
                    </td>
                    <td className="ui-num">{moneyExact(invoice.total, invoice.currency)}</td>
                    <td className="ui-num ui-strong">{moneyExact(invoice.balance, invoice.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<Receipt size={20} />} title="No invoices" />
        )}
      </section>
    </>
  );
}

// ---------------------------------------------------- Deal registration

function Registrations({ slug }: { slug: string }) {
  const rows = useApi<{ id: string; company_name: string; contact: string; status: string; estimated_value: number | null; created_at: string }[]>(
    `/portal-api/${slug}/registrations`,
  );
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState({ company_name: "", first_name: "", last_name: "", email: "", phone: "", estimated_value: "", notes: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ possible_duplicate: boolean }>(`/portal-api/${slug}/registrations`, {
        ...values,
        email: values.email || null,
        phone: values.phone || null,
        notes: values.notes || null,
        estimated_value: values.estimated_value ? Number(values.estimated_value) : null,
      });
      setNotice(
        result.possible_duplicate
          ? "Registered. Another partner or our team may already be working this company — we'll confirm who owns it."
          : "Registered. We'll review it and let you know.",
      );
      setOpen(false);
      setValues({ company_name: "", first_name: "", last_name: "", email: "", phone: "", estimated_value: "", notes: "" });
      await rows.reload();
    } catch (caught) {
      setError(caught instanceof AivaApiError ? caught.message : errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const set = (key: keyof typeof values) => (e: { target: { value: string } }) => setValues({ ...values, [key]: e.target.value });

  return (
    <>
      <div className="portal-head">
        <div>
          <h1>Deal registration</h1>
          <p className="ui-muted">Register opportunities you&apos;re working so they&apos;re protected and routed to the right team.</p>
        </div>
        <button type="button" className="ui-btn portal-btn" onClick={() => setOpen(true)}>
          <Plus size={15} /> Register a deal
        </button>
      </div>
      {notice ? <Alert tone="success">{notice}</Alert> : null}
      <section className="ui-card">
        {!rows.data ? (
          <LoadingRows />
        ) : rows.data.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Company</th>
                  <th>Contact</th>
                  <th>Status</th>
                  <th className="ui-num">Estimated value</th>
                  <th>Registered</th>
                </tr>
              </thead>
              <tbody>
                {rows.data.map((row) => (
                  <tr key={row.id}>
                    <td className="cell-title">{row.company_name}</td>
                    <td>{row.contact || "—"}</td>
                    <td>
                      <Badge tone={row.status === "qualified" || row.status === "converted" ? "success" : row.status === "unqualified" ? "danger" : "info"}>
                        {row.status === "new" ? "Under review" : titleCase(row.status)}
                      </Badge>
                    </td>
                    <td className="ui-num">{row.estimated_value !== null ? moneyExact(row.estimated_value) : "—"}</td>
                    <td>{date(row.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<Handshake size={20} />} title="No registered deals" />
        )}
      </section>
      <Modal
        open={open}
        title="Register a deal"
        onClose={() => setOpen(false)}
        size="lg"
        footer={
          <>
            <button type="button" className="ui-btn" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button type="button" className="ui-btn portal-btn" disabled={busy || !values.company_name.trim() || !values.last_name.trim()} onClick={() => void submit()}>
              Register
            </button>
          </>
        }
      >
        <div className="ui-stack">
          {error ? <Alert>{error}</Alert> : null}
          <Field label="Company" required>
            <input className="ui-input" value={values.company_name} onChange={set("company_name")} />
          </Field>
          <div className="ui-form-grid">
            <Field label="Contact first name">
              <input className="ui-input" value={values.first_name} onChange={set("first_name")} />
            </Field>
            <Field label="Contact last name" required>
              <input className="ui-input" value={values.last_name} onChange={set("last_name")} />
            </Field>
            <Field label="Email">
              <input className="ui-input" type="email" value={values.email} onChange={set("email")} />
            </Field>
            <Field label="Phone">
              <input className="ui-input" type="tel" value={values.phone} onChange={set("phone")} />
            </Field>
            <Field label="Estimated value (USD)">
              <input className="ui-input" type="number" min={0} value={values.estimated_value} onChange={set("estimated_value")} />
            </Field>
          </div>
          <Field label="Notes">
            <textarea className="ui-textarea" rows={3} value={values.notes} onChange={set("notes")} placeholder="Timeline, needs, who else is involved" />
          </Field>
        </div>
      </Modal>
    </>
  );
}

export function PortalAuth({ slug, token }: { slug: string; token: string | null }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setError("This sign-in link is incomplete. Ask for a new one.");
      return;
    }
    let active = true;
    api
      .post(`/portal-api/${slug}/verify`, { token })
      .then(() => active && router.replace(`/portal/${slug}`))
      .catch((caught) => active && setError(errorMessage(caught)));
    return () => {
      active = false;
    };
  }, [slug, token, router]);

  return (
    <div className="public-shell">
      <div className="public-card">
        {error ? (
          <>
            <h1>Couldn&apos;t sign you in</h1>
            <p>{error}</p>
            <Link className="ui-btn" href={`/portal/${slug}`}>
              Get a new link
            </Link>
          </>
        ) : (
          <>
            <h1>Signing you in…</h1>
            <LoadingRows rows={2} />
          </>
        )}
      </div>
    </div>
  );
}
