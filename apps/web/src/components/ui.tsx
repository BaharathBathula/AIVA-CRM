"use client";

import Link from "next/link";
import { AlertCircle, Inbox, Lock, X } from "lucide-react";
import { Children, cloneElement, isValidElement, useEffect, useId, useRef, type ReactElement, type ReactNode } from "react";

import { initials } from "@/lib/format";

// ---------------------------------------------------------------- Page header

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="ui-header">
      <div style={{ minWidth: 0 }}>
        {eyebrow ? <div className="ui-eyebrow">{eyebrow}</div> : null}
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="ui-actions">{actions}</div> : null}
    </div>
  );
}

// ---------------------------------------------------------------- Card

export function Card({
  title,
  icon,
  actions,
  children,
  footer,
  flush,
  className,
}: {
  title?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  flush?: boolean;
  className?: string;
}) {
  return (
    <section className={`ui-card${className ? ` ${className}` : ""}`}>
      {title || actions ? (
        <div className="ui-card-header">
          {title ? (
            <h2>
              {icon}
              {title}
            </h2>
          ) : (
            <span />
          )}
          {actions ? <div className="ui-actions">{actions}</div> : null}
        </div>
      ) : null}
      <div className={`ui-card-body${flush ? " flush" : ""}`}>{children}</div>
      {footer ? <div className="ui-card-footer">{footer}</div> : null}
    </section>
  );
}

// ---------------------------------------------------------------- KPI

export function Kpi({
  label,
  value,
  note,
  tone,
  href,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  tone?: "danger" | "success";
  href?: string;
}) {
  const body = (
    <>
      <div className="ui-kpi-label">{label}</div>
      <div className="ui-kpi-value">{value}</div>
      {note ? <div className="ui-kpi-note">{note}</div> : null}
    </>
  );
  const className = `ui-kpi${tone ? ` tone-${tone}` : ""}`;
  return href ? (
    <Link href={href} className={className}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}

// ---------------------------------------------------------------- Badges

export type Tone = "neutral" | "primary" | "success" | "warning" | "danger" | "info";

export function Badge({ tone = "neutral", children, dot }: { tone?: Tone; children: ReactNode; dot?: boolean }) {
  return (
    <span className={`ui-badge tone-${tone}`}>
      {dot ? <span className="ui-dot" /> : null}
      {children}
    </span>
  );
}

export function Avatar({ name, size = 28 }: { name: string | null | undefined; size?: number }) {
  return (
    <span className="ui-avatar" style={{ width: size, height: size, fontSize: Math.max(10, size * 0.38) }} aria-hidden>
      {initials(name)}
    </span>
  );
}

export const PRIORITY_TONE: Record<string, Tone> = {
  low: "neutral",
  medium: "info",
  high: "warning",
  urgent: "danger",
};

export const CASE_STATUS_TONE: Record<string, Tone> = {
  new: "primary",
  open: "info",
  pending: "warning",
  on_hold: "neutral",
  resolved: "success",
  closed: "neutral",
};

export const QUOTE_STATUS_TONE: Record<string, Tone> = {
  draft: "neutral",
  sent: "info",
  accepted: "success",
  declined: "danger",
  expired: "warning",
};

export const SLA_TONE: Record<string, Tone> = {
  on_track: "success",
  at_risk: "warning",
  breached: "danger",
  met: "success",
  missed: "danger",
  none: "neutral",
};

// ---------------------------------------------------------------- Form fields

export function Field({
  label,
  hint,
  error,
  required,
  children,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  required?: boolean;
  children: ReactNode;
  className?: string;
}) {
  const autoId = useId();
  const noteId = `${autoId}-note`;
  const only = Children.count(children) === 1 && isValidElement(children) ? (children as ReactElement<Record<string, unknown>>) : null;
  // Tie the label (and hint) to a single native control so it has an accessible name.
  const native = only && typeof only.type === "string" && ["input", "select", "textarea"].includes(only.type);
  const id = native ? ((only.props.id as string | undefined) ?? autoId) : undefined;
  const control = native
    ? cloneElement(only, {
        id,
        "aria-describedby": hint || error ? noteId : undefined,
        "aria-invalid": error ? true : only.props["aria-invalid"],
      })
    : children;

  return (
    <div className={`ui-field${className ? ` ${className}` : ""}`}>
      <label htmlFor={id}>
        {label}
        {required ? <span className="ui-required"> *</span> : null}
      </label>
      {control}
      {error ? (
        <span className="error" id={noteId}>
          {error}
        </span>
      ) : hint ? (
        <span className="hint" id={noteId}>
          {hint}
        </span>
      ) : null}
    </div>
  );
}

export function Alert({ tone = "danger", children }: { tone?: "danger" | "info" | "success" | "warning"; children: ReactNode }) {
  return (
    <div className={`ui-alert tone-${tone}`} role={tone === "danger" ? "alert" : "status"}>
      <AlertCircle size={16} style={{ flex: "none", marginTop: 1 }} />
      <div>{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------- Modal

export function Modal({
  open,
  title,
  description,
  onClose,
  children,
  footer,
  size,
}: {
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "lg";
}) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const node = ref.current;
    const focusable = node?.querySelector<HTMLElement>("input, select, textarea, button:not(.ui-modal-close)");
    focusable?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="ui-modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div ref={ref} className={`ui-modal${size ? ` ${size}` : ""}`} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="ui-modal-header">
          <div>
            <h2 id={titleId}>{title}</h2>
            {description ? <p>{description}</p> : null}
          </div>
          <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon ui-btn-sm ui-modal-close" onClick={onClose} aria-label="Close">
            <X size={17} />
          </button>
        </div>
        <div className="ui-modal-body">{children}</div>
        {footer ? <div className="ui-modal-footer">{footer}</div> : null}
      </div>
    </div>
  );
}

/** In-page confirmation (the browser's confirm() dialog is avoided on purpose). */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Delete",
  danger = true,
  busy,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Modal
      open={open}
      title={title}
      onClose={onClose}
      size="sm"
      footer={
        <>
          <button type="button" className="ui-btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className={`ui-btn ${danger ? "ui-btn-danger solid" : "ui-btn-primary"}`}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? <span className="ui-spinner" /> : null}
            {confirmLabel}
          </button>
        </>
      }
    >
      <div style={{ fontSize: 13.5, color: "var(--text-2)", lineHeight: 1.55 }}>{message}</div>
    </Modal>
  );
}

// ---------------------------------------------------------------- States

export function EmptyState({
  icon,
  title,
  message,
  action,
}: {
  icon?: ReactNode;
  title: string;
  message?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="ui-empty">
      <div className="icon">{icon ?? <Inbox size={20} />}</div>
      <h3>{title}</h3>
      {message ? <p>{message}</p> : null}
      {action ? <div style={{ marginTop: 6 }}>{action}</div> : null}
    </div>
  );
}

export function Skeleton({ height = 16, width = "100%", style }: { height?: number; width?: number | string; style?: React.CSSProperties }) {
  return <div className="ui-skeleton" style={{ height, width, ...style }} />;
}

export function LoadingRows({ rows = 6 }: { rows?: number }) {
  return (
    <div style={{ display: "grid", gap: 12, padding: 18 }}>
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} height={18} width={`${92 - (index % 3) * 14}%`} />
      ))}
    </div>
  );
}

export function NoAccess({ what = "this page" }: { what?: string }) {
  return (
    <div className="ui-card">
      <EmptyState
        icon={<Lock size={20} />}
        title="You don't have access"
        message={`Your role doesn't include permission to view ${what}. Ask a workspace admin if you need it.`}
      />
    </div>
  );
}

// ---------------------------------------------------------------- Tabs

export function Tabs<T extends string>({
  value,
  onChange,
  items,
  label,
}: {
  value: T;
  onChange: (value: T) => void;
  items: { value: T; label: ReactNode; count?: number; hot?: boolean }[];
  label: string;
}) {
  return (
    <div className="ui-tabs" role="tablist" aria-label={label}>
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          role="tab"
          className="ui-tab"
          aria-selected={value === item.value}
          onClick={() => onChange(item.value)}
        >
          {item.label}
          {item.count !== undefined ? <span className={`count${item.hot && item.count ? " hot" : ""}`}>{item.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function Progress({ value, tone }: { value: number; tone?: "success" }) {
  const width = Math.max(0, Math.min(100, value));
  return (
    <div className={`ui-progress${tone ? ` ${tone}` : ""}`} role="progressbar" aria-valuenow={Math.round(width)} aria-valuemin={0} aria-valuemax={100}>
      <div style={{ width: `${width}%` }} />
    </div>
  );
}
