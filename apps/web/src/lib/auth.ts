import { aivaRequest } from "@/lib/api";

export interface AivaLoginRequest {
  email: string;
  password: string;
  organization_id: string;
}

export interface AivaLoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user_id: string;
  organization_id: string;
  role: string;
}

interface AivaAuthSession {
  accessToken: string;
  userId: string;
  organizationId: string;
  role: string;
  expiresAt: number;
}

let currentSession: AivaAuthSession | null = null;

export async function loginToAiva(
  credentials: AivaLoginRequest
): Promise<AivaLoginResponse> {
  const response = await aivaRequest<AivaLoginResponse>(
    "/auth/login",
    {
      method: "POST",
      body: JSON.stringify(credentials),
    }
  );

  currentSession = {
    accessToken: response.access_token,
    userId: response.user_id,
    organizationId: response.organization_id,
    role: response.role,
    expiresAt: Date.now() + response.expires_in * 1000,
  };

  return response;
}

export function getAivaAccessToken(): string | null {
  if (!currentSession) {
    return null;
  }

  if (Date.now() >= currentSession.expiresAt) {
    currentSession = null;
    return null;
  }

  return currentSession.accessToken;
}

export function getAivaSession(): Omit<
  AivaAuthSession,
  "accessToken"
> | null {
  if (!getAivaAccessToken() || !currentSession) {
    return null;
  }

  return {
    userId: currentSession.userId,
    organizationId: currentSession.organizationId,
    role: currentSession.role,
    expiresAt: currentSession.expiresAt,
  };
}

export function logoutFromAiva(): void {
  currentSession = null;
}
