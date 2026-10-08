const API_URL = "/api/aiva";

export class AivaApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "AivaApiError";
    this.status = status;
  }
}

export async function aivaRequest<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers);

  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  // Attach an authenticated session when one exists.
  // Dynamic import avoids a circular dependency:
  // auth.ts imports aivaRequest() for the login endpoint.
  if (path !== "/auth/login" && typeof window !== "undefined") {
    const { getAivaAccessToken } = await import("@/lib/auth");
    const token = getAivaAccessToken();

    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers,
    cache: "no-store",
  });

  if (!response.ok) {
    let message = "AIVA API request failed.";

    try {
      const body: unknown = await response.json();

      if (
        typeof body === "object" &&
        body !== null &&
        "detail" in body
      ) {
        const detail = body.detail;

        if (typeof detail === "string") {
          message = detail;
        }
      }
    } catch {
      // Preserve the default error message.
    }

    throw new AivaApiError(message, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
