import { useEffect, useRef, useState, type MutableRefObject } from "react";
import type { Photo, TextBlock } from "../productTypes";
import "./EvidencePanel.css";

// Evidence panel (Design PRD 12): one large 3:4 photo with numbered thumbnails beneath (click the
// photo to enlarge), then one block per text field with its label, then a link to the product on
// Ounass. A quote passed in `highlight` is marked in the text and scrolled into view. Props only:
// the panel fetches nothing, so the Live tag page can reuse it.

export function EvidencePanel({
  title,
  url,
  photos,
  text,
  highlight = null,
  highlightSeq = 0,
}: {
  title: string;
  url: string | null;
  photos: Photo[];
  text: TextBlock[];
  /** The quote to mark (first occurrence, case-insensitive). */
  highlight?: string | null;
  /** Bump this to scroll to the same quote again. */
  highlightSeq?: number;
}) {
  const [current, setCurrent] = useState(0);
  const [failed, setFailed] = useState<Set<number>>(new Set());
  const [enlarged, setEnlarged] = useState(false);
  const markRef = useRef<HTMLElement | null>(null);

  const photo = photos[current] ?? null;
  const usable = (p: Photo | null) => !!p && !!p.url && !failed.has(p.n);
  const fail = (n: number) => setFailed((s) => new Set(s).add(n));

  useEffect(() => {
    markRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [highlight, highlightSeq]);

  useEffect(() => {
    if (!enlarged) return;
    const close = (e: KeyboardEvent) => e.key === "Escape" && setEnlarged(false);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [enlarged]);

  // the first block that contains the quote gets the mark
  const needle = highlight?.trim().toLowerCase() || "";
  const hitBlock = needle ? text.findIndex((b) => b.text.toLowerCase().includes(needle)) : -1;

  return (
    <div className="evidence">
      <div className="evidence-gallery">
        {usable(photo) ? (
          <button type="button" className="evidence-photo" title="Enlarge photo" onClick={() => setEnlarged(true)}>
            <img src={photo!.url!} alt={title} onError={() => fail(photo!.n)} />
          </button>
        ) : (
          <div className="evidence-photo is-empty">Photo unavailable</div>
        )}
        {photos.length > 1 && (
          <div className="evidence-thumbs">
            {photos.map((p, i) => (
              <button
                type="button"
                key={p.n}
                className={"evidence-thumb" + (i === current ? " is-current" : "")}
                title={`Photo ${p.n}`}
                aria-label={`Show photo ${p.n}`}
                aria-pressed={i === current}
                onClick={() => setCurrent(i)}
              >
                {usable(p) && <img src={p.url!} alt="" onError={() => fail(p.n)} />}
                <span className="evidence-thumb-n">{p.n}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="evidence-text">
        {text.map((b, i) => (
          <div key={b.label + i} className="evidence-block">
            <span className="label">{b.label}</span>
            <p className="evidence-p">
              {i === hitBlock ? <Marked text={b.text} needle={needle} markRef={markRef} /> : b.text}
            </p>
          </div>
        ))}
        {url && (
          <a className="evidence-link small" href={url} target="_blank" rel="noopener noreferrer">
            Open on the Ounass website ↗
          </a>
        )}
      </div>

      {enlarged && usable(photo) && (
        <div className="evidence-lightbox" role="dialog" aria-label="Enlarged photo" onClick={() => setEnlarged(false)}>
          <img src={photo!.url!} alt={title} />
        </div>
      )}
    </div>
  );
}

function Marked({
  text,
  needle,
  markRef,
}: {
  text: string;
  needle: string;
  markRef: MutableRefObject<HTMLElement | null>;
}) {
  const at = text.toLowerCase().indexOf(needle);
  if (at < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, at)}
      <mark ref={markRef} className="evidence-mark">
        {text.slice(at, at + needle.length)}
      </mark>
      {text.slice(at + needle.length)}
    </>
  );
}
