import {
  aivaRequest,
} from "@/lib/api";

import type {
  Lead,
  LeadConvertPayload,
  LeadConvertResult,
  LeadCreatePayload,
} from "@/types/lead";


export function getLeads(): Promise<Lead[]> {
  return aivaRequest<Lead[]>(
    "/leads"
  );
}


export function getLead(
  leadId: string
): Promise<Lead> {
  return aivaRequest<Lead>(
    `/leads/${leadId}`
  );
}


export function createLead(
  payload: LeadCreatePayload
): Promise<Lead> {
  return aivaRequest<Lead>(
    "/leads",
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );
}


export function updateLead(
  leadId: string,
  payload: Partial<LeadCreatePayload>
): Promise<Lead> {
  return aivaRequest<Lead>(
    `/leads/${leadId}`,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    }
  );
}


export function convertLead(
  leadId: string,
  payload: LeadConvertPayload
): Promise<LeadConvertResult> {
  return aivaRequest<LeadConvertResult>(
    `/leads/${leadId}/convert`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );
}
