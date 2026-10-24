"use client";

import Link from "next/link";
import { FlaskConical, Pencil, Plus, Target, Trash2, Users } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { useToast } from "@/components/providers";
import { SettingsShell } from "@/components/settings-shell";
import { ColorDot, ColorPicker, TagsInput, Toggle } from "@/components/settings-kit";
import { Alert, Badge, Card, ConfirmDialog, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";

type Persona = {
  id: string;
  name: string;
  description: string | null;
  color: string;
  title_keywords: string[];
  exclude_keywords: string[];
  seniorities: string[];
  departments: string[];
  is_active: boolean;
  position: number;
  matches: number | null;
};

type PersonaList = {
  items: Persona[];
  people_with_titles: number;
  matched_any: number;
  seniorities: string[];
  departments: string[];
  can_edit: boolean;
};

type Person = { type: "lead" | "contact"; id: string; name: string; title: string | null; company: string | null; email: string | null; href: string; seniority: string | null; department: string | null };

export default function PersonasPage() {
  return (
    <SettingsShell feature="buying_intent">
      <Personas />
    </SettingsShell>
  );
}

function Personas() {
  const toast = useToast();
  const personas = useApi<PersonaList>("/personas");
  const [editing, setEditing] = useState<Persona | "new" | null>(null);
  const [deleting, setDeleting] = useState<Persona | null>(null);
  const [viewing, setViewing] = useState<Persona | null>(null);
  const [busy, setBusy] = useState(false);
  const data = personas.data;
  const canEdit = Boolean(data?.can_edit);
  const coverage = data && data.people_with_titles ? Math.round((data.matched_any / data.people_with_titles) * 100) : 0;

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.del(`/personas/${deleting.id}`);
      toast("Persona deleted.");
      setDeleting(null);
      await personas.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Personas"
        description="Describe the people you sell to by job title. AIVA tags matching leads and contacts, and signals can alert you when a new one appears."
        actions={
          canEdit ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setEditing("new")}>
              <Plus size={16} /> New persona
            </button>
          ) : null
        }
      />
      <div className="ui-stack">
        {personas.error ? <Alert>{personas.error}</Alert> : null}
        <TitleTester />
        {data ? (
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            {data.matched_any} of {data.people_with_titles} people with a job title match at least one active persona ({coverage}%).
          </p>
        ) : null}
        {personas.loading && !data ? (
          <div className="ui-card">
            <LoadingRows />
          </div>
        ) : data?.items.length ? (
          <div className="dm-cards">
            {data.items.map((persona) => (
              <Card
                key={persona.id}
                className={persona.is_active ? undefined : "dm-inactive"}
                title={
                  <span className="ui-row" style={{ gap: 8 }}>
                    <ColorDot color={persona.color} /> {persona.name}
                  </span>
                }
                actions={
                  canEdit ? (
                    <span className="ui-row" style={{ gap: 2 }}>
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Edit ${persona.name}`} onClick={() => setEditing(persona)}>
                        <Pencil size={14} />
                      </button>
                      <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm" aria-label={`Delete ${persona.name}`} onClick={() => setDeleting(persona)}>
                        <Trash2 size={14} />
                      </button>
                    </span>
                  ) : null
                }
              >
                {persona.description ? <p className="ui-small" style={{ marginTop: 0 }}>{persona.description}</p> : null}
                <dl className="dm-facts">
                  {persona.title_keywords.length ? (
                    <>
                      <dt>Title has</dt>
                      <dd>{persona.title_keywords.join(", ")}</dd>
                    </>
                  ) : null}
                  {persona.exclude_keywords.length ? (
                    <>
                      <dt>But not</dt>
                      <dd>{persona.exclude_keywords.join(", ")}</dd>
                    </>
                  ) : null}
                  {persona.seniorities.length ? (
                    <>
                      <dt>Seniority</dt>
                      <dd>{persona.seniorities.join(", ")}</dd>
                    </>
                  ) : null}
                  {persona.departments.length ? (
                    <>
                      <dt>Department</dt>
                      <dd>{persona.departments.join(", ")}</dd>
                    </>
                  ) : null}
                </dl>
                <div className="ui-row" style={{ justifyContent: "space-between", marginTop: 10 }}>
                  {!persona.is_active ? <Badge>Off</Badge> : <span />}
                  <button type="button" className="ui-btn ui-btn-sm" onClick={() => setViewing(persona)}>
                    <Users size={14} /> {persona.matches ?? 0} {persona.matches === 1 ? "person" : "people"}
                  </button>
                </div>
              </Card>
            ))}
          </div>
        ) : (
          <div className="ui-card">
            <EmptyState
              icon={<Target size={20} />}
              title="No personas yet"
              message="Start with your main buyer — for example “Operations leaders”: titles with operations or COO, but not assistant."
            />
          </div>
        )}
      </div>

      <PersonaModal
        persona={editing}
        seniorities={data?.seniorities ?? []}
        departments={data?.departments ?? []}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          await personas.reload();
        }}
      />
      <PeopleModal persona={viewing} onClose={() => setViewing(null)} />
      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete persona?"
        message={`“${deleting?.name ?? ""}” will no longer tag people. Signals that watch for it stop firing.`}
        busy={busy}
        onConfirm={() => void remove()}
        onClose={() => setDeleting(null)}
      />
    </>
  );
}

function TitleTester() {
  const toast = useToast();
  const [title, setTitle] = useState("");
  const [result, setResult] = useState<{ seniority: string | null; department: string | null; personas: Persona[] } | null>(null);
  const [busy, setBusy] = useState(false);

  async function test(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    try {
      setResult(await api.post("/personas/test", { title }));
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Test a job title" icon={<FlaskConical size={16} />}>
      <form className="dm-inline-form" onSubmit={(event) => void test(event)}>
        <input className="ui-input sm" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="VP of Operations" aria-label="Job title to test" maxLength={200} />
        <button type="submit" className="ui-btn ui-btn-sm" disabled={busy || !title.trim()}>
          {busy ? <span className="ui-spinner" /> : null} Test
        </button>
      </form>
      {result ? (
        <div className="dm-test-result">
          <span className="ui-small">
            Seniority: <strong>{result.seniority ?? "—"}</strong> · Department: <strong>{result.department ?? "—"}</strong>
          </span>
          <span className="ui-row" style={{ gap: 6, flexWrap: "wrap" }}>
            {result.personas.length ? (
              result.personas.map((p) => (
                <span key={p.id} className="dm-chip">
                  <ColorDot color={p.color} /> {p.name}
                </span>
              ))
            ) : (
              <span className="ui-muted ui-small">No active persona matches this title.</span>
            )}
          </span>
        </div>
      ) : null}
    </Card>
  );
}

function PersonaModal({
  persona,
  seniorities,
  departments,
  onClose,
  onSaved,
}: {
  persona: Persona | "new" | null;
  seniorities: string[];
  departments: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [color, setColor] = useState("blue");
  const [keywords, setKeywords] = useState<string[]>([]);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [levels, setLevels] = useState<string[]>([]);
  const [depts, setDepts] = useState<string[]>([]);
  const [active, setActive] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!persona) return;
    const p = persona === "new" ? null : persona;
    setName(p?.name ?? "");
    setDescription(p?.description ?? "");
    setColor(p?.color ?? "blue");
    setKeywords(p?.title_keywords ?? []);
    setExcluded(p?.exclude_keywords ?? []);
    setLevels(p?.seniorities ?? []);
    setDepts(p?.departments ?? []);
    setActive(p?.is_active ?? true);
    setError(null);
  }, [persona]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const payload = { name, description: description || null, color, title_keywords: keywords, exclude_keywords: excluded, seniorities: levels, departments: depts, is_active: active };
    try {
      if (persona === "new") await api.post("/personas", payload);
      else if (persona) await api.put(`/personas/${persona.id}`, payload);
      toast(persona === "new" ? "Persona created." : "Persona saved.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  function toggle(list: string[], value: string, on: boolean) {
    return on ? [...list, value] : list.filter((v) => v !== value);
  }

  return (
    <Modal
      open={Boolean(persona)}
      title={persona === "new" ? "New persona" : "Edit persona"}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="persona-form" className="ui-btn ui-btn-primary" disabled={saving || !name.trim()}>
            {saving ? <span className="ui-spinner" /> : null} Save persona
          </button>
        </>
      }
    >
      <form id="persona-form" className="ui-stack" onSubmit={(event) => void submit(event)}>
        {error ? <Alert>{error}</Alert> : null}
        <div className="ui-form-grid">
          <Field label="Name" required>
            <input className="ui-input" value={name} maxLength={120} onChange={(event) => setName(event.target.value)} placeholder="Operations leaders" />
          </Field>
          <Field label="Color">
            <ColorPicker value={color} onChange={setColor} />
          </Field>
          <Field label="Description" className="span-2">
            <input className="ui-input" value={description} maxLength={4000} onChange={(event) => setDescription(event.target.value)} placeholder="Run day-to-day operations and own the budget for tools" />
          </Field>
        </div>
        <Field label="Job title contains any of" hint="Whole words; case doesn't matter. Press Enter after each one.">
          <TagsInput label="Title keywords" value={keywords} onChange={setKeywords} placeholder="operations, COO, head of ops" />
        </Field>
        <Field label="But not if the title contains" hint="Leave out people who share words but aren't buyers.">
          <TagsInput label="Excluded keywords" value={excluded} onChange={setExcluded} placeholder="assistant, intern" />
        </Field>
        <div className="ui-form-grid">
          <Field label="Seniority (optional)">
            <div className="dm-checks" role="group" aria-label="Seniority">
              {seniorities.map((level) => (
                <label key={level} className="ui-check">
                  <input type="checkbox" checked={levels.includes(level)} onChange={(event) => setLevels(toggle(levels, level, event.target.checked))} />
                  <span>{level}</span>
                </label>
              ))}
            </div>
          </Field>
          <Field label="Department (optional)">
            <div className="dm-checks" role="group" aria-label="Department">
              {departments.map((dept) => (
                <label key={dept} className="ui-check">
                  <input type="checkbox" checked={depts.includes(dept)} onChange={(event) => setDepts(toggle(depts, dept, event.target.checked))} />
                  <span>{dept}</span>
                </label>
              ))}
            </div>
          </Field>
        </div>
        <span className="ui-row">
          <Toggle label="Persona is active" checked={active} onChange={setActive} />
          <span className="ui-small">{active ? "Active — tags people and drives signals" : "Off"}</span>
        </span>
      </form>
    </Modal>
  );
}

function PeopleModal({ persona, onClose }: { persona: Persona | null; onClose: () => void }) {
  const people = useApi<{ items: Person[]; total: number }>(persona ? `/personas/${persona.id}/people` : null);
  const rows = persona && people.data ? people.data.items : [];
  return (
    <Modal open={Boolean(persona)} title={persona?.name ?? "People"} description={people.data ? `${people.data.total} matching leads and contacts` : undefined} onClose={onClose} size="lg">
      {people.loading && !people.data ? (
        <LoadingRows rows={5} />
      ) : rows.length ? (
        <div className="ui-table-wrap">
          <table className="ui-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Title</th>
                <th>Company</th>
                <th>Type</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((person) => (
                <tr key={`${person.type}-${person.id}`}>
                  <td>
                    <Link href={person.href} className="cell-title">
                      {person.name}
                    </Link>
                  </td>
                  <td className="ui-small">{person.title ?? "—"}</td>
                  <td className="ui-small">{person.company ?? "—"}</td>
                  <td>
                    <Badge>{person.type === "lead" ? "Lead" : "Contact"}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState icon={<Users size={20} />} title="No one matches yet" message="Leads and contacts with matching job titles show up here." />
      )}
    </Modal>
  );
}
