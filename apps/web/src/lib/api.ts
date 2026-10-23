const API_URL = "/api/aiva";

export class AivaApiError extends Error {
  status: number;
  /** Extra details some errors carry, e.g. `code: "approval_required"` with the rule to submit. */
  body: Record<string, unknown>;

  constructor(message: string, status: number, body: Record<string, unknown> = {}) {
    super(message);
    this.name = "AivaApiError";
    this.status = status;
    this.body = body;
  }

  get code(): string | undefined {
    return typeof this.body.code === "string" ? this.body.code : undefined;
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
    let errorBody: Record<string, unknown> = {};

    try {
      const body: unknown = await response.json();

      if (
        typeof body === "object" &&
        body !== null &&
        "detail" in body
      ) {
        errorBody = body as Record<string, unknown>;
        const detail = body.detail;

        if (typeof detail === "string") {
          message = detail;
        } else if (typeof detail === "object" && detail !== null && "message" in detail) {
          // Field-level validation, e.g. web form submissions.
          message = String((detail as { message: unknown }).message);
          errorBody = { ...errorBody, ...(detail as Record<string, unknown>) };
        } else if (Array.isArray(detail)) {
          message = "Check the highlighted fields and try again.";
        }
      }
    } catch {
      // Preserve the default error message.
    }

    if (
      response.status === 401 &&
      typeof window !== "undefined" &&
      !window.location.pathname.startsWith("/login") &&
      !window.location.pathname.startsWith("/portal/") &&
      !path.startsWith("/public/") &&
      !path.startsWith("/portal-api/")
    ) {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.assign(`/login?next=${next}`);
    }

    throw new AivaApiError(message, response.status, errorBody);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
