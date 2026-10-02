cat > src/types/email.ts <<'TS'
export interface EmailParticipant {
  name: string | null;
  email: string;
}


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

  participants: EmailParticipant[];

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

  direction: "inbound" | "outbound";

  subject: string;

  from_address: string;
  from_name: string | null;

  to_recipients: EmailParticipant[];
  cc_recipients: EmailParticipant[];
  bcc_recipients: EmailParticipant[];

  reply_to: string | null;

  body_text: string | null;
  body_html: string | null;
  snippet: string | null;

  occurred_at: string;

  is_read: boolean;
  is_draft: boolean;
  has_attachments: boolean;

  attachments: Record<
    string,
    unknown
  >[];

  internet_message_id: string | null;
  in_reply_to: string | null;

  references: string[];

  message_metadata: Record<
    string,
    unknown
  >;

  created_at: string;
  updated_at: string;
}


export interface EmailThreadFilters {
  accountId?: string;
  contactId?: string;
  opportunityId?: string;
  provider?: string;
  search?: string;
}


export interface EmailThreadCreatePayload {
  provider: string;
  external_thread_id?: string | null;

  subject: string;
  snippet?: string | null;

  account_id?: string | null;
  contact_id?: string | null;
  opportunity_id?: string | null;

  participants?: EmailParticipant[];

  last_message_at?: string | null;
}


export interface EmailMessageCreatePayload {
  thread_id: string;

  provider: string;
  external_message_id?: string | null;

  account_id?: string | null;
  contact_id?: string | null;
  opportunity_id?: string | null;

  direction: "inbound" | "outbound";

  subject: string;

  from_address: string;
  from_name?: string | null;

  to_recipients?: EmailParticipant[];
  cc_recipients?: EmailParticipant[];
  bcc_recipients?: EmailParticipant[];

  reply_to?: string | null;

  body_text?: string | null;
  body_html?: string | null;
  snippet?: string | null;

  occurred_at: string;

  is_read?: boolean;
  is_draft?: boolean;

  has_attachments?: boolean;

  attachments?: Record<
    string,
    unknown
  >[];

  internet_message_id?: string | null;
  in_reply_to?: string | null;

  references?: string[];

  message_metadata?: Record<
    string,
    unknown
  >;
}
TS
