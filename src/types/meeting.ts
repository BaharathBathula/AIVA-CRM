cat > src/types/meeting.ts <<'TS'
export type MeetingStatus =
  | "scheduled"
  | "completed"
  | "cancelled";


export interface MeetingAttendee {
  name: string | null;
  email: string;
  response_status: string | null;
}


export interface Meeting {
  id: string;
  organization_id: string;

  provider: string;
  external_event_id: string | null;

  account_id: string | null;
  contact_id: string | null;
  opportunity_id: string | null;
  organizer_user_id: string | null;

  title: string;
  description: string | null;

  location: string | null;
  meeting_url: string | null;

  start_at: string;
  end_at: string;

  timezone: string | null;

  status: MeetingStatus;

  attendees: MeetingAttendee[];

  is_all_day: boolean;
  is_online: boolean;

  notes: string | null;
  summary: string | null;

  action_items: Record<
    string,
    unknown
  >[];

  meeting_metadata: Record<
    string,
    unknown
  >;

  created_at: string;
  updated_at: string;
}


export interface MeetingFilters {
  accountId?: string;
  contactId?: string;
  opportunityId?: string;
  provider?: string;
  status?: MeetingStatus;
  startFrom?: string;
  startTo?: string;
}


export interface MeetingCreatePayload {
  provider: string;

  external_event_id?: string | null;

  account_id?: string | null;
  contact_id?: string | null;
  opportunity_id?: string | null;
  organizer_user_id?: string | null;

  title: string;

  description?: string | null;

  location?: string | null;
  meeting_url?: string | null;

  start_at: string;
  end_at: string;

  timezone?: string | null;

  status?: MeetingStatus;

  attendees?: MeetingAttendee[];

  is_all_day?: boolean;
  is_online?: boolean;

  notes?: string | null;
  summary?: string | null;

  action_items?: Record<
    string,
    unknown
  >[];

  meeting_metadata?: Record<
    string,
    unknown
  >;
}


export type MeetingUpdatePayload =
  Partial<
    MeetingCreatePayload
  >;
TS
