const API_URL =
  "/api/aiva";


export class AivaApiError
  extends Error {
  status: number;

  constructor(
    message: string,
    status: number
  ) {
    super(message);

    this.name =
      "AivaApiError";

    this.status =
      status;
  }
}


export async function aivaRequest<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers =
    new Headers(
      options.headers
    );

  if (options.body) {
    headers.set(
      "Content-Type",
      "application/json"
    );
  }

  const response =
    await fetch(
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
      const body =
        await response.json();

      if (
        typeof body?.detail
        === "string"
      ) {
        message =
          body.detail;
      }
    } catch {
      // Keep default message.
    }

    throw new AivaApiError(
      message,
      response.status
    );
  }

  return response.json();
}
