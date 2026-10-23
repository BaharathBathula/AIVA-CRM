import type { Tone } from "@/components/ui";

export type InvoiceStatus = "draft" | "sent" | "overdue" | "paid" | "void";

export interface InvoiceSummary {
  id: string;
  number: number;
  display_number: string;
  status: InvoiceStatus;
  quote_id: string | null;
  account_id: string | null;
  account_name: string | null;
  contact_id: string | null;
  contact_name: string | null;
  owner_name: string | null;
  issue_date: string;
  due_date: string | null;
  currency: string;
  subtotal: number;
  discount_total: number;
  tax_total: number;
  total: number;
  amount_paid: number;
  balance: number;
  paid_at: string | null;
  exported_at: string | null;
  created_at: string;
}

export interface InvoiceLine {
  name: string;
  description: string | null;
  product_id: string | null;
  quantity: number;
  unit_price: number;
  discount_percent: number;
  tax_percent: number;
  line_total: number;
}

export interface InvoiceDetail extends InvoiceSummary {
  line_items: InvoiceLine[];
  terms: string | null;
  notes: string | null;
  organization_name: string;
  bill_to?: { name: string; city: string | null; state: string | null; postal_code: string | null; country: string | null } | null;
}

export const INVOICE_STATUS: Record<InvoiceStatus, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "neutral" },
  sent: { label: "Sent", tone: "info" },
  overdue: { label: "Overdue", tone: "danger" },
  paid: { label: "Paid", tone: "success" },
  void: { label: "Void", tone: "neutral" },
};
