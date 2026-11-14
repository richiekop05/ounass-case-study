import { price } from "../format";
import { navigate } from "../router";
import { useStore } from "../store";
import type { Card } from "../types";

/** A product card: the PLP grid (3:4) or a shortlist hero (4:5, with reason pills). */
export function ProductCard({
  card,
  variant = "grid",
  occasion,
  reasons,
}: {
  card: Card;
  variant?: "grid" | "hero";
  occasion?: string | null;
  reasons?: string[];
}) {
  const { saved, toggleSaved, logEvent } = useStore();
  const isSaved = saved.has(card.id);
  const surface = variant === "hero" ? "shortlist" : "grid";
  const href = "/p/" + encodeURIComponent(card.id) + (occasion ? "?occasion=" + encodeURIComponent(occasion) : "");
  const open = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    navigate(href, { state: { surface } });
  };
  return (
    <article className={"card" + (variant === "hero" ? " card--hero" : "")}>
      <div className="card__media">
        <a href={href} onClick={open} tabIndex={-1} aria-hidden="true">
          {card.image ? (
            <img className="card__img" src={card.image} alt="" loading="lazy" />
          ) : (
            <div className="card__img card__img--none">Photo unavailable</div>
          )}
        </a>
        <button
          className={"heart" + (isSaved ? " is-on" : "")}
          aria-pressed={isSaved}
          aria-label={isSaved ? "Remove from saved" : "Save"}
          onClick={() => {
            if (toggleSaved(card.id)) logEvent("save", card.id, { surface, ...(occasion ? { occasion } : {}) });
          }}
        >
          {isSaved ? "♥" : "♡"}
        </button>
      </div>
      <a href={href} onClick={open} className="card__text">
        <span className="label card__brand">{card.brand}</span>
        <span className="card__name">{card.name}</span>
        <span className="card__price">{price(card.price)}</span>
      </a>
      {reasons && reasons.length > 0 && (
        <div className="reasons">
          {reasons.slice(0, 3).map((r) => (
            <span key={r} className="pill pill--reason">
              {r}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}

export function CardPlaceholder({ variant = "grid" }: { variant?: "grid" | "hero" }) {
  return (
    <div className={"card" + (variant === "hero" ? " card--hero" : "")} aria-hidden="true">
      <div className="card__media">
        <div className="card__img placeholder" />
      </div>
      <div className="card__text">
        <span className="placeholder placeholder--line" style={{ width: "40%" }} />
        <span className="placeholder placeholder--line" style={{ width: "80%" }} />
        <span className="placeholder placeholder--line" style={{ width: "30%" }} />
      </div>
    </div>
  );
}
