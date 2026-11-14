import { linkClick } from "../router";
import { useStore } from "../store";
import type { Dept } from "../types";

const NAV: { dept: Dept; label: string }[] = [
  { dept: "women", label: "Women" },
  { dept: "men", label: "Men" },
  { dept: "all", label: "Shop all" },
];

export function BagIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1" aria-hidden="true">
      <rect x="2.5" y="6" width="13" height="10.5" />
      <path d="M6 6V4.5a3 3 0 0 1 6 0V6" />
    </svg>
  );
}

export function StoreHeader({ active }: { active: Dept | null }) {
  const { bag, product } = useStore();
  const current = active ?? product?.department ?? null;
  return (
    <header className="store-header">
      <div className="store-header__top container">
        <span />
        <a className="wordmark" href="/" onClick={linkClick}>
          Richie's
        </a>
        <span className="store-header__bag" aria-label={`Bag, ${bag} pieces`}>
          <BagIcon />
          <span className="store-header__count">{bag}</span>
        </span>
      </div>
      <nav className="store-nav">
        {NAV.map((n) => (
          <a
            key={n.dept}
            href={"/" + n.dept}
            onClick={linkClick}
            className={"store-nav__item" + (current === n.dept ? " is-active" : "")}
          >
            {n.label}
          </a>
        ))}
      </nav>
    </header>
  );
}
