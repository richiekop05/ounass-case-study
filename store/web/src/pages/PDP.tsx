import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { FACET_IDS } from "../filters";
import { CompleteTheLook, PairsRail } from "../components/CompleteTheLook";
import { MoreLooks } from "../components/LookCards";
import { DEPT_LABEL, price } from "../format";
import { linkClick, navigate, openedFrom } from "../router";
import { useStore } from "../store";
import type { Intent, Look, LookForYou, Product } from "../types";


const DELIVERY = "Delivered in one to two days across the UAE. Returns are collected from your door within 14 days.";

export function PDP({ id, occasion, intent }: { id: string; occasion: string | null; intent: Intent | null }) {
  const { logEvent, setProduct, addToBag, showToast } = useStore();
  const [product, setLocal] = useState<Product | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState<string | null>(null);
  const [, setLook] = useState<Look | null>(null);
  const [moreLooks, setMoreLooks] = useState<LookForYou[]>([]);

  // More looks for you: eligible low-intent shoppers only; never includes this product (engineering PRD V5).
  useEffect(() => {
    if (intent !== "low") return;
    let live = true;
    setMoreLooks([]);
    api.looksForYou("all", 3, id).then(
      (r) => live && setMoreLooks(r.looks.filter((l) => l.hero)),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [id, intent]);
  const viewed = useRef(false);

  useEffect(() => {
    if (!intent) return;
    let live = true;
    api.product(id).then(
      (p) => {
        if (!live) return;
        setLocal(p);
        setProduct(p);
        if (!viewed.current) {
          viewed.current = true;
          logEvent("view", p.id, { surface: openedFrom() });
        }
      },
      (e: Error) => live && setError(e.message),
    );
    return () => {
      live = false;
      setProduct(null);
    };
  }, [id, intent, logEvent, setProduct]);

  if (error)
    return (
      <div className="container pdp">
        <p className="message">{error}</p>
        <a className="back-link" href="/women" onClick={linkClick}>
          ← Women
        </a>
      </div>
    );

  if (!product || !intent) return <PdpPlaceholder />;
  const high = intent === "high";

  const deptHref = "/" + product.department;

  const tapTag = (facet: string, value: string) => {
    logEvent("filter_apply", product.id, { facet, value, surface: "tag" });
    navigate(`${deptHref}?${new URLSearchParams({ [facet]: value })}`);
  };

  return (
    <div className="container pdp">
      <a className="back-link" href={deptHref} onClick={linkClick}>
        ← {DEPT_LABEL[product.department]}
      </a>
      <div className="pdp__cols">
        <Gallery images={product.images} name={product.name} />

        <div className="pdp__info">
          <span className="label pdp__brand">{product.brand}</span>
          <h1 className="pdp__name">{product.name}</h1>
          <p className="pdp__price">{price(product.price, product.currency)}</p>

          {product.sizes.length > 0 && (
            <div className="pdp__sizes">
              <span className="label label--stone">Size</span>
              <div className="sizes" role="radiogroup" aria-label="Size">
                {product.sizes.map((s) => (
                  <button
                    key={s}
                    role="radio"
                    aria-checked={size === s}
                    className={"size" + (size === s ? " is-on" : "") + (s.length > 3 ? " size--wide" : "")}
                    onClick={() => {
                      if (size === s) return;
                      setSize(s);
                      logEvent("size_select", product.id, { size: s });
                    }}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          <button
            className="btn-primary pdp__add"
            onClick={() => {
              addToBag(1);
              showToast("Added to bag");
              logEvent("add_to_bag", product.id, size ? { size } : {});
            }}
          >
            Add to bag
          </button>

          {!high && (
            <CompleteTheLook key={product.id + (occasion ?? "")} product={product} occasion={occasion} onLook={setLook} />
          )}

          {!high && product.tags.length > 0 && (
            <section className="about">
              <h2 className="label label--stone">About this piece</h2>
              <div className="about__pills">
                {product.tags.map((t) =>
                  FACET_IDS.has(t.facet) ? (
                    <a
                      key={t.facet + t.value}
                      className="pill pill--tag"
                      href={`${deptHref}?${new URLSearchParams({ [t.facet]: t.value })}`}
                      onClick={(e) => {
                        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
                        e.preventDefault();
                        tapTag(t.facet, t.value);
                      }}
                    >
                      {t.label}
                    </a>
                  ) : (
                    <span key={t.facet + t.value} className="pill pill--tag pill--static">
                      {t.label}
                    </span>
                  ),
                )}
              </div>
            </section>
          )}

          <div className="accordions">
            {product.description && (
              <Accordion title="Description" open>
                <p className="body-text">{product.description}</p>
              </Accordion>
            )}
            <DetailsAccordion details={product.details} />
            <Accordion title="Delivery and returns">
              <p className="body-text">{DELIVERY}</p>
            </Accordion>
          </div>

          {high && <PairsRail key={product.id} product={product} occasion={occasion} onLook={setLook} />}
          {!high && <MoreLooks looks={moreLooks} />}
        </div>
      </div>
    </div>
  );
}

function Gallery({ images, name }: { images: string[]; name: string }) {
  if (images.length === 0)
    return (
      <div className="gallery">
        <div className="gallery__none">Photo unavailable</div>
      </div>
    );
  return (
    <div className="gallery">
      <div className="gallery__strip" tabIndex={0} aria-label="Product images">
        {images.map((src, i) => (
          <img key={i} className="gallery__img" src={src} alt={i === 0 ? name : ""} loading={i === 0 ? "eager" : "lazy"} />
        ))}
      </div>
      {images.length > 1 && <p className="gallery__caption">Swipe for {images.length} views</p>}
    </div>
  );
}

function Accordion({ title, open, children }: { title: string; open?: boolean; children: ReactNode }) {
  return (
    <details className="accordion" open={open}>
      <summary className="accordion__head">
        <span className="label">{title}</span>
        <span className="accordion__mark" aria-hidden="true" />
      </summary>
      <div className="accordion__body">{children}</div>
    </details>
  );
}

function DetailsAccordion({ details }: { details: unknown }) {
  let body: ReactNode = null;
  if (Array.isArray(details)) {
    const items = details.map((d) => (typeof d === "string" ? d : JSON.stringify(d))).filter((d) => d.trim());
    if (items.length)
      body = (
        <ul className="body-list">
          {items.map((d, i) => (
            <li key={i}>{d}</li>
          ))}
        </ul>
      );
  } else if (details && typeof details === "object") {
    const rows = Object.entries(details as Record<string, unknown>).filter(
      ([, v]) => v !== null && v !== undefined && String(v).trim() !== "",
    );
    if (rows.length)
      body = (
        <ul className="body-list body-list--plain">
          {rows.map(([k, v]) => (
            <li key={k}>
              <span className="body-list__key">{k.replace(/_/g, " ")}:</span>{" "}
              {Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : String(v)}
            </li>
          ))}
        </ul>
      );
  } else if (typeof details === "string" && details.trim()) {
    body = <p className="body-text">{details}</p>;
  }
  if (!body) return null;
  return <Accordion title="Details">{body}</Accordion>;
}

function PdpPlaceholder() {
  return (
    <div className="container pdp" aria-hidden="true">
      <span className="back-link placeholder placeholder--line" style={{ width: 80 }} />
      <div className="pdp__cols">
        <div className="gallery">
          <div className="gallery__img placeholder" />
        </div>
        <div className="pdp__info">
          <span className="placeholder placeholder--line" style={{ width: "30%" }} />
          <span className="placeholder placeholder--block" style={{ width: "80%" }} />
          <span className="placeholder placeholder--line" style={{ width: "20%" }} />
          <span className="placeholder placeholder--button" />
        </div>
      </div>
    </div>
  );
}
