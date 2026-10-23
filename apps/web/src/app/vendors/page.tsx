"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Plus, Search, Store } from "lucide-react";
import { Suspense, useState } from "react";

import { FeaturePage } from "@/components/plan";
import { useToast } from "@/components/providers";
import { Alert, Badge, EmptyState, LoadingRows, PageHeader, Tabs } from "@/components/ui";
import { VendorModal } from "@/components/vendor-modal";
import { compactMoney } from "@/lib/format";
import { useApi, useDebounced } from "@/lib/use-api";
import type { Vendor } from "@/lib/workspace";

export default function VendorsPage() {
  return (
    <FeaturePage feature="purchase_orders">
      <Suspense>
        <Vendors />
      </Suspense>
    </FeaturePage>
  );
}

function Vendors() {
  const router = useRouter();
  const params = useSearchParams();
  const toast = useToast();
  const [filter, setFilter] = useState<"active" | "inactive" | "all">("active");
  const [search, setSearch] = useState("");
  const debounced = useDebounced(search);
  const [creating, setCreating] = useState(params.get("new") === "1");
  const query = new URLSearchParams();
  if (filter !== "all") query.set("active", String(filter === "active"));
  if (debounced.trim()) query.set("search", debounced.trim());
  const vendors = useApi<{ items: Vendor[]; can_manage: boolean }>(`/vendors?${query}`);

  return (
    <>
      <PageHeader
        title="Vendors"
        description="The suppliers you buy from, with what's on order and what you've spent."
        actions={
          vendors.data?.can_manage ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setCreating(true)}>
              <Plus size={16} /> New vendor
            </button>
          ) : null
        }
      />
      <div className="ui-toolbar">
        <Tabs
          label="Vendor status"
          value={filter}
          onChange={setFilter}
          items={[
            { value: "active", label: "Active" },
            { value: "inactive", label: "Inactive" },
            { value: "all", label: "All" },
          ]}
        />
        <span className="ui-spacer" />
        <div className="ui-search">
          <Search size={15} />
          <input className="ui-input sm" placeholder="Search vendors" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search vendors" />
        </div>
      </div>
      {vendors.error ? <Alert>{vendors.error}</Alert> : null}
      <div className="ui-card">
        {vendors.loading && !vendors.data ? (
          <LoadingRows />
        ) : vendors.data?.items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Vendor</th>
                  <th>Contact</th>
                  <th>Email</th>
                  <th>Phone</th>
                  <th>Terms</th>
                  <th className="ui-num">Open orders</th>
                  <th className="ui-num">Spend</th>
                </tr>
              </thead>
              <tbody>
                {vendors.data.items.map((vendor) => (
                  <tr key={vendor.id} className="clickable" onClick={() => router.push(`/vendors/${vendor.id}`)}>
                    <td>
                      <Link href={`/vendors/${vendor.id}`} className="cell-title" onClick={(e) => e.stopPropagation()}>
                        {vendor.name}
                      </Link>
                      <div className="cell-sub">
                        {vendor.category ?? "Vendor"}
                        {!vendor.is_active ? (
                          <>
                            {" "}
                            · <Badge>Inactive</Badge>
                          </>
                        ) : null}
                      </div>
                    </td>
                    <td>{vendor.contact_name ?? "—"}</td>
                    <td>{vendor.email ?? "—"}</td>
                    <td>{vendor.phone ?? "—"}</td>
                    <td>{vendor.payment_terms ?? "—"}</td>
                    <td className="ui-num">{vendor.open_orders ?? 0}</td>
                    <td className="ui-num ui-strong">{compactMoney(vendor.total_spend ?? 0, vendor.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<Store size={20} />} title={search ? "No vendors match" : "No vendors yet"} message="Add the suppliers you buy products and materials from." />
        )}
      </div>
      <VendorModal
        open={creating}
        onClose={() => setCreating(false)}
        onSaved={(vendor) => {
          setCreating(false);
          toast(`${vendor.name} added.`);
          router.push(`/vendors/${vendor.id}`);
        }}
      />
    </>
  );
}
