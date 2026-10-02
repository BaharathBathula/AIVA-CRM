import {
  NextRequest,
} from "next/server";


const BACKEND_URL =
  process.env.AIVA_INTERNAL_API_URL ??
  "http://127.0.0.1:8000/api/v1";

const ORGANIZATION_ID =
  process.env.AIVA_ORGANIZATION_ID ??
  "10000000-0000-0000-0000-000000000001";


async function proxy(
  request: NextRequest
) {
  const path =
    request.nextUrl.pathname.replace(
      /^\/api\/aiva\/?/,
      ""
    );

  const targetUrl =
    `${BACKEND_URL}/${path}${request.nextUrl.search}`;

  const headers =
    new Headers();

  headers.set(
    "X-Organization-ID",
    ORGANIZATION_ID
  );

  const contentType =
    request.headers.get(
      "content-type"
    );

  if (contentType) {
    headers.set(
      "Content-Type",
      contentType
    );
  }

  const authorization =
    request.headers.get(
      "authorization"
    );

  if (authorization) {
    headers.set(
      "Authorization",
      authorization
    );
  }

  const options: RequestInit = {
    method: request.method,
    headers,
    cache: "no-store",
  };

  if (
    request.method !== "GET" &&
    request.method !== "HEAD"
  ) {
    options.body =
      await request.text();
  }

  const response =
    await fetch(
      targetUrl,
      options
    );

  return new Response(
    response.body,
    {
      status: response.status,
      headers: {
        "Content-Type":
          response.headers.get(
            "content-type"
          ) ??
          "application/json",
      },
    }
  );
}


export async function GET(
  request: NextRequest
) {
  return proxy(request);
}


export async function POST(
  request: NextRequest
) {
  return proxy(request);
}


export async function PATCH(
  request: NextRequest
) {
  return proxy(request);
}


export async function PUT(
  request: NextRequest
) {
  return proxy(request);
}


export async function DELETE(
  request: NextRequest
) {
  return proxy(request);
}
