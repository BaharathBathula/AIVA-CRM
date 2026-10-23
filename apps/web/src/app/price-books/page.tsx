"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { BookMarked, Plus } from "lucide-react";
import { useState } from "react";

import { FeaturePage } from "@/components/plan";
import { PriceBookModal } from "@/components/price-book-modal";
import { Alert, Badge, EmptyState, LoadingRows, PageHeader } from "@/components/ui";
import { date } from "@/lib/format";
import { useApi } from "@/lib/use-api";
import type { PriceBook } from "@/lib/workspace";

export default function PriceBooksPage() {
  return (
    <FeaturePage feature="price_books">
      <PriceBooks />
    </FeaturePage>
  );
}

function PriceBooks() {
  const router = useRouter();
  const books = useApi<{ items: PriceBook[]; can_manage: boolean }>("/price-books");
  const [creating, setCreating] = useState(false);

  return (
    <>
      <PageHeader
        title="Price books"
        description="Special list prices and volume discounts for partners, wholesale buyers or regions. Pick a price book on a quote or sales order to price it."
        actions={
          books.data?.can_manage ? (
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => setCreating(true)}>
              <Plus size={16} /> New price book
            </button>
          ) : null
        }
      />
      {books.error ? <Alert>{books.error}</Alert> : null}
      <div className="ui-card">
        {books.loading && !books.data ? (
          <LoadingRows />
        ) : books.data?.items.length ? (
          <div className="ui-table-wrap">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Price book</th>
                  <th>Pricing</th>
                  <th>Valid</th>
                  <th className="ui-num">Products</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {books.data.items.map((book) => (
                  <tr key={book.id} className="clickable" onClick={() => router.push(`/price-books/${book.id}`)}>
                    <td>
                      <Link href={`/price-books/${book.id}`} className="cell-title" onClick={(e) => e.stopPropagation()}>
                        {book.name}
                      </Link>
                      {book.description ? <div className="cell-sub ui-truncate" style={{ maxWidth: 360 }}>{book.description}</div> : null}
                    </td>
                    <td>{book.pricing_model === "differential" ? "List price + volume tiers" : "List price"} · {book.currency}</td>
                    <td>{book.valid_from || book.valid_until ? `${date(book.valid_from) || "Any time"} – ${date(book.valid_until) || "No end"}` : "Always"}</td>
                    <td className="ui-num">{book.entry_count}</td>
                    <td>
                      <Badge tone={book.is_current ? "success" : "neutral"}>{book.is_current ? "In use" : book.is_active ? "Not in date range" : "Inactive"}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={<BookMarked size={20} />} title="No price books yet" message="Create one for each group of customers that gets its own prices." />
        )}
      </div>
      <PriceBookModal open={creating} onClose={() => setCreating(false)} onSaved={(book) => router.push(`/price-books/${book.id}`)} />
    </>
  );
}
