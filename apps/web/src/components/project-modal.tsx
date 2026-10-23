"use client";

import { useEffect, useState } from "react";

import { Alert, Field, Modal } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";
import type { Member, Project } from "@/lib/workspace";
import type { Account } from "@/types/account";
import type { Opportunity } from "@/types/opportunity";

/** Create a project or edit its details. */
export function ProjectModal({ open, project, onClose, onSaved }: { open: boolean; project?: Project | null; onClose: () => void; onSaved: (project: Project) => void }) {
  const members = useApi<Member[]>(open ? "/meta/members" : null);
  const accounts = useApi<Account[]>(open ? "/accounts?limit=100" : null);
  const opportunities = useApi<Opportunity[]>(open ? "/opportunities?limit=100" : null);
  const [form, setForm] = useState({ name: "", description: "", status: "planning", priority: "medium", account_id: "", opportunity_id: "", owner_user_id: "", start_date: "", due_date: "", budget: "" });
  const [team, setTeam] = useState<string[]>([]);
  const [milestones, setMilestones] = useState("Kickoff\nGo-live");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    if (project) {
      setForm({
        name: project.name,
        description: project.description ?? "",
        status: project.status,
        priority: project.priority,
        account_id: project.account_id ?? "",
        opportunity_id: project.opportunity_id ?? "",
        owner_user_id: project.owner_user_id ?? "",
        start_date: project.start_date ?? "",
        due_date: project.due_date ?? "",
        budget: project.budget === null ? "" : String(project.budget),
      });
      setTeam(project.member_user_ids);
    } else {
      setForm({ name: "", description: "", status: "planning", priority: "medium", account_id: "", opportunity_id: "", owner_user_id: "", start_date: new Date().toISOString().slice(0, 10), due_date: "", budget: "" });
      setTeam([]);
      setMilestones("Kickoff\nGo-live");
    }
  }, [open, project]);

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setForm({ ...form, [key]: e.target.value });

  async function save() {
    setError(null);
    if (!form.name.trim()) return setError("Name the project.");
    const payload = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      status: form.status,
      priority: form.priority,
      account_id: form.account_id || null,
      opportunity_id: form.opportunity_id || null,
      owner_user_id: form.owner_user_id || null,
      start_date: form.start_date || null,
      due_date: form.due_date || null,
      budget: form.budget === "" ? null : Number(form.budget),
      member_user_ids: team,
    };
    setBusy(true);
    try {
      onSaved(
        project
          ? await api.patch<Project>(`/projects/${project.id}`, payload)
          : await api.post<Project>("/projects", {
              ...payload,
              milestones: milestones
                .split("\n")
                .map((m) => m.trim())
                .filter(Boolean)
                .map((name) => ({ name })),
            })
      );
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      size="lg"
      title={project ? "Edit project" : "New project"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={() => void save()} disabled={busy}>
            {busy ? <span className="ui-spinner" /> : null} {project ? "Save" : "Create project"}
          </button>
        </>
      }
    >
      {error ? (
        <div style={{ marginBottom: 12 }}>
          <Alert>{error}</Alert>
        </div>
      ) : null}
      <div className="ui-form-grid">
        <Field label="Name" required className="span-2">
          <input className="ui-input" value={form.name} onChange={set("name")} placeholder="Northwind warehouse rollout" />
        </Field>
        <Field label="Status">
          <select className="ui-select" value={form.status} onChange={set("status")}>
            <option value="planning">Planning</option>
            <option value="active">Active</option>
            <option value="on_hold">On hold</option>
            <option value="completed">Completed</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </Field>
        <Field label="Priority">
          <select className="ui-select" value={form.priority} onChange={set("priority")}>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="critical">Critical</option>
          </select>
        </Field>
        <Field label="Deal">
          <select className="ui-select" value={form.opportunity_id} onChange={(e) => {
            const opp = opportunities.data?.find((o) => o.id === e.target.value);
            setForm({ ...form, opportunity_id: e.target.value, account_id: opp?.account_id ?? form.account_id });
          }}>
            <option value="">No deal</option>
            {(opportunities.data ?? []).map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Account">
          <select className="ui-select" value={form.account_id} onChange={set("account_id")}>
            <option value="">No account</option>
            {(accounts.data ?? []).map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Project owner">
          <select className="ui-select" value={form.owner_user_id} onChange={set("owner_user_id")}>
            <option value="">Me</option>
            {(members.data ?? []).map((m) => (
              <option key={m.id} value={m.id}>
                {m.full_name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Budget">
          <input className="ui-input" type="number" min={0} step="0.01" value={form.budget} onChange={set("budget")} />
        </Field>
        <Field label="Start">
          <input className="ui-input" type="date" value={form.start_date} onChange={set("start_date")} />
        </Field>
        <Field label="Due">
          <input className="ui-input" type="date" value={form.due_date} onChange={set("due_date")} />
        </Field>
        <div className="ui-field span-2">
          <span className="ui-label">Team</span>
          <div className="check-grid">
            {(members.data ?? []).map((m) => (
              <label key={m.id} className="ui-check">
                <input type="checkbox" checked={team.includes(m.id)} onChange={() => setTeam(team.includes(m.id) ? team.filter((t) => t !== m.id) : [...team, m.id])} /> {m.full_name}
              </label>
            ))}
          </div>
        </div>
        {!project ? (
          <Field label="Milestones" hint="One per line. You can change them later." className="span-2">
            <textarea className="ui-textarea" rows={3} value={milestones} onChange={(e) => setMilestones(e.target.value)} />
          </Field>
        ) : null}
        <Field label="Description" className="span-2">
          <textarea className="ui-textarea" rows={3} value={form.description} onChange={set("description")} />
        </Field>
      </div>
    </Modal>
  );
}
