"use client";

import { Phone, PhoneOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useToast } from "@/components/providers";
import { Alert, Field, Modal } from "@/components/ui";
import { api, errorMessage } from "@/lib/use-api";

const OUTCOMES: { value: string; label: string }[] = [
  { value: "connected", label: "Connected" },
  { value: "left_voicemail", label: "Left voicemail" },
  { value: "no_answer", label: "No answer" },
  { value: "busy", label: "Busy" },
  { value: "scheduled_callback", label: "Scheduled a callback" },
  { value: "wrong_number", label: "Wrong number" },
];

function clock(seconds: number) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export type CallTarget = {
  name: string;
  phone: string | null;
  lead_id?: string;
  contact_id?: string;
  account_id?: string;
  opportunity_id?: string;
};

/**
 * Built-in dialer: starts the call on this device (phone, softphone or
 * calling app via tel: links), times it, and logs the outcome and notes.
 */
export function CallDialog({ target, open, onClose, onLogged }: { target: CallTarget; open: boolean; onClose: () => void; onLogged?: () => void }) {
  const toast = useToast();
  const [phone, setPhone] = useState(target.phone ?? "");
  const [started, setStarted] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [outcome, setOutcome] = useState("connected");
  const [notes, setNotes] = useState("");
  const [followUp, setFollowUp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (!open) return;
    setPhone(target.phone ?? "");
    setStarted(null);
    setElapsed(0);
    setOutcome("connected");
    setNotes("");
    setFollowUp("");
    setError(null);
  }, [open, target.phone]);

  useEffect(() => {
    if (started === null) return;
    timer.current = window.setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 500);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [started]);

  function start() {
    setStarted(Date.now());
    if (phone.trim()) window.location.href = `tel:${phone.replace(/[^\d+]/g, "")}`;
  }

  function stop() {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = null;
    setStarted(null);
  }

  async function save() {
    stop();
    setBusy(true);
    setError(null);
    try {
      await api.post("/calls", {
        phone: phone || null,
        outcome,
        duration_seconds: elapsed,
        notes: notes || null,
        lead_id: target.lead_id,
        contact_id: target.contact_id,
        account_id: target.account_id,
        opportunity_id: target.opportunity_id,
        follow_up_in_days: followUp === "" ? null : Number(followUp),
      });
      toast(followUp !== "" ? "Call logged and follow-up task created." : "Call logged.");
      onLogged?.();
      onClose();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title={`Call ${target.name}`}
      description="The call starts on this device. When you're done, log how it went."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void save()}>
            Log call
          </button>
        </>
      }
    >
      <div className="ui-stack">
        {error ? <Alert>{error}</Alert> : null}
        <div className="call-timer" aria-live="polite">
          {clock(elapsed)}
        </div>
        <div className="ui-row" style={{ justifyContent: "center", gap: 10 }}>
          {started === null ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={start}>
              <Phone size={16} /> {elapsed ? "Resume timer" : phone ? "Call now" : "Start timer"}
            </button>
          ) : (
            <button type="button" className="ui-btn ui-btn-danger solid" onClick={stop}>
              <PhoneOff size={16} /> End call
            </button>
          )}
        </div>
        <Field label="Phone number">
          <input className="ui-input" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+1 214 555 0100" />
        </Field>
        <div className="ui-form-grid">
          <Field label="Outcome">
            <select className="ui-select" value={outcome} onChange={(e) => setOutcome(e.target.value)}>
              {OUTCOMES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Follow-up task">
            <select className="ui-select" value={followUp} onChange={(e) => setFollowUp(e.target.value)}>
              <option value="">None</option>
              <option value="0">Today</option>
              <option value="1">Tomorrow</option>
              <option value="3">In 3 days</option>
              <option value="7">In a week</option>
            </select>
          </Field>
        </div>
        <Field label="Notes">
          <textarea className="ui-textarea" rows={4} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="What did you talk about?" />
        </Field>
      </div>
    </Modal>
  );
}
