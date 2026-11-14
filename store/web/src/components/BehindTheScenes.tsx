import { useEffect, useRef } from "react";
import { eventLine, eventMeta } from "../format";
import { useStore } from "../store";

const DIMENSIONS = ["Aesthetic", "Colour", "Formality", "Coverage"];

// Demo chrome: the right-edge tab and the dark drawer that shows what the store knows.
export function BehindTheScenes() {
  const { state, refreshState, product, btsOpen: open, setBtsOpen: setOpen } = useStore();
  const panel = useRef<HTMLElement>(null);

  useEffect(() => {
    if (open) refreshState();
  }, [open, refreshState]);

  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const outside = (e: MouseEvent) => {
      if (panel.current && !panel.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", esc);
    document.addEventListener("mousedown", outside);
    return () => {
      document.removeEventListener("keydown", esc);
      document.removeEventListener("mousedown", outside);
    };
  }, [open, setOpen]);

  const intent = state?.intent;

  return (
    <>
      <button className="bts-tab" onClick={() => setOpen(true)} aria-expanded={open}>
        Behind the scenes
      </button>
      <aside ref={panel} className={"bts" + (open ? " is-open" : "")} aria-hidden={!open} aria-label="Behind the scenes">
        <div className="bts__head">
          <span className="bts__title">Behind the scenes</span>
          <button className="bts__close" onClick={() => setOpen(false)}>
            Close
          </button>
        </div>

        <section className="bts__section">
          <h3 className="bts__label">Intent</h3>
          <div className="bts__intent">{intent ? intent.state.toUpperCase() : "…"}</div>
          {intent && <p className="bts__muted">{intent.reason}</p>}
          {intent?.pending && intent.pending !== intent.state && (
            <>
              <p className="bts__highlight">Next page loads as {intent.pending} intent</p>
              {intent.pendingReason && <p className="bts__dim">{intent.pendingReason}</p>}
            </>
          )}
        </section>

        <section className="bts__section">
          <h3 className="bts__label">Taste profile</h3>
          {DIMENSIONS.map((d) => {
            const rows = state?.profile?.find((p) => p.label === d)?.rows ?? [];
            return (
              <div className="bts__dimension" key={d}>
                <div className="bts__row bts__row--plain">
                  <span>{d}</span>
                  {rows.length === 0 && <span className="bts__dim">No signal yet</span>}
                </div>
                {rows.slice(0, 3).map((r) => {
                  const pct = Math.round(Math.max(0, Math.min(1, r.share)) * 100);
                  return (
                    <div className="bts__bar-row" key={r.label}>
                      <span className="bts__muted bts__bar-label">{r.label}</span>
                      <span className="bts__bar" aria-hidden="true">
                        <span className="bts__bar-fill" style={{ width: pct + "%" }} />
                      </span>
                      <span className="bts__pct">{pct}%</span>
                    </div>
                  );
                })}
              </div>
            );
          })}
          <p className="bts__dim bts__note">Starting history is simulated</p>
        </section>

        {state?.looks && (
          <section className="bts__section">
            <h3 className="bts__label">Looks for you</h3>
            <div className="bts__intent bts__intent--sm">
              {{ eligible: "ELIGIBLE", not_yet: "NOT YET", never: "NEVER" }[state.looks.state]}
            </div>
            <p className="bts__muted">{state.looks.reason}</p>
            {state.looks.state === "eligible" && state.looks.code !== "taste_profile" && (
              <p className="bts__dim">
                {state.looks.count} positive {state.looks.count === 1 ? "signal" : "signals"} · threshold {state.looks.threshold}
              </p>
            )}
            {state.looks.pending && (
              <p className="bts__highlight">Threshold reached. First look card appears on the next page load</p>
            )}
            {state.looks.since && <p className="bts__dim">{state.looks.since}</p>}
            <p className="bts__muted bts__lead">
              Lead module: {{ looks: "Looks for you", occasion_picker: "occasion picker", none: "none" }[state.lead.module]}
            </p>
            <p className="bts__dim">{state.lead.reason}</p>
          </section>
        )}

        <section className="bts__section">
          <h3 className="bts__label">Last five events</h3>
          {state && state.events.length === 0 && <p className="bts__dim">Nothing yet</p>}
          <ol className="bts__events">
            {state?.events.map((e, i) => (
              <li key={i} className="bts__event">
                <span>{eventLine(e)}</span>
                {e.effect && <span className="bts__highlight bts__effect">{e.effect}</span>}
                <span className="bts__dim">{eventMeta(e)}</span>
              </li>
            ))}
          </ol>
        </section>

        {product && (
          <section className="bts__section">
            <h3 className="bts__label">
              Product tags {product.sample && <span className="bts__badge">Sample</span>}
            </h3>
            {product.tags.map((t) => (
              <div className="bts__row bts__tag" key={t.facet + t.value}>
                <span className="bts__muted">{t.facetLabel}</span>
                <span>{t.label}</span>
                <span className="bts__conf">
                  {!product.sample && t.confidence !== null ? t.confidence.toFixed(2) : ""}
                </span>
              </div>
            ))}
            {product.tags.length === 0 && <p className="bts__dim">No tags</p>}
            {product.sample && (
              <p className="bts__dim bts__note">Loaded as sample data; tagged by the tagger, not reviewed</p>
            )}
          </section>
        )}
      </aside>
    </>
  );
}
