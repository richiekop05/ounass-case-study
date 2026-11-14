import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { navigate } from "../router";
import { useStore, type Audience } from "../store";
import type { Dept, LookForYou, Occasion, Refine, Shortlist } from "../types";
import { TopLooks } from "./LookCards";
import { CardPlaceholder, ProductCard } from "./ProductCard";

// The PLP feature band (low intent only). It shows ONE lead module, decided by the server when the page loaded
// (engineering PRD V5): "show when the store knows, ask when it does not".
//  * looks: the Looks for you row. For a shopper whose taste was built this session the occasion picker sits
//    under it, collapsed to one line that opens in place; with a long history (Layla) the row stands alone and
//    occasions are reached from the product page (README V7)
//  * occasion_picker: the picker (the occasion in play suggested first), or the "Who is it for?" opener first
//  * a chosen occasion replaces the band with its shortlist. The PLP grid stays below.

const REFINES: { key: keyof Refine; label: string }[] = [
  { key: "budget", label: "Under AED 3,000" },
  { key: "neutral", label: "Neutrals" },
  { key: "covered", label: "More covered" },
];

const AUDIENCE_WORD: Record<Audience, string> = { me: "yourself", her: "her", him: "him" };

export function FeatureBand({
  dept,
  occasion,
  setOccasion,
  topLooks,
}: {
  dept: Dept;
  occasion: string | null;
  setOccasion: (o: string | null) => void;
  /** The first looks from Looks for you, shown only when it is the lead module; null while loading. */
  topLooks: LookForYou[] | null;
}) {
  const { persona, audience, setAudience, state } = useStore();
  const lead = state?.lead;
  const asksWho = persona?.department === "all";
  const [pickerOpen, setPickerOpen] = useState(false);

  if (!lead || lead.module === "none") return null;

  const opener = (
    <Opener
      onAnswer={(a) => {
        setAudience(a);
        navigate(a === "her" ? "/women" : "/men");
      }}
    />
  );
  const picker = (
    <Picker
      dept={dept}
      onPick={setOccasion}
      shoppingFor={asksWho && audience ? AUDIENCE_WORD[audience] : null}
      onChangeAudience={() => setAudience(null)}
    />
  );

  let body;
  if (occasion) {
    body = <ShortlistView key={occasion + dept} occasion={occasion} dept={dept} onChange={() => setOccasion(null)} />;
  } else if (lead.module === "looks" && topLooks === null) {
    body = <div className="lfy-ph" aria-hidden="true" />;
  } else if (lead.module === "looks" && topLooks && topLooks.length > 0) {
    body = (
      <>
        <TopLooks looks={topLooks} />
        {!lead.occasionToggle ? null : pickerOpen ? (
          asksWho && !audience ? opener : picker
        ) : (
          <div className="band__toggle">
            <button className="occasion-toggle" onClick={() => setPickerOpen(true)}>
              Shopping for an occasion? <span aria-hidden="true">↓</span>
            </button>
          </div>
        )}
      </>
    );
  } else if (lead.showOpener && !audience) {
    body = opener;
  } else {
    body = picker;
  }
  // Looks alone (README V7): nothing sits under the row, so the band closes up beneath it
  const alone = !occasion && lead.module === "looks" && !lead.occasionToggle && !!topLooks?.length;
  return <section className={"band" + (alone ? " band--looks-only" : "")}>{body}</section>;
}

function Opener({ onAnswer }: { onAnswer: (a: Audience) => void }) {
  return (
    <div className="band__centre">
      <h2 className="band__title">Who is it for?</h2>
      <div className="chips">
        {(
          [
            ["me", "For me"],
            ["her", "For her"],
            ["him", "For him"],
          ] as [Audience, string][]
        ).map(([a, label]) => (
          <button key={a} className="chip" onClick={() => onAnswer(a)}>
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}

function Picker({
  dept,
  onPick,
  shoppingFor,
  onChangeAudience,
}: {
  dept: Dept;
  onPick: (o: string) => void;
  shoppingFor: string | null;
  onChangeAudience: () => void;
}) {
  const [data, setData] = useState<{ occasions: Occasion[]; note: string | null } | null>(null);
  useEffect(() => {
    let live = true;
    setData(null);
    api.occasions(dept).then(
      (r) => live && setData(r),
      () => live && setData({ occasions: [], note: null }),
    );
    return () => {
      live = false;
    };
  }, [dept]);

  if (data && data.occasions.length === 0) return null;
  const ordered = data ? [...data.occasions.filter((o) => o.suggested), ...data.occasions.filter((o) => !o.suggested)] : [];

  return (
    <div className="band__centre">
      <h2 className="band__title">Where are you going?</h2>
      {shoppingFor && (
        <p className="band__note">
          Shopping for {shoppingFor} ·{" "}
          <button className="text-link" onClick={onChangeAudience}>
            Change
          </button>
        </p>
      )}
      {data?.note && <p className="band__note">{data.note}</p>}
      <div className="chips">
        {data
          ? ordered.map((o) => (
              <button key={o.value} className={"chip" + (o.suggested ? " is-filled" : "")} onClick={() => onPick(o.value)}>
                {o.label}
              </button>
            ))
          : [0, 1, 2, 3, 4].map((i) => <span key={i} className="chip placeholder chip--ph" />)}
      </div>
    </div>
  );
}

function ShortlistView({ occasion, dept, onChange }: { occasion: string; dept: Dept; onChange: () => void }) {
  const { logEvent } = useStore();
  const [refine, setRefine] = useState<Refine>({});
  const [list, setList] = useState<Shortlist | null>(null);
  const [error, setError] = useState<string | null>(null);
  const viewed = useRef(false);
  const refineKey = JSON.stringify(refine);

  useEffect(() => {
    let live = true;
    setList(null);
    setError(null);
    api.shortlist(occasion, dept, JSON.parse(refineKey) as Refine).then(
      (r) => {
        if (!live) return;
        setList(r);
        if (!viewed.current) {
          viewed.current = true;
          logEvent("shortlist_view", null, { occasion, count: r.count });
        }
      },
      (e: Error) => live && setError(e.message),
    );
    return () => {
      live = false;
    };
  }, [occasion, dept, refineKey, logEvent]);

  const toggle = (key: keyof Refine) => {
    const on = !refine[key];
    const next = { ...refine };
    if (on) next[key] = true;
    else delete next[key];
    setRefine(next);
    logEvent("refine_apply", null, { refine: key, on, occasion });
  };

  const sub = list
    ? `${list.count} ${list.count === 1 ? "piece" : "pieces"}. ` +
      (list.tasteRanked
        ? "Chosen for the occasion and ranked by your taste"
        : "Chosen for the occasion, across a range of styles")
    : "";

  return (
    <div className="shortlist">
      <div className="shortlist__head">
        <h2 className="band__title">
          {list ? `For ${list.phrase}` : <span className="placeholder placeholder--title shortlist__ph" />}
        </h2>
        <button className="text-link" onClick={onChange}>
          Change
        </button>
      </div>
      <p className="shortlist__sub">{sub || " "}</p>
      <div className="chips chips--left">
        {REFINES.map((r) => (
          <button
            key={r.key}
            className={"refine" + (refine[r.key] ? " is-on" : "")}
            aria-pressed={!!refine[r.key]}
            onClick={() => toggle(r.key)}
          >
            {r.label}
          </button>
        ))}
      </div>
      {error ? (
        <p className="message">{error}</p>
      ) : list && list.items.length === 0 ? (
        <div className="message">
          <p>Nothing matches that yet.</p>
          <button className="link-button link-button--accent" onClick={() => setRefine({})}>
            Clear the refinement
          </button>
        </div>
      ) : (
        <div className="hero-grid">
          {list
            ? list.items.map((i) => (
                <ProductCard key={i.id} card={i} variant="hero" occasion={occasion} reasons={i.reasons} />
              ))
            : [0, 1, 2].map((k) => <CardPlaceholder key={k} variant="hero" />)}
        </div>
      )}
    </div>
  );
}
