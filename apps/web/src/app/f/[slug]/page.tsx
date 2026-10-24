"use client";

import { useParams, useSearchParams } from "next/navigation";
import { CheckCircle2 } from "lucide-react";
import { Suspense, useEffect, useMemo, useState, type FormEvent } from "react";

import { Alert, Field, LoadingRows } from "@/components/ui";
import { AivaApiError } from "@/lib/api";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface PublicField {
  key: string;
  label: string;
  type: string;
  required: boolean;
  options: string[];
  placeholder?: string;
  help?: string;
  step: number;
}

interface PublicForm {
  title: string;
  description: string;
  submit_label: string;
  success_message: string;
  redirect_url: string | null;
  accent: string | null;
  organization_name: string;
  fields: PublicField[];
  steps: number;
}

const ATTRIBUTION = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "gbraid", "wbraid", "ga_client_id"];
const SAFE_VALUE = /^[A-Za-z0-9_\-.:%+ /]{1,200}$/;

function PublicFormView() {
  const { slug } = useParams<{ slug: string }>();
  const params = useSearchParams();
  const embed = params.get("embed") === "1";
  const form = useApi<PublicForm>(`/public/forms/${slug}`);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [step, setStep] = useState(1);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [honeypot, setHoneypot] = useState("");

  // Attribution handed over by AIVA's tracking script on the page this form is embedded in
  // (ad click IDs and the Google Analytics client ID). URL parameters win.
  const [fromPage, setFromPage] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!embed || window.parent === window) return;
    function receive(event: MessageEvent) {
      if (event.source !== window.parent || !event.data || event.data.type !== "aiva:attribution") return;
      const incoming = event.data.data as Record<string, unknown> | undefined;
      const clean: Record<string, string> = {};
      ATTRIBUTION.forEach((key) => {
        const value = incoming?.[key];
        if (typeof value === "string" && SAFE_VALUE.test(value)) clean[key] = value;
      });
      setFromPage(clean);
    }
    window.addEventListener("message", receive);
    window.parent.postMessage({ type: "aiva:attribution-request" }, "*");
    return () => window.removeEventListener("message", receive);
  }, [embed]);

  const attribution = useMemo(() => {
    const data: Record<string, string> = { ...fromPage };
    ATTRIBUTION.forEach((key) => {
      const value = params.get(key);
      if (value) data[key] = value;
    });
    return data;
  }, [params, fromPage]);

  useEffect(() => {
    document.title = form.data ? `${form.data.title} · ${form.data.organization_name}` : "Form";
  }, [form.data]);

  if (form.error) {
    return (
      <div className={`public-shell${embed ? " embed" : ""}`}>
        <div className="public-card">
          <h1>This form isn&apos;t available</h1>
          <p>{form.error}</p>
        </div>
      </div>
    );
  }
  if (!form.data) {
    return (
      <div className={`public-shell${embed ? " embed" : ""}`}>
        <div className="public-card">
          <LoadingRows rows={6} />
        </div>
      </div>
    );
  }

  const data = form.data;
  const fields = data.fields.filter((f) => (f.step || 1) === step);
  const last = step >= data.steps;
  const accent = data.accent ? { background: data.accent, borderColor: data.accent, color: "#fff" } : undefined;

  function check(scope: PublicField[]) {
    const problems: Record<string, string> = {};
    for (const field of scope) {
      const value = values[field.key];
      if (field.required && (value === undefined || value === "" || value === false)) problems[field.key] = `${field.label} is required.`;
      if (field.type === "email" && value && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(value))) problems[field.key] = "Enter a valid email address.";
    }
    setErrors(problems);
    return !Object.keys(problems).length;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!check(fields)) return;
    if (!last) {
      setStep(step + 1);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post(`/public/forms/${slug}/submit`, {
        data: { ...values, _hp: honeypot },
        attribution: { ...attribution, referrer: document.referrer || undefined, page: embed ? document.referrer || undefined : window.location.href },
      });
      if (data.redirect_url) {
        (window.top ?? window).location.href = data.redirect_url;
        return;
      }
      setDone(true);
    } catch (caught) {
      if (caught instanceof AivaApiError && caught.body.fields) {
        setErrors(caught.body.fields as Record<string, string>);
        const firstStep = data.fields.find((f) => (caught.body.fields as Record<string, string>)[f.key])?.step;
        if (firstStep) setStep(firstStep);
      }
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`public-shell${embed ? " embed" : ""}`}>
      <div className="public-card">
        {done ? (
          <div className="ui-stack" style={{ justifyItems: "center", textAlign: "center", padding: "20px 0" }}>
            <CheckCircle2 size={44} color="var(--success)" />
            <h1>{data.success_message}</h1>
          </div>
        ) : (
          <>
            <h1>{data.title}</h1>
            {data.description ? <p>{data.description}</p> : null}
            {data.steps > 1 ? (
              <div className="form-steps" aria-label={`Step ${step} of ${data.steps}`}>
                {Array.from({ length: data.steps }, (_, i) => (
                  <span key={i} className={i < step ? "done" : ""} />
                ))}
              </div>
            ) : null}
            <form className="ui-stack" onSubmit={submit} noValidate>
              {error ? <Alert>{error}</Alert> : null}
              <div className="hp-field" aria-hidden>
                <label>
                  Leave this empty
                  <input tabIndex={-1} autoComplete="off" value={honeypot} onChange={(e) => setHoneypot(e.target.value)} />
                </label>
              </div>
              {fields.map((field) => (
                <Field key={field.key} label={field.label} required={field.required} hint={field.help || undefined} error={errors[field.key]}>
                  {field.type === "textarea" ? (
                    <textarea className="ui-textarea" rows={4} value={String(values[field.key] ?? "")} placeholder={field.placeholder} onChange={(e) => setValues({ ...values, [field.key]: e.target.value })} />
                  ) : field.type === "select" ? (
                    <select className="ui-select" value={String(values[field.key] ?? "")} onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}>
                      <option value="">Choose…</option>
                      {field.options.map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  ) : field.type === "checkbox" ? (
                    <label className="ui-check">
                      <input type="checkbox" checked={values[field.key] === true} onChange={(e) => setValues({ ...values, [field.key]: e.target.checked })} /> Yes
                    </label>
                  ) : (
                    <input
                      className="ui-input"
                      type={field.type === "tel" ? "tel" : field.type}
                      value={String(values[field.key] ?? "")}
                      placeholder={field.placeholder}
                      autoComplete={field.type === "email" ? "email" : field.type === "tel" ? "tel" : undefined}
                      onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
                    />
                  )}
                </Field>
              ))}
              <div className="ui-row" style={{ gap: 10 }}>
                {step > 1 ? (
                  <button type="button" className="ui-btn" onClick={() => setStep(step - 1)}>
                    Back
                  </button>
                ) : null}
                <button type="submit" className="ui-btn ui-btn-primary" style={{ flex: 1, ...accent }} disabled={busy}>
                  {busy ? "Sending…" : last ? data.submit_label : "Next"}
                </button>
              </div>
            </form>
          </>
        )}
        {embed ? null : (
          <div className="public-foot">
            {data.organization_name} · Powered by AIVA CRM
          </div>
        )}
      </div>
    </div>
  );
}

export default function PublicFormPage() {
  return (
    <Suspense fallback={null}>
      <PublicFormView />
    </Suspense>
  );
}
