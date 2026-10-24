"use client";

import { Phone, PhoneCall, PhoneOff } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useToast } from "@/components/providers";
import { Alert, Field, Modal } from "@/components/ui";
import { api, errorMessage, useApi } from "@/lib/use-api";

export type CallOutcome = { key: string; label: string; connected: boolean; follow_up: boolean };

/** Used until the workspace's dialer settings load (and if they can't be read). */
export const DEFAULT_CALL_OUTCOMES: CallOutcome[] = [
  { key: "connected", label: "Connected", connected: true, follow_up: false },
  { key: "left_voicemail", label: "Left voicemail", connected: false, follow_up: true },
  { key: "no_answer", label: "No answer", connected: false, follow_up: true },
  { key: "busy", label: "Busy", connected: false, follow_up: true },
  { key: "wrong_number", label: "Wrong number", connected: false, follow_up: false },
  { key: "scheduled_callback", label: "Scheduled callback", connected: true, follow_up: true },
];

type DialerSettings = {
  calling_method: "device" | "twilio";
  outcomes: CallOutcome[];
  require_notes: boolean;
  auto_follow_up: boolean;
  follow_up_days: number;
};

function clock(seconds: number) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function inDays(days: number) {
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === 7) return "in a week";
  return `in ${days} days`;
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
 * calling app via tel: links) or through Twilio, times it, and logs the
 * outcome and notes using the workspace's call outcomes (Settings → Dialer).
 */
export function CallDialog({ target, open, onClose, onLogged }: { target: CallTarget; open: boolean; onClose: () => void; onLogged?: () => void }) {
  const toast = useToast();
  const settings = useApi<{ data: DialerSettings }>(open ? "/workspace-settings/dialer" : null);
  const dialer = settings.data?.data;
  const outcomes = dialer?.outcomes?.length ? dialer.outcomes : DEFAULT_CALL_OUTCOMES;
  const [phone, setPhone] = useState(target.phone ?? "");
  const [started, setStarted] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [outcome, setOutcome] = useState("");
  const [notes, setNotes] = useState("");
  /** "" = the workspace default (automatic task or none); a number = a task due in that many days. */
  const [followUp, setFollowUp] = useState("");
  const [busy, setBusy] = useState(false);
  const [dialing, setDialing] = useState(false);
  const [twilioMessage, setTwilioMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (!open) return;
    setPhone(target.phone ?? "");
    setStarted(null);
    setElapsed(0);
    setOutcome("");
    setNotes("");
    setFollowUp("");
    setError(null);
    setTwilioMessage(null);
  }, [open, target.phone]);

  // Default to the first outcome that means "we talked" once the list is known.
  const selected = useMemo(
    () => outcomes.find((item) => item.key === outcome) ?? outcomes.find((item) => item.connected) ?? outcomes[0],
    [outcome, outcomes],
  );

  useEffect(() => {
    if (started === null) return;
    timer.current = window.setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 500);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [started]);

  const automatic = Boolean(dialer?.auto_follow_up && selected?.follow_up);
  const followDays = dialer?.follow_up_days ?? 2;
  const useTwilio = dialer?.calling_method === "twilio";
  const notesRequired = Boolean(dialer?.require_notes);

  function start() {
    setStarted(Date.now());
    if (phone.trim()) window.location.href = `tel:${phone.replace(/[^\d+]/g, "")}`;
  }

  async function callWithTwilio() {
    if (!phone.trim()) {
      setError("Add a phone number to call.");
      return;
    }
    setDialing(true);
    setError(null);
    const record = target.contact_id
      ? { record_type: "contact", record_id: target.contact_id }
      : target.lead_id
        ? { record_type: "lead", record_id: target.lead_id }
        : target.account_id
          ? { record_type: "account", record_id: target.account_id }
          : {};
    try {
      const result = await api.post<{ message: string }>("/dialer/call", { to: phone.trim(), ...record });
      setTwilioMessage(result.message);
      setStarted(Date.now());
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setDialing(false);
    }
  }

  function stop() {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = null;
    setStarted(null);
  }

  async function save() {
    if (notesRequired && !notes.trim()) {
      setError("Your workspace asks for call notes. Add a line about the call.");
      return;
    }
    stop();
    setBusy(true);
    setError(null);
    const days = followUp === "" ? null : Number(followUp);
    try {
      const result = await api.post<{ task_id: string | null }>("/calls", {
        phone: phone || null,
        outcome: selected?.key ?? "connected",
        duration_seconds: elapsed,
        notes: notes || null,
        lead_id: target.lead_id,
        contact_id: target.contact_id,
        account_id: target.account_id,
        opportunity_id: target.opportunity_id,
        follow_up_in_days: days,
      });
      toast(result?.task_id ? "Call logged and follow-up task created." : "Call logged.");
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
      description={useTwilio ? "AIVA rings your phone first, then connects you. When you're done, log how it went." : "The call starts on this device. When you're done, log how it went."}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" disabled={busy} onClick={() => void save()}>
            {busy ? <span className="ui-spinner" /> : null} Log call
          </button>
        </>
      }
    >
      <div className="ui-stack">
        {error ? <Alert>{error}</Alert> : null}
        <div className="call-timer" aria-live="polite">
          {clock(elapsed)}
        </div>
        <div className="call-dialog-actions">
          {started === null ? (
            <>
              {useTwilio ? (
                <button type="button" className="ui-btn ui-btn-primary" disabled={dialing} onClick={() => void callWithTwilio()}>
                  {dialing ? <span className="ui-spinner" /> : <PhoneCall size={16} />} Call with Twilio
                </button>
              ) : null}
              <button type="button" className={`ui-btn${useTwilio ? "" : " ui-btn-primary"}`} onClick={start}>
                <Phone size={16} /> {elapsed ? "Resume timer" : useTwilio ? (phone ? "Call from this device" : "Start timer") : phone ? "Call now" : "Start timer"}
              </button>
            </>
          ) : (
            <button type="button" className="ui-btn ui-btn-danger solid" onClick={stop}>
              <PhoneOff size={16} /> End call
            </button>
          )}
        </div>
        {twilioMessage ? <p className="call-dialog-note">{twilioMessage}</p> : null}
        <Field label="Phone number">
          <input className="ui-input" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+1 214 555 0100" />
        </Field>
        <div className="ui-form-grid">
          <Field label="Outcome">
            <select className="ui-select" value={selected?.key ?? ""} onChange={(e) => setOutcome(e.target.value)}>
              {outcomes.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Follow-up task" hint={automatic && followUp === "" ? `AIVA adds a call-back task due ${inDays(followDays)}.` : undefined}>
            <select className="ui-select" value={followUp} onChange={(e) => setFollowUp(e.target.value)}>
              <option value="">{automatic ? `Automatic (${inDays(followDays)})` : "None"}</option>
              <option value="0">Today</option>
              <option value="1">Tomorrow</option>
              <option value="3">In 3 days</option>
              <option value="7">In a week</option>
            </select>
          </Field>
        </div>
        <Field label="Notes" required={notesRequired}>
          <textarea className="ui-textarea" rows={4} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="What did you talk about?" />
        </Field>
      </div>
    </Modal>
  );
}
