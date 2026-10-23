"use client";

import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { PortalAuth } from "@/components/portal-app";

function Verify() {
  const { slug } = useParams<{ slug: string }>();
  const params = useSearchParams();
  return <PortalAuth slug={slug} token={params.get("token")} />;
}

export default function PortalAuthPage() {
  return (
    <Suspense>
      <Verify />
    </Suspense>
  );
}
