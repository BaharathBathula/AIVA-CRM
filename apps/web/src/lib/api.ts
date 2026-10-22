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

  // Authentication is carried by httpOnly session cookies that the
  // /api/aiva proxy turns into backend credentials.

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

    if (
      response.status === 401 &&
      typeof window !== "undefined" &&
      !window.location.pathname.startsWith("/login")
    ) {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.assign(`/login?next=${next}`);
    }

    throw new AivaApiError(message, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
