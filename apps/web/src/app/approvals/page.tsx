"use client";

import Link from "next/link";
import { Check, ClipboardCheck, Settings2, Undo2, X } from "lucide-react";
import { useState } from "react";

import { FeaturePage } from "@/components/plan";
import { useSession, useToast } from "@/components/providers";
import { Alert, Avatar, Badge, Card, EmptyState, Field, LoadingRows, Modal, PageHeader, Tabs, type Tone } from "@/components/ui";
import { dateTime, relativeTime } from "@/lib/format";
import { api, errorMessage, useApi } from "@/lib/use-api";

interface ApprovalRequestRow {
  id: string;
  rule_name: string;
  object_type: "opportunity" | "quote";
  record_id: string;
  record_label: string;
  link: string;
  requested_change: Record<string, unknown>;
  status: "pending" | "approved" | "rejected" | "cancelled";
  requested_by: string | null;
  requested_by_user_id: string | null;
  approver: string | null;
  decided_by: string | null;
  decided_at: string | null;
  request_comment: string | null;
  decision_comment: string | null;
  can_decide: boolean;
  created_at: string;
}

const STATUS_TONE: Record<string, Tone> = { pending: "warning", approved: "success", rejected: "danger", cancelled: "neutral" };

function changeLabel(row: ApprovalRequestRow) {
  const change = row.requested_change;
  if (row.object_type === "opportunity" && change.stage_name) return `Move to ${String(change.stage_name)}`;
  if (row.object_type === "quote") return "Send the quote to the customer";
  return "Change";
}

export default function ApprovalsPage() {
  return (
    <FeaturePage feature="approvals">
      <Approvals />
    </FeaturePage>
  );
}

function Approvals() {
  const toast = useToast();
  const { can, session } = useSession();
  const [view, setView] = useState<"inbox" | "mine" | "history">("inbox");
  const approvals = useApi<{ items: ApprovalRequestRow[]; counts: { inbox: number; mine: number } }>(`/approvals?view=${view}`);
  const [deciding, setDeciding] = useState<{ row: ApprovalRequestRow; approve: boolean } | null>(null);

  async function withdraw(row: ApprovalRequestRow) {
    try {
      await api.post(`/approvals/${row.id}/cancel`);
      toast("Request withdrawn.");
      await approvals.reload();
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    }
  }

  return (
    <>
      <PageHeader
        title="Approvals"
        description="Deals moving into protected stages and quotes over your discount limits wait here until the right person approves them."
        actions={
          can("admin.settings") ? (
            <Link href="/settings/approvals" className="ui-btn">
              <Settings2 size={15} /> Approval rules
            </Link>
          ) : null
        }
      />
      {approvals.error ? <Alert>{approvals.error}</Alert> : null}
      <Card
        flush
        title={
          <Tabs
            label="Approvals"
            value={view}
            onChange={setView}
            items={[
              { value: "inbox", label: "Waiting on me", count: approvals.data?.counts.inbox },
              { value: "mine", label: "My requests", count: approvals.data?.counts.mine },
              { value: "history", label: "History" },
            ]}
          />
        }
      >
        {approvals.loading && !approvals.data ? (
          <LoadingRows />
        ) : approvals.data?.items.length ? (
          <ul className="approval-list">
            {approvals.data.items.map((row) => (
              <li key={row.id} className="approval-row">
                <Avatar name={row.requested_by} size={34} />
                <div className="approval-body">
                  <div className="ui-row" style={{ gap: 8, flexWrap: "wrap" }}>
                    <Link href={row.link} className="approval-title">
                      {row.record_label}
                    </Link>
                    <Badge tone={STATUS_TONE[row.status]}>{row.status}</Badge>
                    <span className="ui-muted ui-small">{row.rule_name}</span>
                  </div>
                  <div className="ui-small">
                    <strong>{changeLabel(row)}</strong> · asked by {row.requested_by ?? "someone"} {relativeTime(row.created_at)} · approver {row.approver ?? "—"}
                  </div>
                  {row.request_comment ? <p className="approval-quote">“{row.request_comment}”</p> : null}
                  {row.status !== "pending" && row.decided_at ? (
                    <div className="ui-muted ui-small">
                      {row.status === "cancelled" ? "Withdrawn" : `${row.status === "approved" ? "Approved" : "Rejected"} by ${row.decided_by ?? "—"}`} on {dateTime(row.decided_at)}
                      {row.decision_comment ? ` — “${row.decision_comment}”` : ""}
                    </div>
                  ) : null}
                </div>
                <div className="approval-actions">
                  {row.status === "pending" && row.can_decide ? (
                    <>
                      <button type="button" className="ui-btn ui-btn-sm" onClick={() => setDeciding({ row, approve: false })}>
                        <X size={14} /> Reject
                      </button>
                      <button type="button" className="ui-btn ui-btn-primary ui-btn-sm" onClick={() => setDeciding({ row, approve: true })}>
                        <Check size={14} /> Approve
                      </button>
                    </>
                  ) : null}
                  {row.status === "pending" && row.requested_by_user_id === session?.user.id ? (
                    <button type="button" className="ui-btn ui-btn-ghost ui-btn-sm" onClick={() => void withdraw(row)}>
                      <Undo2 size={14} /> Withdraw
                    </button>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            icon={<ClipboardCheck size={20} />}
            title={view === "inbox" ? "Nothing waiting on you" : view === "mine" ? "No pending requests" : "No decisions yet"}
            message={view === "inbox" ? "When someone needs your sign-off it shows up here and in your notifications." : "Requests appear here once they're submitted."}
          />
        )}
      </Card>
      <DecideModal
        target={deciding}
        onClose={() => setDeciding(null)}
        onDone={(message) => {
          toast(message);
          setDeciding(null);
          void approvals.reload();
        }}
      />
    </>
  );
}

function DecideModal({ target, onClose, onDone }: { target: { row: ApprovalRequestRow; approve: boolean } | null; onClose: () => void; onDone: (message: string) => void }) {
  const toast = useToast();
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);

  async function decide() {
    if (!target) return;
    setBusy(true);
    try {
      const result = await api.post<{ message: string }>(`/approvals/${target.row.id}/decide`, { approve: target.approve, comment: comment.trim() || null });
      setComment("");
      onDone(result.message || (target.approve ? "Approved." : "Rejected."));
    } catch (caught) {
      toast(errorMessage(caught), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={Boolean(target)}
      title={target?.approve ? "Approve request" : "Reject request"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className={`ui-btn ${target?.approve ? "ui-btn-primary" : "ui-btn-danger"}`} disabled={busy} onClick={() => void decide()}>
            {target?.approve ? "Approve" : "Reject"}
          </button>
        </>
      }
    >
      {target ? (
        <div className="ui-stack">
          <p style={{ margin: 0 }}>
            <strong>{target.row.record_label}</strong> — {changeLabel(target.row)}.
            {target.approve ? " The change is applied as soon as you approve." : " The record stays as it is and the requester is told why."}
          </p>
          <Field label={target.approve ? "Comment (optional)" : "Reason"}>
            <textarea className="ui-textarea" rows={3} value={comment} onChange={(e) => setComment(e.target.value)} />
          </Field>
        </div>
      ) : null}
    </Modal>
  );
}
