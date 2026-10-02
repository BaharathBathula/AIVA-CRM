const API_URL =
  process.env.NEXT_PUBLIC_AIVA_API_URL ??
  "http://localhost:8000/api/v1";

const ORGANIZATION_ID =
  process.env.NEXT_PUBLIC_AIVA_ORGANIZATION_ID;


export class AivaApiError extends Error {
  status: number;

  constructor(
    message: string,
    status: number
  ) {
    super(message);
    this.name = "AivaApiError";
    this.status = status;
  }
}


function getOrganizationId(): string {
  if (!ORGANIZATION_ID) {
    throw new AivaApiError(
      "AIVA organization is not configured.",
      500
    );
  }

  return ORGANIZATION_ID;
}


export async function aivaRequest<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(
    options.headers
  );

  headers.set(
    "Content-Type",
    "application/json"
  );

  headers.set(
    "X-Organization-ID",
    getOrganizationId()
  );

  const response = await fetch(
    `${API_URL}${path}`,
    {
      ...options,
      headers,
      cache: "no-store",
    }
  );

  if (!response.ok) {
    let message =
      "AIVA API request failed.";

    try {
      const body = await response.json();

      if (typeof body?.detail === "string") {
        message = body.detail;
      } else if (
        body?.detail?.status
      ) {
        message = body.detail.status;
      }
    } catch {
      // Keep default error message.
    }

    throw new AivaApiError(
      message,
      response.status
    );
  }

  return response.json();
}
