/** Display formatting shared by the platform screens. */

const currencyCache = new Map<string, Intl.NumberFormat>();

function currencyFormatter(currency: string, compact: boolean) {
  const key = `${currency}:${compact}`;
  let formatter = currencyCache.get(key);
  if (!formatter) {
    formatter = new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      notation: compact ? "compact" : "standard",
      minimumFractionDigits: 0,
      maximumFractionDigits: compact ? 1 : 0,
    });
    currencyCache.set(key, formatter);
  }
  return formatter;
}

export function money(value: number | string | null | undefined, currency = "USD", compact = false) {
  const amount = Number(value ?? 0);
  if (!Number.isFinite(amount)) return "—";
  return currencyFormatter(currency || "USD", compact).format(amount);
}

export function moneyExact(value: number | string | null | undefined, currency = "USD") {
  const amount = Number(value ?? 0);
  return new Intl.NumberFormat("en-US", { style: "currency", currency: currency || "USD" }).format(
    Number.isFinite(amount) ? amount : 0
  );
}

export function compactMoney(value: number | string | null | undefined, currency = "USD") {
  return money(value, currency, Math.abs(Number(value ?? 0)) >= 10000);
}

export function number(value: number | string | null | undefined, digits = 0) {
  const amount = Number(value ?? 0);
  if (!Number.isFinite(amount)) return "—";
  return amount.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function percent(value: number | null | undefined, digits = 0) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

function toDate(value: string | Date | null | undefined) {
  if (!value) return null;
  const date = typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00`) : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function date(value: string | Date | null | undefined) {
  const parsed = toDate(value);
  return parsed ? parsed.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "—";
}

export function shortDate(value: string | Date | null | undefined) {
  const parsed = toDate(value);
  return parsed ? parsed.toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "—";
}

export function dateTime(value: string | Date | null | undefined) {
  const parsed = toDate(value);
  return parsed
    ? parsed.toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" })
    : "—";
}

export function relativeTime(value: string | Date | null | undefined) {
  const parsed = toDate(value);
  if (!parsed) return "—";
  const seconds = Math.round((parsed.getTime() - Date.now()) / 1000);
  const abs = Math.abs(seconds);
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  if (abs < 45) return "just now";
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(seconds / 86400), "day");
  return date(parsed);
}

/** "2h 15m" style duration from minutes, signed values allowed. */
export function duration(minutes: number) {
  const abs = Math.abs(Math.round(minutes));
  const days = Math.floor(abs / 1440);
  const hours = Math.floor((abs % 1440) / 60);
  const mins = abs % 60;
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

export function initials(name: string | null | undefined) {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() || "?";
}

export function titleCase(value: string | null | undefined) {
  if (!value) return "—";
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function plural(count: number, singular: string, pluralForm = `${singular}s`) {
  return `${number(count)} ${count === 1 ? singular : pluralForm}`;
}

export function greeting(now = new Date()) {
  const hour = now.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

export function todayLabel(now = new Date()) {
  return now.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
}

export function toDateInput(value: string | null | undefined) {
  if (!value) return "";
  return value.slice(0, 10);
}
