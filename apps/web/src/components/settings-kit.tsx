"use client";

import { Check, KeyRound, RotateCcw, Save, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";

import { useToast } from "@/components/providers";
import { Alert, LoadingRows } from "@/components/ui";
import type { OrganizationMember } from "@/types/organization-member";
import { api, errorMessage, useApi } from "@/lib/use-api";

/**
 * Building blocks for settings pages: a hook that loads and saves one
 * workspace settings section, and the controls those pages share.
 */

export type SettingsResponse<T> = {
  namespace: string;
  data: T;
  /** Which secret keys have a stored value (values are never returned). */
  secrets: Record<string, boolean>;
  can_edit: boolean;
};

function same(a: unknown, b: unknown) {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function useWorkspaceSettings<T extends Record<string, unknown>>(namespace: string) {
  const toast = useToast();
  const loaded = useApi<SettingsResponse<T>>(`/workspace-settings/${namespace}`);
  const [draft, setDraft] = useState<T | null>(null);
  const [secrets, setSecrets] = useState<Record<string, string | null>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (loaded.data) setDraft(loaded.data.data);
  }, [loaded.data]);

  const dirty = Boolean(draft && loaded.data && (!same(draft, loaded.data.data) || Object.keys(secrets).length));

  const set = useCallback(<K extends keyof T>(key: K, value: T[K]) => {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  }, []);

  /** A string replaces the stored secret, null removes it, undefined drops the pending change. */
  const setSecret = useCallback((key: string, value: string | null | undefined) => {
    setSecrets((current) => {
      const next = { ...current };
      if (value === undefined || value === "") delete next[key];
      else next[key] = value;
      return next;
    });
  }, []);

  async function save(overrides?: Partial<T>, message = "Settings saved.") {
    if (!draft) return false;
    setSaving(true);
    setSaveError(null);
    try {
      const body = { data: { ...draft, ...(overrides ?? {}) }, secrets };
      const result = await api.put<SettingsResponse<T>>(`/workspace-settings/${namespace}`, body);
      loaded.setData(result);
      setDraft(result.data);
      setSecrets({});
      toast(message);
      return true;
    } catch (caught) {
      const text = errorMessage(caught);
      setSaveError(text);
      toast(text, "danger");
      return false;
    } finally {
      setSaving(false);
    }
  }

  function reset() {
    if (loaded.data) setDraft(loaded.data.data);
    setSecrets({});
    setSaveError(null);
  }

  return {
    draft,
    saved: loaded.data?.data ?? null,
    secretsSet: loaded.data?.secrets ?? {},
    pendingSecrets: secrets,
    canEdit: loaded.data?.can_edit ?? false,
    loading: loaded.loading && !loaded.data,
    error: loaded.error,
    saveError,
    dirty,
    saving,
    set,
    setDraft,
    setSecret,
    save,
    reset,
    reload: loaded.reload,
  };
}

export type SettingsState<T extends Record<string, unknown>> = ReturnType<typeof useWorkspaceSettings<T>>;

/** Shows loading and load errors, then renders the form once the draft is ready. */
export function SettingsBody<T extends Record<string, unknown>>({
  state,
  children,
}: {
  state: SettingsState<T>;
  children: (draft: T) => ReactNode;
}) {
  if (state.error && !state.draft) return <Alert>{state.error}</Alert>;
  if (!state.draft)
    return (
      <div className="ui-card">
        <LoadingRows rows={6} />
      </div>
    );
  return (
    <>
      {!state.canEdit ? <Alert tone="info">You can see these settings. Ask a workspace admin to change them.</Alert> : null}
      {children(state.draft)}
      <SaveBar state={state} />
    </>
  );
}

/** Sticky bar that appears when there are unsaved changes. */
export function SaveBar<T extends Record<string, unknown>>({ state }: { state: SettingsState<T> }) {
  if (!state.dirty || !state.canEdit) return null;
  return (
    <div className="settings-savebar" role="region" aria-label="Unsaved changes">
      <span>You have unsaved changes.</span>
      <div className="ui-actions">
        <button type="button" className="ui-btn" onClick={state.reset} disabled={state.saving}>
          <RotateCcw size={15} /> Discard
        </button>
        <button type="button" className="ui-btn ui-btn-primary" onClick={() => void state.save()} disabled={state.saving}>
          {state.saving ? <span className="ui-spinner" /> : <Save size={15} />} Save changes
        </button>
      </div>
    </div>
  );
}

/** One labelled setting: label and explanation on the left, the control on the right. */
export function SettingRow({ label, hint, children, stacked }: { label: ReactNode; hint?: ReactNode; children: ReactNode; stacked?: boolean }) {
  return (
    <div className={`settings-row${stacked ? " stacked" : ""}`}>
      <div className="settings-row-text">
        <div className="settings-row-label">{label}</div>
        {hint ? <div className="settings-row-hint">{hint}</div> : null}
      </div>
      <div className="settings-row-control">{children}</div>
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <label className="ui-switch" title={checked ? "On" : "Off"}>
      <input type="checkbox" role="switch" aria-label={label} checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
      <span />
    </label>
  );
}

export function NumberInput({
  value,
  onChange,
  min,
  max,
  step,
  disabled,
  suffix,
  label,
  allowEmpty,
  width = 120,
}: {
  value: number | null | undefined;
  onChange: (value: number | null) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  suffix?: string;
  label: string;
  allowEmpty?: boolean;
  width?: number;
}) {
  return (
    <span className="settings-number">
      <input
        className="ui-input sm"
        type="number"
        aria-label={label}
        style={{ width }}
        value={value ?? ""}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        placeholder={allowEmpty ? "No limit" : undefined}
        onChange={(event) => {
          const raw = event.target.value;
          if (raw === "") return onChange(allowEmpty ? null : (min ?? 0));
          const parsed = Number(raw);
          if (!Number.isNaN(parsed)) onChange(parsed);
        }}
      />
      {suffix ? <span className="ui-muted ui-small">{suffix}</span> : null}
    </span>
  );
}

/** A list of short values entered one at a time (Enter or comma adds, × removes). */
export function TagsInput({
  value,
  onChange,
  placeholder,
  disabled,
  label,
  transform,
}: {
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
  label: string;
  transform?: (value: string) => string;
}) {
  const [text, setText] = useState("");

  function add(raw: string) {
    const parts = raw
      .split(/[,\n]/)
      .map((part) => (transform ? transform(part.trim()) : part.trim()))
      .filter(Boolean);
    if (!parts.length) return;
    const next = [...value];
    for (const part of parts) if (!next.some((existing) => existing.toLowerCase() === part.toLowerCase())) next.push(part);
    onChange(next);
    setText("");
  }

  function onKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      add(text);
    } else if (event.key === "Backspace" && !text && value.length) {
      onChange(value.slice(0, -1));
    }
  }

  return (
    <div className={`settings-tags${disabled ? " disabled" : ""}`}>
      {value.map((item) => (
        <span key={item} className="settings-tag">
          {item}
          {!disabled ? (
            <button type="button" aria-label={`Remove ${item}`} onClick={() => onChange(value.filter((v) => v !== item))}>
              <X size={12} />
            </button>
          ) : null}
        </span>
      ))}
      {!disabled ? (
        <input
          value={text}
          aria-label={label}
          placeholder={value.length ? "" : placeholder}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKey}
          onBlur={() => add(text)}
          onPaste={(event) => {
            const pasted = event.clipboardData.getData("text");
            if (/[,\n]/.test(pasted)) {
              event.preventDefault();
              add(pasted);
            }
          }}
        />
      ) : null}
    </div>
  );
}

/** An API key or token: never shown once saved; can be replaced or removed. */
export function SecretInput({
  name,
  isSet,
  pending,
  onChange,
  disabled,
  placeholder,
  label,
}: {
  name: string;
  isSet: boolean;
  pending: string | null | undefined;
  onChange: (key: string, value: string | null | undefined) => void;
  disabled?: boolean;
  placeholder?: string;
  label: string;
}) {
  const [editing, setEditing] = useState(false);
  const removing = pending === null;
  if (isSet && !editing && pending === undefined) {
    return (
      <span className="ui-row" style={{ gap: 8 }}>
        <span className="settings-secret-set">
          <KeyRound size={13} /> Saved
        </span>
        {!disabled ? (
          <>
            <button type="button" className="ui-btn ui-btn-sm" onClick={() => setEditing(true)}>
              Replace
            </button>
            <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" onClick={() => onChange(name, null)}>
              Remove
            </button>
          </>
        ) : null}
      </span>
    );
  }
  if (removing) {
    return (
      <span className="ui-row" style={{ gap: 8 }}>
        <span className="ui-muted ui-small">Will be removed when you save.</span>
        <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" onClick={() => onChange(name, undefined)}>
          Undo
        </button>
      </span>
    );
  }
  return (
    <input
      className="ui-input sm"
      type="password"
      autoComplete="off"
      aria-label={label}
      disabled={disabled}
      placeholder={placeholder ?? "Paste the key"}
      value={pending ?? ""}
      onChange={(event) => onChange(name, event.target.value)}
      style={{ maxWidth: 360 }}
    />
  );
}

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Monday = 0, matching the API. */
export function DaysPicker({ value, onChange, disabled }: { value: number[]; onChange: (value: number[]) => void; disabled?: boolean }) {
  return (
    <div className="settings-days" role="group" aria-label="Days">
      {WEEKDAYS.map((day, index) => {
        const on = value.includes(index);
        return (
          <button
            key={day}
            type="button"
            className={`settings-day${on ? " on" : ""}`}
            aria-pressed={on}
            disabled={disabled}
            onClick={() => onChange(on ? value.filter((d) => d !== index) : [...value, index].sort())}
          >
            {on ? <Check size={12} /> : null}
            {day}
          </button>
        );
      })}
    </div>
  );
}

export function hourLabel(hour: number) {
  if (hour === 0 || hour === 24) return "12:00 AM";
  if (hour === 12) return "12:00 PM";
  return hour < 12 ? `${hour}:00 AM` : `${hour - 12}:00 PM`;
}

export function HourSelect({
  value,
  onChange,
  disabled,
  label,
  from = 0,
  to = 23,
}: {
  value: number;
  onChange: (value: number) => void;
  disabled?: boolean;
  label: string;
  from?: number;
  to?: number;
}) {
  const hours = Array.from({ length: to - from + 1 }, (_, i) => from + i);
  return (
    <select className="ui-select sm" aria-label={label} value={value} disabled={disabled} onChange={(event) => onChange(Number(event.target.value))} style={{ width: 140 }}>
      {hours.map((hour) => (
        <option key={hour} value={hour}>
          {hour === 24 ? "Midnight" : hourLabel(hour)}
        </option>
      ))}
    </select>
  );
}

export const TIMEZONES = [
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Phoenix",
  "America/Los_Angeles",
  "America/Anchorage",
  "Pacific/Honolulu",
  "America/Toronto",
  "America/Mexico_City",
  "America/Sao_Paulo",
  "Europe/London",
  "Europe/Dublin",
  "Europe/Paris",
  "Europe/Berlin",
  "Europe/Madrid",
  "Europe/Amsterdam",
  "Africa/Johannesburg",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
  "Pacific/Auckland",
  "UTC",
];

/** Workspace members for pickers (active people first). */
export function useMembers() {
  const members = useApi<OrganizationMember[]>("/organization-members");
  const list = useMemo(() => [...(members.data ?? [])].sort((a, b) => Number(b.is_active) - Number(a.is_active) || a.full_name.localeCompare(b.full_name)), [members.data]);
  const names = useMemo(() => Object.fromEntries(list.map((m) => [m.user_id, m.full_name])), [list]);
  return { members: list, names, loading: members.loading };
}

/** Checkbox list of people. */
export function MemberPicker({
  value,
  onChange,
  disabled,
  label = "People",
}: {
  value: string[];
  onChange: (value: string[]) => void;
  disabled?: boolean;
  label?: string;
}) {
  const { members } = useMembers();
  const [filter, setFilter] = useState("");
  const shown = members.filter((m) => m.is_active && (!filter || `${m.full_name} ${m.email}`.toLowerCase().includes(filter.toLowerCase())));
  return (
    <div className="settings-member-picker" role="group" aria-label={label}>
      {members.length > 8 ? (
        <input className="ui-input sm" placeholder="Find a person" aria-label="Find a person" value={filter} onChange={(event) => setFilter(event.target.value)} />
      ) : null}
      <div className="settings-member-list">
        {shown.map((member) => (
          <label key={member.user_id} className="ui-check">
            <input
              type="checkbox"
              disabled={disabled}
              checked={value.includes(member.user_id)}
              onChange={(event) => onChange(event.target.checked ? [...value, member.user_id] : value.filter((id) => id !== member.user_id))}
            />
            <span>
              {member.full_name} <span className="ui-muted ui-small">{member.email}</span>
            </span>
          </label>
        ))}
        {!shown.length ? <span className="ui-muted ui-small">No one matches.</span> : null}
      </div>
    </div>
  );
}

/** Colored chip choices used by stages, personas, territories and trackers. */
export const COLORS = ["gray", "blue", "green", "teal", "amber", "orange", "red", "pink", "purple"] as const;

export function ColorPicker({ value, onChange, disabled }: { value: string; onChange: (value: string) => void; disabled?: boolean }) {
  return (
    <div className="settings-colors" role="radiogroup" aria-label="Color">
      {COLORS.map((color) => (
        <button
          key={color}
          type="button"
          role="radio"
          aria-checked={value === color}
          aria-label={color}
          disabled={disabled}
          className={`settings-color c-${color}${value === color ? " on" : ""}`}
          onClick={() => onChange(color)}
        />
      ))}
    </div>
  );
}

export function ColorDot({ color }: { color: string | null | undefined }) {
  return <span className={`settings-dot c-${color ?? "gray"}`} aria-hidden />;
}

/** Stat block used on overview and usage pages. */
export function UsageMeter({ used, total, label }: { used: number; total: number; label: string }) {
  const percent = total ? Math.min(100, Math.round((used / total) * 100)) : 0;
  const tone = percent >= 100 ? "danger" : percent >= 80 ? "warning" : "ok";
  return (
    <div className={`settings-meter tone-${tone}`}>
      <div className="settings-meter-top">
        <span>{label}</span>
        <strong>
          {used.toLocaleString()} <span className="ui-muted">of {total.toLocaleString()}</span>
        </strong>
      </div>
      <div className="settings-meter-bar" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
        <div style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}
