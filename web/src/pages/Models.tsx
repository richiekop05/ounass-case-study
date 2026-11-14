import { useCallback, useEffect, useState } from "react";
import { api, errorInfo } from "../api";
import { useToast } from "../components/Toast";
import { TopBar } from "../components/TopBar";
import "./Models.css";

// Models tab (Design PRD 10): which model profile categorises and which tags. The profiles come from
// config/models.toml (never edited here); the choice is saved in the database and used by everything tagged
// from then on. Saving never re-tags anything.

type Result = { products: number; costPerProduct: number; secondsPerProduct: number; accuracy: number | null; accuracyAnswers: number | null } | null;
type Profile = {
  name: string; modelId: string; provider: string; readsPhotos: boolean; inputPrice: number; outputPrice: number;
  results: { categorise: Result; tag: Result };
};
type Data = { profiles: Profile[]; selected: { categorise: string; tag: string } };
type Step = "categorise" | "tag";

const NAMES: Record<string, string> = { haiku: "Haiku", sonnet: "Sonnet", opus: "Opus" };
const display = (n: string) => NAMES[n] ?? n;
const money = (v: number) => (v >= 1 ? `$${v.toFixed(2)}` : `${Number((v * 100).toPrecision(2))}¢`);

function Card({ p, step, selected, current, onPick }: { p: Profile; step: Step; selected: boolean; current: boolean; onPick: () => void }) {
  const r = p.results[step];
  return (
    <button className={"md-card" + (selected ? " is-selected" : "")} aria-pressed={selected} onClick={onPick}>
      <span className="md-head">
        <span className="md-name">{display(p.name)}</span>
        {current && <span className="md-current">Current</span>}
      </span>
      <span className="muted small">{p.modelId}</span>
      <span className="muted small">${p.inputPrice.toFixed(2)} in · ${p.outputPrice.toFixed(2)} out per million tokens</span>
      {p.readsPhotos ? <span className="small">Reads photos</span> : (
        <span className="small">Text only <span className="md-flag">Facets judged from photos will go to review</span></span>
      )}
      <span className="md-results small">
        {r ? (
          <>
            <span>{r.accuracy === null ? "Not scored on the golden set" : `${Math.round(r.accuracy * 100)}% ${step === "tag" ? "of golden answers right" : "of categories right"}`}</span>
            <span className="muted">{money(r.costPerProduct)} and {r.secondsPerProduct.toFixed(1)} s per product · {r.products} products</span>
          </>
        ) : <span className="muted">Not run yet</span>}
      </span>
    </button>
  );
}

export function Models({ onChanged }: { onChanged?: () => void }) {
  const toast = useToast();
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pick, setPick] = useState<{ categorise: string; tag: string } | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    api.get<Data>("/api/models")
      .then((d) => { setData(d); setPick(d.selected); setError(null); })
      .catch((e) => setError(errorInfo(e).message));
  }, []);
  useEffect(load, [load]);

  const dirty = !!data && !!pick && (pick.categorise !== data.selected.categorise || pick.tag !== data.selected.tag);
  const save = async () => {
    if (!data || !pick) return;
    setSaving(true);
    try {
      await api.put("/api/models", {
        ...(pick.categorise !== data.selected.categorise ? { categoriseModel: pick.categorise } : {}),
        ...(pick.tag !== data.selected.tag ? { tagModel: pick.tag } : {}),
      });
      toast({ message: "Saved. Products tagged from now on use this model.", duration: 4000 });
      load();
      onChanged?.();
    } catch (e) {
      toast({ message: errorInfo(e).message });
    } finally {
      setSaving(false);
    }
  };

  const section = (step: Step, title: string, note: string) => (
    <section className="md-section">
      <h2 className="md-h2">{title}</h2>
      <p className="muted small md-note">{note}</p>
      <div className="md-cards">
        {data!.profiles.map((p) => (
          <Card key={p.name} p={p} step={step} selected={pick![step] === p.name} current={data!.selected[step] === p.name}
                onPick={() => setPick((x) => ({ ...x!, [step]: p.name }))} />
        ))}
      </div>
    </section>
  );

  return (
    <>
      <TopBar left={<h1 className="title">Models</h1>} />
      <div className="shell-scroll">
        <div className="md">
          {error && <div className="error-banner" role="alert"><span className="error-banner-text">{error}</span><button className="btn btn-secondary btn-sm" onClick={load}>Try again</button></div>}
          {!data && !error && <div aria-busy="true" className="md-cards">{[0, 1, 2].map((i) => <div key={i} className="skeleton md-sk" />)}</div>}
          {data && pick && (
            <>
              {section("categorise", "Categorising model", "Picks each product's category. Results are from past categorising runs.")}
              {section("tag", "Tagging model", "Fills in the facets. Results are from past tagging runs; accuracy is against the golden set.")}
              <p className="muted small">
                The choice applies to everything tagged from now on: Live tag, a category change that re-tags, and command-line
                runs that do not name a model. Saving never re-tags existing products and never changes approved ones.
              </p>
            </>
          )}
        </div>
      </div>
      {dirty && (
        <div className="md-footer" role="region" aria-label="Unsaved model choice">
          <span>
            {pick!.categorise !== data!.selected.categorise && `Categorising: ${display(data!.selected.categorise)} → ${display(pick!.categorise)}. `}
            {pick!.tag !== data!.selected.tag && `Tagging: ${display(data!.selected.tag)} → ${display(pick!.tag)}.`}
          </span>
          <button className="btn btn-secondary" onClick={() => setPick(data!.selected)}>Discard</button>
          <button className="btn btn-primary" disabled={saving} onClick={() => void save()}>{saving ? "Saving…" : "Save changes"}</button>
        </div>
      )}
    </>
  );
}
