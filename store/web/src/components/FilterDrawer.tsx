import { useEffect, useState } from "react";
import { api, type Filters } from "../api";
import type { Dept, Facet } from "../types";

// Ivory panel on the right over a light scrim: tick values for one facet, then "Show {n} pieces".
export function FilterDrawer({
  facet,
  dept,
  filters,
  onApply,
  onClose,
}: {
  facet: Facet;
  dept: Dept;
  filters: Filters;
  onApply: (values: string[]) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<string[]>(filters[facet.id] || []);
  const [count, setCount] = useState<number | null>(null);

  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", esc);
    document.body.classList.add("no-scroll");
    return () => {
      document.removeEventListener("keydown", esc);
      document.body.classList.remove("no-scroll");
    };
  }, [onClose]);

  useEffect(() => {
    let live = true;
    const next = { ...filters, [facet.id]: draft };
    if (!draft.length) delete next[facet.id];
    const t = window.setTimeout(() => {
      api.products(dept, next).then(
        (r) => live && setCount(r.count),
        () => undefined,
      );
    }, 150);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [draft, dept, facet.id, filters]);

  const toggle = (v: string) => setDraft((d) => (d.includes(v) ? d.filter((x) => x !== v) : [...d, v]));

  return (
    <div className="drawer-layer">
      <div className="scrim" onClick={onClose} />
      <aside className="filter-drawer" role="dialog" aria-label={facet.label}>
        <div className="filter-drawer__head">
          <h2 className="filter-drawer__title">{facet.label}</h2>
          <button className="link-button" onClick={onClose}>
            Close
          </button>
        </div>
        <ul className="filter-drawer__list">
          {facet.values.map((v) => (
            <li key={v.value}>
              <label className="check-row">
                <input type="checkbox" checked={draft.includes(v.value)} onChange={() => toggle(v.value)} />
                <span className="check-row__box" aria-hidden="true" />
                <span className="check-row__label">{v.label}</span>
                <span className="check-row__count">{v.count}</span>
              </label>
            </li>
          ))}
          {facet.values.length === 0 && <li className="muted">Nothing to filter by here yet.</li>}
        </ul>
        <div className="filter-drawer__foot">
          <button className="btn-primary" onClick={() => onApply(draft)}>
            {count === null ? "Show pieces" : `Show ${count} ${count === 1 ? "piece" : "pieces"}`}
          </button>
        </div>
      </aside>
    </div>
  );
}
