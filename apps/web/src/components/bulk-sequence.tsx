"use client";

import { SquareStack, X } from "lucide-react";
import { useState } from "react";

import { useSession } from "@/components/providers";
import { EnrollInSequenceModal } from "@/components/sequence-enroll";

/** Whether this person can pick rows and add them to a sequence. */
export function useCanEnroll() {
  const { has, can } = useSession();
  return has("sequences") && can("sequences.manage");
}

/** Floating bar for selected list rows: add them to a sequence. */
export function BulkSequenceBar({
  recordType,
  selected,
  onClear,
}: {
  recordType: "lead" | "contact";
  selected: string[];
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState(false);
  if (!selected.length) return null;
  return (
    <>
      <div className="bulk-bar" role="region" aria-label="Selected rows">
        <strong>
          {selected.length} {recordType === "lead" ? (selected.length === 1 ? "lead" : "leads") : selected.length === 1 ? "contact" : "contacts"} selected
        </strong>
        <button type="button" className="ui-btn ui-btn-sm ui-btn-primary" onClick={() => setOpen(true)}>
          <SquareStack size={14} /> Add to sequence
        </button>
        <button type="button" className="ui-btn ui-btn-sm ui-btn-ghost" onClick={onClear} aria-label="Clear selection">
          <X size={14} /> Clear
        </button>
      </div>
      <EnrollInSequenceModal
        open={open}
        recordType={recordType}
        recordIds={selected}
        onClose={() => {
          setOpen(false);
          // Keep the results on screen until the modal is closed, then clear the rows that were added.
          if (done) {
            setDone(false);
            onClear();
          }
        }}
        onDone={() => setDone(true)}
      />
    </>
  );
}

/** Header checkbox that selects or clears every visible row. */
export function SelectAllBox({ ids, selected, onChange }: { ids: string[]; selected: string[]; onChange: (ids: string[]) => void }) {
  const all = ids.length > 0 && ids.every((id) => selected.includes(id));
  const some = !all && ids.some((id) => selected.includes(id));
  return (
    <input
      type="checkbox"
      aria-label={all ? "Clear selection" : "Select all rows"}
      checked={all}
      ref={(element) => {
        if (element) element.indeterminate = some;
      }}
      onChange={() => onChange(all ? selected.filter((id) => !ids.includes(id)) : Array.from(new Set([...selected, ...ids])))}
    />
  );
}
