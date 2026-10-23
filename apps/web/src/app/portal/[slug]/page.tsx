"use client";

import { useParams } from "next/navigation";
import { Suspense } from "react";

import { PortalApp } from "@/components/portal-app";

export default function PortalPage() {
  const { slug } = useParams<{ slug: string }>();
  return (
    <Suspense>
      <PortalApp slug={slug} />
    </Suspense>
  );
}
