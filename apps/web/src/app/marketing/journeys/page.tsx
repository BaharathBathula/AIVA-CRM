"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Plus, Route } from "lucide-react";
import { useState } from "react";

import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Badge, Card, EmptyState, Field, LoadingRows, Modal, PageHeader } from "@/components/ui";
import { relativeTime } from "@/lib/format";
import type { JourneySummary } from "@/lib/marketing";
import { api, errorMessage, useApi } from "@/lib/use-api";

const STATUS_TONE = { draft: "neutral", active: "success", paused: "warning" } as const;

export default function JourneysPage() {
  return (
    <FeaturePage feature="customer_journeys">
      <Journeys />
    </FeaturePage>
  );
}

function Journeys() {
  const { can } = useSession();
  const journeys = useApi<JourneySummary[]>("/journeys");
  const [creating, setCreating] = useState(false);
  return (
    <>
      <PageHeader
        title="Customer journeys"
        description="Multi-step nurture: send emails, wait, create tasks and branch on what people do — automatically for every new lead or contact that matches."
        actions={
          can("marketing.manage") ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setCreating(true)}>
              <Plus size={16} /> New journey
            </button>
          ) : null
        }
      />
      {journeys.error ? <Alert>{journeys.error}</Alert> : null}
      {journeys.loading && !journeys.data ? (
        <Card>
          <LoadingRows />
        </Card>
      ) : journeys.data?.length ? (
        <div className="ui-grid ui-grid-3">
          {journeys.data.map((journey) => (
            <Link key={journey.id} href={`/marketing/journeys/${journey.id}`} className="ui-card" style={{ padding: 20, display: "grid", gap: 10, color: "inherit", textDecoration: "none" }}>
              <div className="ui-row">
                <span className="type-icon">
                  <Route size={15} />
                </span>
                <strong style={{ flex: 1 }}>{journey.name}</strong>
                <Badge tone={STATUS_TONE[journey.status]}>{journey.status}</Badge>
              </div>
              <span className="ui-muted ui-small">{journey.description || `${journey.steps.length} steps for ${journey.object_type}s`}</span>
              <div className="stat-strip" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
                <div className="billing-stat">
                  <span>In journey</span>
                  <strong>{journey.enrollments.active}</strong>
                </div>
                <div className="billing-stat">
                  <span>Completed</span>
                  <strong>{journey.enrollments.completed}</strong>
                </div>
                <div className="billing-stat">
                  <span>Steps</span>
                  <strong>{journey.steps.length}</strong>
                </div>
              </div>
              <span className="ui-muted ui-small">
                {journey.trigger === "created" ? `Starts when a ${journey.object_type} is created` : "People are added by hand"} · updated {relativeTime(journey.updated_at)}
              </span>
            </Link>
          ))}
        </div>
      ) : (
        <Card>
          <EmptyState icon={<Route size={20} />} title="No journeys yet" message="Build a welcome series for new website leads, or a check-in sequence for new customers." />
        </Card>
      )}
      <NewJourney open={creating} onClose={() => setCreating(false)} />
    </>
  );
}

function NewJourney({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const toast = useToast();
  const [name, setName] = useState("");
  const [objectType, setObjectType] = useState("lead");
  const [busy, setBusy] = useState(false);

  async function create() {
    setBusy(true);
    try {
      const journey = await api.post<JourneySummary>("/journeys", {
        name: name.trim(),
        object_type: objectType,
        trigger: "created",
        conditions: [],
        steps: [
          { type: "create_task", params: { title: "Reach out to {{first_name}} {{last_name}}", due_in_days: 1 } },
          { type: "wait", params: { days: 3 } },
          { type: "notify", params: { title: "{{full_name}} is ready for a follow-up" } },
        ],
      });
      router.push(`/marketing/journeys/${journey.id}`);
    } catch (caught) {
      toast(errorMessage(caught), "danger");
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title="New journey"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy || !name.trim()} onClick={() => void create()}>
            Create and edit
          </button>
        </>
      }
    >
      <div className="ui-stack">
        <Field label="Journey name" required>
          <input className="ui-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="New website lead welcome" />
        </Field>
        <Field label="Who goes through it">
          <select className="ui-select" value={objectType} onChange={(e) => setObjectType(e.target.value)}>
            <option value="lead">Leads</option>
            <option value="contact">Contacts</option>
          </select>
        </Field>
      </div>
    </Modal>
  );
}
