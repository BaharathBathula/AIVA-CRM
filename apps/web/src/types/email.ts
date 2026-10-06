export type EmailDirection =
  | "inbound"
  | "outbound";

export interface EmailThread {
  id: string;
  organization_id: string;

  provider: string;
  external_thread_id: string | null;

  subject: string;
  snippet: string | null;

  account_id: string | null;
  contact_id: string | null;
  opportunity_id: string | null;

  participants: Array<
    Record<string, unknown>
  >;

  last_message_at: string | null;

  created_at: string;
  updated_at: string;
}

export interface EmailMessage {
  id: string;
  organization_id: string;
  thread_id: string;

  provider: string;
  external_message_id: string | null;

  account_id: string | null;
  contact_id: string | null;
  opportunity_id: string | null;

  direction: EmailDirection;

  subject: string;

  from_address: string;
  from_name: string | null;

  to_recipients: Array<
    Record<string, unknown>
  >;

  cc_recipients: Array<
    Record<string, unknown>
  >;

  bcc_recipients: Array<
    Record<string, unknown>
  >;

  reply_to: string | null;

  body_text: string | null;
  body_html: string | null;
  snippet: string | null;

  occurred_at: string;

  is_read: boolean;
  is_draft: boolean;

  has_attachments: boolean;

  attachments: Array<
    Record<string, unknown>
  >;

  internet_message_id: string | null;
  in_reply_to: string | null;

  references: string[];

  message_metadata:
    Record<string, unknown>;

  created_at: string;
  updated_at: string;
}

export interface EmailComposePayload {
  to_recipients: Array<Record<string, unknown>>;
  cc_recipients?: Array<Record<string, unknown>>;
  bcc_recipients?: Array<Record<string, unknown>>;

  subject: string;

  body_text?: string | null;
  body_html?: string | null;

  from_address: string;
  from_name?: string | null;
  reply_to?: string | null;

  account_id?: string | null;
  contact_id?: string | null;
  lead_id?: string | null;
  opportunity_id?: string | null;

  is_draft?: boolean;
}

export interface EmailReplyPayload {
  body_text?: string | null;
  body_html?: string | null;

  from_address: string;
  from_name?: string | null;

  cc_recipients?: Array<Record<string, unknown>>;
  bcc_recipients?: Array<Record<string, unknown>>;

  is_draft?: boolean;
}

export interface EmailSendResult {
  thread: EmailThread;
  message: EmailMessage;
}