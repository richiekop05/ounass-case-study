import { useEffect, useState } from "react";
import { api } from "../api";
import { isLow, type CategoryDecision, type CategoryInfo } from "../productTypes";
import { ConfidenceBadge } from "./ConfidenceBadge";
import "./CategoryCard.css";

// Category card (Design PRD 12): the full path as breadcrumbs with the last level in weight 600,
// the confidence badge on the right, the runner-up as a quiet "Use instead: …" link, and Change,
// which opens a search box over all categories. Approve shows only while the category is in review.

type Result = { id: string; path: string[] };

export function CategoryCard({
  category,
  decision,
  onDecide,
  busy = false,
  readOnly = false,
}: {
  category: CategoryInfo;
  decision: CategoryDecision | undefined;
  onDecide: (d: CategoryDecision) => void;
  /** a category request or re-tag is running: no new decision until it ends */
  busy?: boolean;
  /** an approved product: the category cannot change until it is sent back to review */
  readOnly?: boolean;
}) {
  const [searching, setSearching] = useState(false);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Result[]>([]);
  const [error, setError] = useState<string | null>(null);

  // search over all categories, debounced
  useEffect(() => {
    if (!searching) return;
    let live = true;
    const t = window.setTimeout(() => {
      api
        .get<{ categories: Result[] }>("/api/categories?q=" + encodeURIComponent(q.trim()))
        .then((r) => {
          if (!live) return;
          setResults(r.categories);
          setError(null);
        })
        .catch((e: Error) => live && setError(e.message));
    }, 250);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [q, searching]);

  const path = decision?.kind === "changed" ? decision.path : category.path;
  const decided = !!decision;
  const choose = (r: Result) => {
    onDecide(r.id === category.id ? { kind: "approved" } : { kind: "changed", id: r.id, path: r.path });
    setSearching(false);
    setQ("");
  };
  const barClass = decided ? " is-ok" : category.flagged ? " is-flag" : "";

  return (
    <section className={"cat-card" + barClass} id="category-card" aria-label="Category">
      <div className="cat-head">
        <span className="label">Category</span>
        {decided && (
          <span className="decided-pill">✓ {decision!.kind === "approved" ? "Approved" : "Changed"}</span>
        )}
        <span className="cat-badge">
          <ConfidenceBadge value={category.confidence} low={isLow(category.confidence, category.level)} />
        </span>
      </div>
      <div className="cat-main">
        <div className="cat-crumbs">
          {path.map((p, i) => (
            <span key={i} className="cat-crumb">
              <span className={i === path.length - 1 ? "cat-crumb-last" : undefined}>{p}</span>
              {i < path.length - 1 && <span className="cat-sep" aria-hidden="true">›</span>}
            </span>
          ))}
        </div>
        {!readOnly && (
          <div className="cat-actions">
            {category.flagged && !decided && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={busy}
                onClick={() => onDecide({ kind: "approved" })}
              >
                Approve
              </button>
            )}
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={busy}
              onClick={() => setSearching((s) => !s)}
            >
              Change
            </button>
          </div>
        )}
      </div>
      {category.runnerUp && decision?.kind !== "changed" && !readOnly && (
        <button type="button" className="cat-alt small" disabled={busy} onClick={() => choose(category.runnerUp!)}>
          Use instead: <span className="cat-alt-path">{category.runnerUp.path.join(" › ")}</span>
        </button>
      )}
      {searching && !readOnly && !busy && (
        <div className="cat-search">
          <input
            className="cat-search-input"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search all categories"
            aria-label="Search all categories"
            autoFocus
          />
          {error && <span className="small cat-error">{error}</span>}
          <div className="cat-results">
            {results.map((r) => (
              <button type="button" key={r.id} className="cat-result" onClick={() => choose(r)}>
                {r.path.join(" › ")}
              </button>
            ))}
            {!error && results.length === 0 && q.trim() && <span className="small muted cat-none">No category matches.</span>}
          </div>
          <button type="button" className="btn btn-quiet btn-sm cat-cancel" onClick={() => setSearching(false)}>
            Cancel
          </button>
        </div>
      )}
    </section>
  );
}
