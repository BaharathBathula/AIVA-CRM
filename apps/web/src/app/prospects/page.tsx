"use client";

import Link from "next/link";

import { ProspectSearch } from "@/components/integrations/ProspectSearch";
import { FeaturePage } from "@/components/plan";
import { PageHeader } from "@/components/ui";

export default function ProspectsPage() {
  return (
    <FeaturePage feature="waterfall_enrichment" permission="records.read" what="Finding prospects">
      <PageHeader
        title="Find prospects"
        description="Search Apollo.io and ZoomInfo for people and companies that fit your ideal customer, then import them as leads, contacts or accounts. People on your do-not-contact list are never imported."
        actions={
          <Link className="ui-btn" href="/integrations?category=Sales%20Intelligence">
            Sources
          </Link>
        }
      />
      <ProspectSearch />
    </FeaturePage>
  );
}
