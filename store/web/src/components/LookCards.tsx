import { useEffect, useRef } from "react";
import { navigate } from "../router";
import { useStore } from "../store";
import type { Card, LookForYou, LookPlacement } from "../types";

// Looks for you in its three placements (Design PRD 5, engineering PRD V5): a compact row at the top of the PLP
// when it is the lead module, a card after every eighth product in the grid, and "More looks for you" on the
// product page. Every card logs look_card_view once when shown and look_card_tap when opened, with its placement.

function hrefOf(l: LookForYou) {
  return `/p/${encodeURIComponent((l.hero as Card).id)}?occasion=${encodeURIComponent(l.occasion)}`;
}

/** Logs look_card_view once, when at least half the card is on screen, and returns the ref and the tap handler. */
function useLookCard(l: LookForYou, placement: LookPlacement, position: number) {
  const { logEvent } = useStore();
  const seen = useRef(false);
  const ref = useRef<HTMLAnchorElement>(null);
  const hero = l.hero as Card;
  useEffect(() => {
    const el = ref.current;
    if (!el || seen.current) return;
    const log = () => {
      if (seen.current) return;
      seen.current = true;
      void logEvent("look_card_view", hero.id, { placement, occasion: l.occasion, position });
    };
    if (typeof IntersectionObserver === "undefined") {
      log();
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          log();
          io.disconnect();
        }
      },
      { threshold: 0.5 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [hero.id, l.occasion, placement, position, logEvent]);
  const open = (e: React.MouseEvent) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    void logEvent("look_card_tap", hero.id, { placement, occasion: l.occasion, position });
    navigate(hrefOf(l), { state: { surface: "look" } });
  };
  return { ref, open };
}

function Img({ card, className }: { card: Card | null | undefined; className: string }) {
  if (!card) return <span className={className} />;
  return card.image ? (
    <img className={className} src={card.image} alt="" loading="lazy" />
  ) : (
    <span className={className + " card__img--none"}>Photo unavailable</span>
  );
}

/** Top of the PLP, only when Looks for you is the lead module: a short row that keeps the picker line in view. */
export function TopLooks({ looks }: { looks: LookForYou[] }) {
  if (!looks.length) return null;
  return (
    <div className="lfy">
      <p className="label lfy__label">Looks for you</p>
      <div className="lfy__row">
        {looks.map((l, i) => (
          <CompactLook key={(l.hero as Card).id} look={l} placement="top_band" position={i + 1} />
        ))}
      </div>
    </div>
  );
}

function CompactLook({ look, placement, position }: { look: LookForYou; placement: LookPlacement; position: number }) {
  const { ref, open } = useLookCard(look, placement, position);
  return (
    <a ref={ref} className={"lfy-card" + (placement === "product_page" ? " lfy-card--pdp" : "")} href={hrefOf(look)} onClick={open}>
      <Img card={look.hero} className="lfy-card__hero" />
      <span className="lfy-card__text">
        <span className="lfy-card__title">{look.title}</span>
        <span className="lfy-card__sub">{look.sub}</span>
        <span className="lfy-card__thumbs">
          {[0, 1, 2].map((i) => (
            <Img key={i} card={look.thumbs[i]} className="lfy-card__thumb" />
          ))}
        </span>
      </span>
    </a>
  );
}

/** In the grid: two cells wide, one row high, the same ivory and type as the product cards. */
export function GridLook({ look, position }: { look: LookForYou; position: number }) {
  const { ref, open } = useLookCard(look, "grid", position);
  return (
    <a ref={ref} className="grid-look" href={hrefOf(look)} onClick={open}>
      <Img card={look.hero} className="grid-look__hero" />
      <span className="grid-look__text">
        <span className="label label--stone grid-look__label">A look for you</span>
        <span className="grid-look__title">{look.title}</span>
        <span className="grid-look__sub">{look.sub}</span>
        <span className="grid-look__thumbs">
          {[0, 1, 2].map((i) => (
            <Img key={i} card={look.thumbs[i]} className="grid-look__thumb" />
          ))}
        </span>
        <span className="label grid-look__cta">View the look</span>
      </span>
    </a>
  );
}

/** Below the accordions on the product page; never includes the product on the page. */
export function MoreLooks({ looks }: { looks: LookForYou[] }) {
  if (!looks.length) return null;
  return (
    <section className="more-looks">
      <h2 className="more-looks__title">More looks for you</h2>
      <div className="more-looks__list">
        {looks.map((l, i) => (
          <CompactLook key={(l.hero as Card).id} look={l} placement="product_page" position={i + 1} />
        ))}
      </div>
    </section>
  );
}
