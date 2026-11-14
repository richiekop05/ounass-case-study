import {
  EVIDENCE_LABEL,
  FLAG_REASON,
  hasValue,
  isLow,
  plainReason,
  shownText,
  shownValue,
  valueKey,
  valueText,
  type Facet,
  type FacetDecision,
} from "../productTypes";
import { Chip } from "./Chip";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { ValueEditor, type EditorResult } from "./ValueEditor";
import "./FacetRow.css";

// Facet row (Design PRD 12), open and compact.
// Open: line 1 name (definition on hover), "Required", decision pill and confidence badge; line 2
// the value chips; line 3 the evidence label, the quote and the reason. Actions on the right:
// Approve (flagged rows), Change, Unknown (optional facets that have a value). Rule facets are
// read-only. Compact: one 40px line with name, value and badge; clicking it opens the row.

const REQUIRED_MSG = "Choose a value. This facet is required.";

export function FacetRow({
  facet: f,
  decision,
  open,
  editing,
  onToggle,
  onApprove,
  onEdit,
  onEditDone,
  onEditCancel,
  onUnknown,
  onQuote,
  problem,
}: {
  facet: Facet;
  decision: FacetDecision | undefined;
  open: boolean;
  editing: boolean;
  onToggle: () => void;
  onApprove: () => void;
  onEdit: () => void;
  onEditDone: (r: EditorResult) => void;
  onEditCancel: () => void;
  onUnknown: () => void;
  onQuote: (quote: string) => void;
  /** Why approval was refused for this facet, from the server (Design PRD 13: validation problem). */
  problem?: string;
}) {
  const shown = shownValue(f, decision);
  const a = f.answer;
  const missing = f.required && !f.rule && shown.state !== "not_applicable" && !hasValue(shown);
  const bar = missing || problem ? " is-error" : decision ? " is-ok" : f.flagged ? " is-flag" : "";
  const status = decision ? (decision.kind === "approved" ? "Approved" : "Changed") : null;
  const badge = (
    <ConfidenceBadge value={a?.confidence ?? null} low={isLow(a?.confidence ?? null, f.level)} rule={f.rule} />
  );
  const name = (
    <span className="label facet-name" title={f.definition ?? undefined}>
      {f.name}
    </span>
  );

  if (!open) {
    return (
      <button type="button" id={"facet-" + f.id} className={"facet-compact" + bar} onClick={onToggle} aria-expanded={false}>
        {name}
        <span className={"facet-compact-value" + (hasValue(shown) ? "" : " muted")}>{shownText(shown, f.unit)}</span>
        {status && <span className="facet-status">✓ {status}</span>}
        {badge}
      </button>
    );
  }

  const quote = a?.quote?.trim() || "";
  const ev = a?.evidence ? EVIDENCE_LABEL[a.evidence] : undefined;
  const reason = plainReason(a?.reason ?? null);
  const flagNote = f.flagged && !decision && f.flagReason ? FLAG_REASON[f.flagReason] : undefined;
  const canAct = !f.rule;

  return (
    <div id={"facet-" + f.id} className={"facet-open" + bar + (f.flagged && !decision ? " is-pending" : "")}>
      <div className="facet-line1">
        {name}
        {f.required && <span className="facet-required">Required</span>}
        {status && <span className="decided-pill">✓ {status}</span>}
        <div className="facet-line1-right">
          {badge}
          {!f.flagged && (
            <button type="button" className="facet-collapse" title="Collapse" aria-label="Collapse" onClick={onToggle}>
              −
            </button>
          )}
        </div>
      </div>
      <div className="facet-body">
        <div className="facet-main">
          <div className="facet-values">
            {hasValue(shown) ? (
              shown.values.map((v) => <Chip key={valueKey(v)}>{valueText(v, f.unit)}</Chip>)
            ) : (
              <span className="muted">{shownText(shown, f.unit)}</span>
            )}
          </div>
          {missing && <span className="small facet-error">{REQUIRED_MSG}</span>}
          {problem && !missing && <span className="small facet-error">{problem}</span>}
          {(ev || quote || reason) && (
            <div className="facet-line3 small">
              {ev && <span className="facet-ev">{ev}</span>}
              {quote && (
                <button type="button" className="facet-quote" title="Show in product text" onClick={() => onQuote(quote)}>
                  “{quote}”
                </button>
              )}
              {reason && <span>{reason}</span>}
            </div>
          )}
          {a && !f.quoteFound && <span className="facet-flagnote">⚠ Not found in product text</span>}
          {flagNote && <span className="facet-flagnote">{flagNote}</span>}
        </div>
        {canAct && !editing && (
          <div className="facet-actions">
            {f.flagged && !decision && (
              <button type="button" className="btn btn-secondary btn-sm" onClick={onApprove}>
                Approve
              </button>
            )}
            <button type="button" className="btn btn-secondary btn-sm" onClick={onEdit}>
              Change
            </button>
            {!f.required && hasValue(shown) && (
              <button type="button" className="btn btn-quiet btn-sm" onClick={onUnknown}>
                Unknown
              </button>
            )}
          </div>
        )}
      </div>
      {editing && <ValueEditor facet={f} current={shown} onDone={onEditDone} onCancel={onEditCancel} />}
    </div>
  );
}
