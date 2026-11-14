import "./StatusBadge.css";

// Status badge (Design PRD 12): a pill in text-xs. The wording lives here so every screen says the
// same thing. "Category in review" is the smaller inline variant used in the list's Category column.
export type Status = "in-review" | "approved" | "edited" | "needs-review" | "ready" | "category-review";

const STATUS: Record<Status, { label: string; className: string }> = {
  "in-review": { label: "In review", className: "pill pill-flag" },
  approved: { label: "Approved · live", className: "pill pill-ok" },
  edited: { label: "Edited", className: "pill pill-outline status-muted" },
  "needs-review": { label: "Needs review", className: "pill pill-flag" },
  ready: { label: "Ready", className: "pill pill-outline" },
  "category-review": { label: "Category in review", className: "status-inline" },
};

export function StatusBadge({ status }: { status: Status }) {
  const s = STATUS[status];
  return <span className={s.className}>{s.label}</span>;
}
