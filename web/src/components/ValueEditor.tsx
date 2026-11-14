import { useState } from "react";
import { hasValue, type AnswerValue, type Facet, type ShownValue } from "../productTypes";
import "./ValueEditor.css";

// Value editor (Design PRD 12): opens in place under the row, never as a pop-up. Single-value
// facets are chips with one selectable; multi-value facets are chips that toggle, with "2 of 3
// selected" beneath; number and text facets are an input. Done and Cancel. Only allowed values can
// be chosen, a multi-value facet refuses more than its limit, and a required facet cannot be left empty.

export type EditorResult = { state: "value" | "unknown"; values: AnswerValue[] };

const REQUIRED_MSG = "Choose a value. This facet is required.";

export function ValueEditor({
  facet,
  current,
  onDone,
  onCancel,
}: {
  facet: Facet;
  current: ShownValue;
  onDone: (r: EditorResult) => void;
  onCancel: () => void;
}) {
  const isInput = facet.type === "number" || facet.type === "text";
  const startIds = hasValue(current)
    ? current.values.flatMap((v) => {
        if ("id" in v) {
          // match by id, then by label (an answer can carry a value id the list spells differently)
          const hit = facet.values.find((o) => o.id === v.id) ?? facet.values.find((o) => o.label === v.label);
          return hit ? [hit.id] : [];
        }
        return [];
      })
    : [];
  const startText = hasValue(current)
    ? current.values.map((v) => ("number" in v ? String(v.number) : "text" in v ? v.text : "")).join("")
    : "";

  const [selected, setSelected] = useState<number[]>(startIds);
  const [text, setText] = useState(startText);
  const [error, setError] = useState<string | null>(null);

  const max = facet.multi ? facet.maxValues : 1;

  const toggle = (id: number) => {
    setError(null);
    if (!facet.multi) {
      setSelected((s) => (s[0] === id ? [] : [id]));
      return;
    }
    if (selected.includes(id)) {
      setSelected(selected.filter((x) => x !== id));
      return;
    }
    // an exclusive value (e.g. "all-season") stands alone: picking it clears the others, and picking
    // another value clears it (catalog rule exclusive_with_other_values)
    const isExcl = (x: number) => facet.values.some((o) => o.id === x && o.exclusive);
    if (isExcl(id)) setSelected([id]);
    else {
      const rest = selected.filter((x) => !isExcl(x));
      if (max !== null && rest.length >= max) setError(`Choose up to ${max}.`);
      else setSelected([...rest, id]);
    }
  };

  const done = () => {
    if (isInput) {
      const t = text.trim();
      if (!t) {
        if (facet.required) return setError(REQUIRED_MSG);
        return onDone({ state: "unknown", values: [] });
      }
      if (facet.type === "number") {
        const n = Number(t);
        if (!Number.isFinite(n)) return setError("Enter a number.");
        return onDone({ state: "value", values: [{ number: n }] });
      }
      return onDone({ state: "value", values: [{ text: t }] });
    }
    if (selected.length === 0) {
      if (facet.required) return setError(REQUIRED_MSG);
      return onDone({ state: "unknown", values: [] });
    }
    // keep the facet's own order (ordinal facets read low to high)
    const values = facet.values.filter((o) => selected.includes(o.id)).map((o) => ({ id: o.id, label: o.label }));
    onDone({ state: "value", values });
  };

  return (
    <div className="ved">
      {isInput ? (
        <label className="ved-input-row">
          <input
            className={"ved-input" + (facet.type === "number" ? " is-number" : "") + (error ? " is-error" : "")}
            type={facet.type === "number" ? "number" : "text"}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setError(null);
            }}
            aria-label={facet.name}
            autoFocus
          />
          {facet.unit && <span className="muted">{facet.unit}</span>}
        </label>
      ) : (
        <>
          <div className="ved-chips" role="group" aria-label={facet.name}>
            {facet.values.map((o) => {
              const on = selected.includes(o.id);
              return (
                <button
                  type="button"
                  key={o.id}
                  className={"ved-chip" + (on ? " is-on" : "")}
                  aria-pressed={on}
                  title={o.definition ?? undefined}
                  onClick={() => toggle(o.id)}
                >
                  {o.label}
                </button>
              );
            })}
          </div>
          {facet.multi && (
            <span className="small muted">
              {max !== null ? `${selected.length} of ${max} selected` : `${selected.length} selected`}
            </span>
          )}
        </>
      )}
      {error && (
        <span className="small ved-error" role="alert">
          {error}
        </span>
      )}
      <div className="ved-buttons">
        <button type="button" className="btn btn-sm ved-done" onClick={done}>
          Done
        </button>
        <button type="button" className="btn btn-quiet btn-sm" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
