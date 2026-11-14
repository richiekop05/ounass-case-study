import type { ReactNode } from "react";
import "./Chip.css";

// Chip (Design PRD 12): 24px high, 999px radius. Value chips are white with a border; flagged facet
// chips on the list use flag-bg. With onRemove it becomes a removable filter chip.
export function Chip({
  children,
  tone = "value",
  onRemove,
  removeLabel = "Remove filter",
}: {
  children: ReactNode;
  tone?: "value" | "flag";
  onRemove?: () => void;
  removeLabel?: string;
}) {
  return (
    <span className={"chip chip-" + tone + (onRemove ? " chip-removable" : "")}>
      {children}
      {onRemove && (
        <button type="button" className="chip-remove" onClick={onRemove} title={removeLabel} aria-label={removeLabel}>
          ×
        </button>
      )}
    </span>
  );
}
