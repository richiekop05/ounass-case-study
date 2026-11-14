import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { occasionPhrase, price } from "../format";
import { navigate } from "../router";
import { useStore } from "../store";
import type { Look, LookItem, LookSlot, Product } from "../types";

const FADE_MS = 230;

/** "Complete the look" on the PDP: one card per slot, swap in place, total and "Add look to bag". */
export function CompleteTheLook({
  product,
  occasion,
  onLook,
}: {
  product: Product;
  occasion: string | null;
  onLook?: (look: Look | null) => void;
}) {
  const { logEvent, addToBag, showToast } = useStore();
  const [look, setLook] = useState<Look | null | "loading">("loading");
  const [index, setIndex] = useState<Record<string, number>>({});
  const [fading, setFading] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState(false);
  const seen = useRef(false);

  useEffect(() => {
    let live = true;
    api.look(product.id, occasion).then(
      (l) => {
        if (!live) return;
        const shown = l.filled >= 1 && l.slots.length > 0 ? l : null;
        setLook(shown);
        onLook?.(l);
        if (shown && !seen.current) {
          seen.current = true;
          logEvent("look_view", product.id, { anchor: product.id, occasion: l.occasion, slots: shown.slots.length });
        }
      },
      () => {
        if (!live) return;
        setLook(null);
        onLook?.(null);
      },
    );
    return () => {
      live = false;
    };
  }, [product.id, occasion, logEvent, onLook]);

  if (look === "loading") return product.role === "none" ? null : <LookPlaceholder />;
  if (!look) return null;

  const at = (s: LookSlot) => Math.min(index[s.slot] ?? 0, s.items.length - 1);
  const shown = look.slots.map((s) => s.items[at(s)]);
  const total = shown.reduce((sum, i) => sum + i.price, 0);

  const step = (s: LookSlot, dir: 1 | -1) => {
    const from = at(s);
    const to = from + dir;
    if (to < 0 || to >= s.items.length || fading[s.slot]) return;
    setFading((f) => ({ ...f, [s.slot]: true }));
    window.setTimeout(() => {
      setIndex((ix) => ({ ...ix, [s.slot]: to }));
      setFading((f) => ({ ...f, [s.slot]: false }));
    }, FADE_MS);
    const ctx = (i: number) => ({ slot: s.slot, position: i + 1, surface: "look", anchor: product.id });
    // Forward: out goes the shown item, in comes the next. Back: the same pair in reverse.
    void logEvent("swap_out", s.items[from].id, ctx(from)).then(() => logEvent("swap_in", s.items[to].id, ctx(to)));
  };

  // The occasion row (engineering PRD V7): choosing one rebuilds the look in place. All cards fade out, the new
  // look (with its own alternates) fades in, swap positions reset, no reload or scroll. It becomes the session's
  // occasion, so the next listing page opens on its shortlist.
  const choose = (o: string) => {
    setEditing(false);
    if (o === look.occasion) return;
    void logEvent("occasion_select", product.id, { occasion: o, surface: "product_page", anchor: product.id });
    setFading(Object.fromEntries(look.slots.map((s) => [s.slot, true])));
    const wait = new Promise((r) => window.setTimeout(r, FADE_MS));
    Promise.all([api.look(product.id, o), wait]).then(
      ([l]) => {
        if (l.filled >= 1 && l.slots.length > 0) {
          setLook(l);
          setIndex({});
          onLook?.(l);
          const q = new URLSearchParams(window.location.search);
          q.set("occasion", o);
          window.history.replaceState(window.history.state, "", window.location.pathname + "?" + q.toString());
        }
        setFading({});
      },
      () => setFading({}),
    );
  };
  const options = look.occasionOptions ?? [];
  const asking = options.length > 0 && (!look.occasion || editing);

  const open = (item: LookItem) => {
    const q = look.occasion ? "?occasion=" + encodeURIComponent(look.occasion) : "";
    navigate("/p/" + encodeURIComponent(item.id) + q, { state: { surface: "look" } });
  };

  const many = look.filled > 1 && look.slots.length > 1;
  const subline =
    "Built around this piece" + (look.occasionLabel ? ` for ${occasionPhrase(look.occasionLabel)}` : "");

  return (
    <section className="look" aria-label={look.title ?? "Complete the look"}>
      {asking ? (
        <div className="occ-row">
          <span className="occ-row__ask">
            <span className="label occ-row__label">Wearing it to…?</span>
            <span className="occ-row__hint">Choose one to restyle the look</span>
          </span>
          {options.map((o) => {
            const on = o.value === look.occasion;
            return (
              <button
                key={o.value}
                className={"occ-pill" + (on ? " is-on" : "")}
                aria-pressed={on}
                onClick={() => choose(o.value)}
              >
                <span className="occ-pill__dot" aria-hidden="true" />
                {o.label}
              </button>
            );
          })}
        </div>
      ) : (
        options.length > 0 &&
        look.occasion && (
          <div className="occ-row occ-row--set">
            <span className="label occ-row__label">Styling for:</span>
            <span>{look.occasionLabel ? occasionPhrase(look.occasionLabel).replace(/^(an?|the) /, "") : look.occasion}</span>
            <span className="occ-row__sep">·</span>
            <button className="occ-row__change" onClick={() => setEditing(true)}>
              Change
            </button>
          </div>
        )
      )}
      <h2 className="look__title">{look.title ?? (many ? "Complete the look" : "Pairs well with")}</h2>
      <p className="look__sub">{subline}</p>

      <div className="look__row">
        {look.slots.map((s) => {
          const i = at(s);
          const item = s.items[i];
          const n = s.items.length;
          const last = i === n - 1;
          const q = look.occasion ? "?occasion=" + encodeURIComponent(look.occasion) : "";
          return (
            <div className="slot" key={s.slot}>
              <div className="slot__top">
                <span className="label slot__label">{s.label}</span>
                {n > 1 && (
                  <span className="slot__count">
                    {i + 1} of {n}
                  </span>
                )}
              </div>
              <div className="slot__body">
              <a
                className={"slot__card" + (fading[s.slot] ? " is-fading" : "")}
                href={"/p/" + encodeURIComponent(item.id) + q}
                onClick={(e) => {
                  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
                  e.preventDefault();
                  open(item);
                }}
              >
                {item.image ? (
                  <img className="card__img" src={item.image} alt="" loading="lazy" />
                ) : (
                  <div className="card__img card__img--none">Photo unavailable</div>
                )}
                <span className="label card__brand slot__brand">{item.brand}</span>
                <span className="card__name">{item.name}</span>
                <span className="card__price">{price(item.price)}</span>
                {item.reason && (
                  <span className="pill pill--reason" title={item.reason}>
                    {item.reason}
                  </span>
                )}
              </a>
              {n > 1 && (
                <div className="slot__nav">
                  <button className="slot__back" onClick={() => step(s, -1)} disabled={i === 0} aria-label="Previous option">
                    ←
                  </button>
                  <button className="slot__swap" onClick={() => step(s, 1)} disabled={last}>
                    {last ? "No more options" : <>Swap <span aria-hidden="true">⇄</span></>}
                  </button>
                </div>
              )}
              </div>
            </div>
          );
        })}
      </div>

      {many && (
        <div className="look__foot">
          <div className="look__sum">
            <span className="label label--stone">The look · {shown.length} pieces</span>
            <span className="look__total">{price(total)}</span>
          </div>
          <button
            className="btn-secondary"
            onClick={() => {
              addToBag(shown.length);
              showToast("Look added to bag");
              void logEvent("add_look_to_bag", product.id, {
                anchor: product.id,
                items: shown.map((i) => i.id),
                occasion: look.occasion,
              });
            }}
          >
            Add look to bag
          </button>
        </div>
      )}
    </section>
  );
}

function LookPlaceholder() {
  return (
    <section className="look" aria-hidden="true">
      <span className="placeholder placeholder--title" />
      <span className="placeholder placeholder--line look__sub-ph" />
      <div className="look__row">
        {[0, 1, 2].map((k) => (
          <div className="slot" key={k}>
            <span className="placeholder placeholder--line" style={{ width: "40%" }} />
            <div className="card__img placeholder slot__ph-img" />
            <span className="placeholder placeholder--line" style={{ width: "60%" }} />
            <span className="placeholder placeholder--line" style={{ width: "85%" }} />
          </div>
        ))}
      </div>
    </section>
  );
}

/** High intent: a compact "Pairs well with" rail below the accordions, one item per slot, no swap, no reasons. */
export function PairsRail({
  product,
  occasion,
  onLook,
}: {
  product: Product;
  occasion: string | null;
  onLook?: (look: Look | null) => void;
}) {
  const [look, setLook] = useState<Look | null | "loading">("loading");
  useEffect(() => {
    let live = true;
    api.look(product.id, occasion).then(
      (l) => {
        if (!live) return;
        setLook(l.filled >= 1 && l.slots.length > 0 ? l : null);
        onLook?.(l);
      },
      () => {
        if (!live) return;
        setLook(null);
        onLook?.(null);
      },
    );
    return () => {
      live = false;
    };
  }, [product.id, occasion, onLook]);

  if (look === "loading")
    return product.role === "none" ? null : (
      <section className="rail" aria-hidden="true">
        <span className="placeholder placeholder--line rail__ph-title" />
        <div className="rail__row">
          {[0, 1, 2].map((k) => (
            <div className="rail__card" key={k}>
              <div className="card__img placeholder" />
            </div>
          ))}
        </div>
      </section>
    );
  if (!look) return null;
  const q = look.occasion ? "?occasion=" + encodeURIComponent(look.occasion) : "";
  return (
    <section className="rail">
      <h2 className="label rail__title">{look.title ?? "Pairs well with"}</h2>
      <div className="rail__row">
        {look.slots.map((s) => {
          const item = s.items[0];
          const href = "/p/" + encodeURIComponent(item.id) + q;
          return (
            <a
              key={s.slot}
              className="rail__card"
              href={href}
              onClick={(e) => {
                if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
                e.preventDefault();
                navigate(href, { state: { surface: "look" } });
              }}
            >
              {item.image ? (
                <img className="card__img" src={item.image} alt="" loading="lazy" />
              ) : (
                <div className="card__img card__img--none">Photo unavailable</div>
              )}
              <span className="label card__brand rail__brand">{item.brand}</span>
              <span className="card__price">{price(item.price)}</span>
            </a>
          );
        })}
      </div>
    </section>
  );
}
