import { navigate } from "../router";
import "./Rail.css";

// Left rail (Design PRD 4): seven tabs, count badges on Products and Approved, settings under a
// divider, and a footer naming the run, default level and models behind what is on screen.

export type RailInfo = {
  waiting: number | null;
  approved: number | null;
  runId: string | null;
  defaultLevel: number | null;
  categoriseModel: string | null;
  tagModel: string | null;
};

const MAIN = [
  { key: "products", label: "Products", count: "waiting" as const },
  { key: "approved", label: "Approved", count: "approved" as const },
  { key: "live", label: "Live tag" },
  { key: "metrics", label: "Metrics" },
];
const SETTINGS = [
  { key: "confidence", label: "Confidence" },
  { key: "facets", label: "Facets" },
  { key: "models", label: "Models" },
];

export function Rail({ active, info }: { active: string; info: RailInfo }) {
  const item = (t: { key: string; label: string; count?: "waiting" | "approved" }) => {
    const n = t.count ? info[t.count] : null;
    return (
      <button
        key={t.key}
        className={"rail-item" + (active === t.key ? " is-active" : "")}
        aria-current={active === t.key ? "page" : undefined}
        onClick={() => navigate("/" + t.key)}
      >
        <span>{t.label}</span>
        {t.count && n !== null && <span className="rail-count">{n}</span>}
      </button>
    );
  };
  const dash = (v: string | null) => v ?? "—";
  return (
    <aside className="rail">
      <div className="rail-brand">Tagger</div>
      <nav className="rail-nav">
        {MAIN.map(item)}
        <div className="rail-divider" />
        <span className="label rail-section">Settings</span>
        {SETTINGS.map(item)}
      </nav>
      <div className="rail-footer">
        <span>Run {dash(info.runId)}</span>
        <span>Default level {info.defaultLevel === null ? "—" : info.defaultLevel.toFixed(2)}</span>
        <span>Categorising: {dash(info.categoriseModel)}</span>
        <span>Tagging: {dash(info.tagModel)}</span>
      </div>
    </aside>
  );
}
