import "./ConfidenceBadge.css";

// Confidence badge (Design PRD 12): a pill with the number to two decimals and a 40px bar filled
// in proportion. Below the threshold it reads "Low 0.62" in flag colours; rule-written facets show
// "Rule"; no confidence at all shows a muted dash.
export function ConfidenceBadge({
  value,
  low,
  rule = false,
}: {
  value: number | null;
  low: boolean;
  rule?: boolean;
}) {
  if (rule) return <span className="conf-badge-quiet">Rule</span>;
  if (value === null) return <span className="conf-badge-quiet" aria-label="No confidence">—</span>;
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const text = value.toFixed(2);
  return (
    <span className={"conf-badge" + (low ? " is-low" : "")}>
      {low ? `Low ${text}` : text}
      <span className="conf-badge-bar" aria-hidden="true">
        <span className="conf-badge-fill" style={{ width: `${pct}%` }} />
      </span>
    </span>
  );
}
