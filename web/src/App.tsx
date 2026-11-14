import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { ConfirmProvider } from "./components/ConfirmDialog";
import { Rail, type RailInfo } from "./components/Rail";
import { ToastProvider } from "./components/Toast";
import { TopBar } from "./components/TopBar";
import { LiveTag } from "./pages/LiveTag";
import { Confidence } from "./pages/Confidence";
import { Facets } from "./pages/Facets";
import { MetricsPage } from "./pages/Metrics";
import { Models } from "./pages/Models";
import { ProductDetail } from "./pages/ProductDetail";
import { ProductList, type Summary } from "./pages/ProductList";
import { navigate, useRoute } from "./router";
import "./App.css";

const TITLES: Record<string, string> = {
  products: "Products",
  approved: "Approved",
  live: "Live tag",
  metrics: "Metrics",
  confidence: "Confidence",
  facets: "Facets",
  models: "Models",
};

const EMPTY_INFO: RailInfo = {
  waiting: null, approved: null, runId: null, defaultLevel: null, categoriseModel: null, tagModel: null,
};

export default function App() {
  const route = useRoute();
  const [info, setInfo] = useState<RailInfo>(EMPTY_INFO);
  const [health, setHealth] = useState<string>("Connecting to the database…");

  const tab = route.path.split("/")[1] || "";
  useEffect(() => {
    if (!TITLES[tab]) navigate("/products", { replace: true });
  }, [tab]);

  // Rail counts and footer come from the unfiltered summary: loaded once on start, again each time
  // a list loads, and after every approve or undo, so the badges stay current.
  const refreshInfo = useCallback(() => {
    api
      .get<Summary>("/api/summary")
      .then((s) =>
        setInfo({
          waiting: s.ready + s.needsReview,
          approved: s.approved,
          runId: s.latestRunId === null ? null : String(s.latestRunId),
          defaultLevel: s.defaultLevel,
          categoriseModel: s.categoriseModel,
          tagModel: s.tagModel,
        }),
      )
      .catch(() => undefined);
  }, []);
  useEffect(refreshInfo, [refreshInfo]);

  const listTab = /^\/(products|approved)\/?$/.exec(route.path)?.[1] as "products" | "approved" | undefined;
  const detail = /^\/(products|approved)\/([^/]+)\/?$/.exec(route.path);

  useEffect(() => {
    api
      .get<{ products: number }>("/api/health")
      .then((h) => setHealth(`Connected · ${h.products} scraped products`))
      .catch((e: Error) => setHealth(e.message));
  }, []);

  return (
    <ToastProvider>
    <ConfirmProvider>
    <div className="shell">
      <Rail active={tab} info={info} />
      <main className="shell-main">
        {detail ? (
          <ProductDetail
            key={detail[2]}
            id={decodeURIComponent(detail[2])}
            base={detail[1] as "products" | "approved"}
            onChanged={refreshInfo}
          />
        ) : listTab ? (
          <ProductList tab={listTab} onLoaded={refreshInfo} onChanged={refreshInfo} />
        ) : tab === "live" ? (
          <LiveTag onChanged={refreshInfo} />
        ) : tab === "metrics" ? (
          <MetricsPage />
        ) : tab === "confidence" ? (
          <Confidence onChanged={refreshInfo} />
        ) : tab === "facets" ? (
          <Facets />
        ) : tab === "models" ? (
          <Models onChanged={refreshInfo} />
        ) : (
          <>
            <TopBar left={<h1 className="title">{TITLES[tab] ?? ""}</h1>} />
            <div className="shell-scroll">
              <div className="placeholder-page">
                <p className="muted">{health}</p>
                <p className="muted small">This tab is built in a later step of docs/app/PLAN.md.</p>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
    </ConfirmProvider>
    </ToastProvider>
  );
}
