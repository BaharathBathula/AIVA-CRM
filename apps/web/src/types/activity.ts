export type ActivityType =
  | "email"
  | "call"
  | "meeting"
  | "note"
  | "sms"
  | "task_completed"
  | "document"
  | "system"
  | "ai_action";


export type ActivityDirection =
  | "inbound"
  | "outbound";


export interface Activity {
  id: string;
  organization_id: string;

  account_id: string | null;
  contact_id: string | null;
  created_by_user_id: string | null;

  activity_type: ActivityType;

  subject: string;
  body: string | null;

  direction: ActivityDirection | null;

  occurred_at: string;

  external_id: string | null;

  activity_metadata: Record<
    string,
    unknown
  >;

  created_at: string;
  updated_at: string;
}


export interface ActivityCreatePayload {
  account_id?: string | null;
  contact_id?: string | null;

  activity_type: ActivityType;

  subject: string;
  body?: string | null;

  direction?: ActivityDirection | null;

  occurred_at?: string;

  external_id?: string | null;

  activity_metadata?: Record<
    string,
    unknown
  >;
}
