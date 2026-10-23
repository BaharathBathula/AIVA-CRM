/**
 * Types and labels for orders, purchasing, meetings, documents, services,
 * projects, social, website visits, pins and team chat.
 */

import type { Tone } from "@/components/ui";

export interface Address {
  street?: string | null;
  city?: string | null;
  state?: string | null;
  postal_code?: string | null;
  country?: string | null;
}

export interface OrderLine {
  product_id: string | null;
  name: string;
  description: string | null;
  sku?: string | null;
  quantity: number;
  unit_price: number;
  discount_percent: number;
  tax_percent: number;
  line_total?: number;
  received_quantity?: number;
}

// ---------------------------------------------------------------- Vendors

export interface Vendor {
  id: string;
  name: string;
  category: string | null;
  contact_name: string | null;
  email: string | null;
  phone: string | null;
  website: string | null;
  gl_account: string | null;
  tax_id: string | null;
  payment_terms: string | null;
  currency: string;
  street: string | null;
  city: string | null;
  state: string | null;
  postal_code: string | null;
  country: string | null;
  notes: string | null;
  is_active: boolean;
  owner_name: string | null;
  open_orders?: number;
  order_count?: number;
  total_spend?: number;
  created_at: string;
}

// ---------------------------------------------------------------- Price books

export interface PriceTier {
  from_qty: number;
  to_qty: number | null;
  discount_percent: number;
}

export interface PriceBookEntry {
  id: string;
  product_id: string;
  product_name: string;
  sku: string | null;
  standard_price: number;
  list_price: number;
  tiers: PriceTier[];
}

export interface PriceBook {
  id: string;
  name: string;
  description: string | null;
  currency: string;
  pricing_model: "flat" | "differential";
  valid_from: string | null;
  valid_until: string | null;
  is_active: boolean;
  is_current: boolean;
  entry_count: number;
  entries?: PriceBookEntry[];
  can_manage?: boolean;
  updated_at: string;
}

/** Price for a product from a price book (list price and the volume discount for a quantity). */
export function bookPrice(entries: PriceBookEntry[] | undefined, productId: string | null, quantity: number) {
  const entry = entries?.find((e) => e.product_id === productId);
  if (!entry) return null;
  const tier = entry.tiers.find((t) => quantity >= t.from_qty && (t.to_qty === null || quantity <= t.to_qty));
  return { unit_price: entry.list_price, discount_percent: tier?.discount_percent ?? 0 };
}

// ---------------------------------------------------------------- Orders

export type SalesOrderStatus = "draft" | "confirmed" | "fulfilled" | "cancelled";
export type PurchaseOrderStatus = "draft" | "issued" | "partially_received" | "received" | "cancelled";

export const SO_STATUS: Record<SalesOrderStatus, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "neutral" },
  confirmed: { label: "Confirmed", tone: "info" },
  fulfilled: { label: "Fulfilled", tone: "success" },
  cancelled: { label: "Cancelled", tone: "danger" },
};

export const PO_STATUS: Record<PurchaseOrderStatus, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "neutral" },
  issued: { label: "Issued", tone: "info" },
  partially_received: { label: "Partly received", tone: "warning" },
  received: { label: "Received", tone: "success" },
  cancelled: { label: "Cancelled", tone: "danger" },
};

interface OrderBase {
  id: string;
  number: number;
  display_number: string;
  subject: string;
  owner_user_id: string | null;
  owner_name: string | null;
  order_date: string;
  currency: string;
  subtotal: number;
  discount_total: number;
  tax_total: number;
  shipping: number;
  total: number;
  line_count: number;
  created_at: string;
  updated_at: string;
  line_items?: OrderLine[];
  terms?: string | null;
  notes?: string | null;
  organization_name?: string;
  can_manage?: boolean;
}

export interface SalesOrder extends OrderBase {
  status: SalesOrderStatus;
  is_overdue: boolean;
  quote_id: string | null;
  account_id: string | null;
  account_name: string | null;
  contact_id: string | null;
  contact_name: string | null;
  opportunity_id: string | null;
  opportunity_name: string | null;
  price_book_id: string | null;
  due_date: string | null;
  customer_po: string | null;
  carrier: string | null;
  tracking_number: string | null;
  stock_applied: boolean;
  confirmed_at: string | null;
  fulfilled_at: string | null;
  cancelled_at: string | null;
  billing_address?: Address;
  shipping_address?: Address;
  quote?: { id: string; display_number: string; name: string } | null;
  invoices?: { id: string; display_number: string; status: string; total: number }[];
  purchase_orders?: { id: string; display_number: string; status: string; total: number }[];
  stock_warnings?: string[];
}

export interface PurchaseOrder extends OrderBase {
  status: PurchaseOrderStatus;
  is_late: boolean;
  vendor_id: string | null;
  vendor_name: string | null;
  sales_order_id: string | null;
  expected_date: string | null;
  vendor_reference: string | null;
  received_percent: number;
  issued_at: string | null;
  received_at: string | null;
  cancelled_at: string | null;
  ship_to?: Address;
  vendor?: Vendor | null;
  sales_order?: { id: string; display_number: string; subject: string } | null;
}

export function lineTotals(lines: OrderLine[], shipping = 0) {
  const round = (v: number) => Math.round(v * 100) / 100;
  let subtotal = 0;
  let discount = 0;
  let tax = 0;
  for (const line of lines) {
    const gross = round((Number(line.quantity) || 0) * (Number(line.unit_price) || 0));
    const lineDiscount = round((gross * (Number(line.discount_percent) || 0)) / 100);
    subtotal += gross;
    discount += lineDiscount;
    tax += round(((gross - lineDiscount) * (Number(line.tax_percent) || 0)) / 100);
  }
  return {
    subtotal: round(subtotal),
    discount: round(discount),
    tax: round(tax),
    shipping: round(shipping),
    total: round(subtotal - discount + tax + (Number(shipping) || 0)),
  };
}

export function formatAddress(address: Address | undefined | null) {
  if (!address) return "";
  const cityLine = [address.city, address.state, address.postal_code].filter(Boolean).join(", ");
  return [address.street, cityLine, address.country].filter(Boolean).join("\n");
}

// ---------------------------------------------------------------- Meetings

export interface Attendee {
  type: "user" | "contact" | "lead" | "email";
  id: string | null;
  email: string | null;
  name: string;
  response: "needs_action" | "accepted" | "declined" | "tentative";
}

export interface Meeting {
  id: string;
  title: string;
  description: string | null;
  location: string | null;
  meeting_url: string | null;
  is_online: boolean;
  is_all_day: boolean;
  start_at: string;
  end_at: string;
  status: "scheduled" | "completed" | "cancelled";
  account_id: string | null;
  account_name: string | null;
  contact_id: string | null;
  contact_name: string | null;
  lead_id: string | null;
  lead_name: string | null;
  opportunity_id: string | null;
  opportunity_name: string | null;
  organizer_user_id: string | null;
  organizer_name: string | null;
  attendees: Attendee[];
  notes: string | null;
  summary: string | null;
  action_items: string[];
  reminder_minutes: number | null;
  cancel_reason: string | null;
  my_response: Attendee["response"] | null;
  can_edit: boolean;
  invites_sent: number;
  conflicts?: { id: string; title: string; start_at: string; end_at: string }[];
  tasks_created?: number;
}

export const MEETING_STATUS: Record<Meeting["status"], { label: string; tone: Tone }> = {
  scheduled: { label: "Scheduled", tone: "info" },
  completed: { label: "Held", tone: "success" },
  cancelled: { label: "Cancelled", tone: "danger" },
};

export const RESPONSE_LABEL: Record<Attendee["response"], string> = {
  needs_action: "No reply",
  accepted: "Going",
  declined: "Declined",
  tentative: "Maybe",
};

// ---------------------------------------------------------------- Documents

export interface DocumentLinkRef {
  link_id: string;
  record_id: string;
  type: string;
  label: string;
  href: string;
  kind: string;
}

export interface DocumentItem {
  id: string;
  name: string;
  description: string | null;
  folder_id: string | null;
  folder_name: string | null;
  content_type: string;
  size_bytes: number;
  version: number;
  visibility: "organization" | "private";
  tags: string[];
  owner_user_id: string | null;
  owner_name: string | null;
  download_count: number;
  previewable: boolean;
  links: DocumentLinkRef[];
  can_edit: boolean;
  created_at: string;
  updated_at: string;
  versions?: { version: number; file_name: string; content_type: string; size_bytes: number; note: string | null; uploaded_by: string | null; created_at: string }[];
}

export interface Folder {
  id: string;
  name: string;
  parent_id: string | null;
  document_count: number;
}

export function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function fileKind(contentType: string, name: string) {
  const ext = name.includes(".") ? name.split(".").pop()!.toUpperCase() : "";
  if (contentType.startsWith("image/")) return "Image";
  if (contentType === "application/pdf") return "PDF";
  if (/sheet|excel|csv/.test(contentType) || ["XLSX", "XLS", "CSV"].includes(ext)) return "Spreadsheet";
  if (/word|document/.test(contentType) || ["DOC", "DOCX"].includes(ext)) return "Document";
  if (/presentation|powerpoint/.test(contentType) || ["PPT", "PPTX"].includes(ext)) return "Slides";
  if (/zip|compressed/.test(contentType)) return "Archive";
  return ext || "File";
}

/** Three or four letters for a file icon. */
export function fileBadge(contentType: string, name: string) {
  const kind = fileKind(contentType, name);
  const map: Record<string, string> = { Image: "IMG", PDF: "PDF", Spreadsheet: "XLS", Document: "DOC", Slides: "PPT", Archive: "ZIP", File: "FILE" };
  return map[kind] ?? kind.slice(0, 4).toUpperCase();
}

/** Multipart upload through the authenticated proxy. */
export async function uploadFile<T>(path: string, form: FormData): Promise<T> {
  const response = await fetch(`/api/aiva${path}`, { method: "POST", body: form, credentials: "same-origin" });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail[0]?.msg ?? "Upload failed." : "Upload failed.");
  }
  return data as T;
}

// ---------------------------------------------------------------- Services and appointments

export interface ServiceItem {
  id: string;
  name: string;
  category: string | null;
  description: string | null;
  duration_minutes: number;
  buffer_minutes: number;
  price: number;
  currency: string;
  location_type: "in_person" | "online" | "phone" | "customer_site";
  location: string | null;
  staff: { id: string; name: string }[];
  staff_user_ids: string[];
  is_active: boolean;
  upcoming_appointments: number | null;
}

export const LOCATION_TYPES: Record<ServiceItem["location_type"], string> = {
  in_person: "At our office",
  online: "Online",
  phone: "Phone call",
  customer_site: "At the customer's site",
};

export interface Appointment {
  id: string;
  number: number;
  display_number: string;
  title: string;
  status: "scheduled" | "completed" | "cancelled" | "no_show";
  service_id: string | null;
  service_name: string | null;
  staff_user_id: string | null;
  staff_name: string | null;
  contact_id: string | null;
  contact_name: string | null;
  account_id: string | null;
  account_name: string | null;
  lead_id: string | null;
  lead_name: string | null;
  start_at: string;
  end_at: string;
  location: string | null;
  price: number;
  currency: string;
  notes: string | null;
  outcome: string | null;
  cancel_reason: string | null;
  customer_emailed?: boolean;
}

export const APPOINTMENT_STATUS: Record<Appointment["status"], { label: string; tone: Tone }> = {
  scheduled: { label: "Scheduled", tone: "info" },
  completed: { label: "Completed", tone: "success" },
  cancelled: { label: "Cancelled", tone: "danger" },
  no_show: { label: "No-show", tone: "warning" },
};

// ---------------------------------------------------------------- Projects

export type ProjectStatus = "planning" | "active" | "on_hold" | "completed" | "cancelled";

export const PROJECT_STATUS: Record<ProjectStatus, { label: string; tone: Tone }> = {
  planning: { label: "Planning", tone: "neutral" },
  active: { label: "Active", tone: "info" },
  on_hold: { label: "On hold", tone: "warning" },
  completed: { label: "Completed", tone: "success" },
  cancelled: { label: "Cancelled", tone: "danger" },
};

export interface ProjectTask {
  id: string;
  title: string;
  description: string | null;
  status: "open" | "in_progress" | "completed" | "cancelled";
  priority: "low" | "medium" | "high" | "urgent";
  assigned_to_user_id: string | null;
  assignee_name: string | null;
  due_at: string | null;
  completed_at: string | null;
  milestone_id: string | null;
  is_overdue: boolean;
}

export interface Milestone {
  id: string;
  name: string;
  due_date: string | null;
  status: "open" | "completed";
  position: number;
  is_late: boolean;
  tasks_total: number;
  tasks_done: number;
}

export interface Project {
  id: string;
  number: number;
  display_number: string;
  name: string;
  description: string | null;
  status: ProjectStatus;
  priority: "low" | "medium" | "high" | "critical";
  is_late: boolean;
  account_id: string | null;
  account_name: string | null;
  opportunity_id: string | null;
  opportunity_name: string | null;
  owner_user_id: string | null;
  owner_name: string | null;
  members: { id: string; name: string }[];
  member_user_ids: string[];
  start_date: string | null;
  due_date: string | null;
  budget: number | null;
  currency: string;
  tasks_total: number;
  tasks_done: number;
  tasks_overdue: number;
  milestones_total: number;
  milestones_done: number;
  progress: number;
  completed_at: string | null;
  milestones?: Milestone[];
  tasks?: ProjectTask[];
  can_manage?: boolean;
  can_work?: boolean;
}

// ---------------------------------------------------------------- Social

export type Network = "linkedin" | "x" | "facebook" | "instagram";

export const NETWORK_LABEL: Record<Network, string> = { linkedin: "LinkedIn", x: "X", facebook: "Facebook", instagram: "Instagram" };

export interface SocialChannel {
  id: string;
  network: Network;
  network_label: string;
  name: string;
  handle: string | null;
  external_id: string;
  status: "connected" | "error";
  last_error: string | null;
  last_checked_at: string | null;
}

export interface SocialPost {
  id: string;
  text: string;
  link_url: string | null;
  image_url: string | null;
  status: "draft" | "scheduled" | "publishing" | "published" | "partial" | "failed";
  scheduled_at: string | null;
  published_at: string | null;
  campaign: string | null;
  channel_ids: string[];
  channels: { id: string; name: string; network: Network | null; result: { status: string; url?: string | null; error?: string | null } | null }[];
  created_by: string | null;
  created_at: string;
}

export const POST_STATUS: Record<SocialPost["status"], { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "neutral" },
  scheduled: { label: "Scheduled", tone: "info" },
  publishing: { label: "Publishing", tone: "info" },
  published: { label: "Published", tone: "success" },
  partial: { label: "Partly published", tone: "warning" },
  failed: { label: "Failed", tone: "danger" },
};

// ---------------------------------------------------------------- Website visits

export interface TrackingSite {
  id: string;
  name: string;
  domain: string;
  site_key: string;
  is_active: boolean;
  last_hit_at: string | null;
  visitors_30d: number;
}

export interface Visitor {
  id: string;
  label: string;
  is_identified: boolean;
  email: string | null;
  name: string | null;
  site_name: string | null;
  contact_id: string | null;
  contact_name: string | null;
  lead_id: string | null;
  lead_name: string | null;
  first_seen_at: string;
  last_seen_at: string;
  visit_count: number;
  page_view_count: number;
  total_seconds: number;
  first_referrer: string | null;
  last_url: string | null;
  device: string | null;
  browser: string | null;
  language: string | null;
  timezone: string | null;
}

export interface VisitSession {
  id: string;
  started_at: string;
  duration_seconds: number;
  page_count: number;
  source: string;
  referrer: string | null;
  utm_campaign: string | null;
  landing_url: string | null;
  pages: { url: string; path: string; title: string | null; viewed_at: string; seconds: number }[];
}

export function seconds(value: number) {
  if (value < 60) return `${value}s`;
  const minutes = Math.floor(value / 60);
  if (minutes < 60) return `${minutes}m ${value % 60 ? `${value % 60}s` : ""}`.trim();
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

// ---------------------------------------------------------------- Pins and chat

export interface PinItem {
  id: string;
  record_type: string;
  record_id: string;
  label: string;
  subtitle: string | null;
  href: string;
  position: number;
}

export interface ChatChannel {
  id: string;
  kind: "channel" | "direct";
  name: string | null;
  title: string;
  topic: string | null;
  is_private: boolean;
  is_archived: boolean;
  is_member: boolean;
  my_role: "owner" | "member" | null;
  member_count: number;
  member_ids: string[];
  other_user_id: string | null;
  unread: number;
  last_message: { body: string; author: string | null; created_at: string } | null;
  members?: { id: string; name: string; email: string | null }[];
}

export interface ChatMessage {
  id: string;
  channel_id: string;
  user_id: string | null;
  author: string;
  body: string;
  mentions: string[];
  record: { type: string; id: string; label: string; href: string; kind: string } | null;
  is_mine: boolean;
  edited_at: string | null;
  deleted: boolean;
  created_at: string;
}

export interface Person {
  id: string;
  full_name: string;
  email: string;
  job_title: string | null;
  phone: string | null;
  timezone: string | null;
  role: string;
  role_name: string;
  team_name: string | null;
  manager_name: string | null;
  last_login_at: string | null;
  is_me: boolean;
}

export interface Member {
  id: string;
  full_name: string;
  email: string;
}

export const PINS_CHANGED = "aiva:pins-changed";
