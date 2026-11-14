import { useEffect, useState } from "react";
import { PersonaBar } from "./components/PersonaBar";
import { StoreHeader } from "./components/StoreHeader";
import { Toast } from "./components/Toast";
import { BehindTheScenes } from "./components/BehindTheScenes";
import { PLP } from "./pages/PLP";
import { PDP } from "./pages/PDP";
import { useRoute } from "./router";
import { useStore, type Audience } from "./store";
import type { Dept, Intent, Persona } from "./types";

const DEPTS: Dept[] = ["women", "men", "all"];

/** The department "/" opens: James's answer if he gave one, else the persona's own. */
function homeDept(persona: Persona | null, audience: Audience | null): Dept {
  if (persona?.department === "all") return audience ? (audience === "her" ? "women" : "men") : "all";
  return persona?.department ?? "women";
}

export default function App() {
  const route = useRoute();
  const { persona, audience, loadPage } = useStore();
  const parts = route.path.split("/").filter(Boolean);

  // A page load is a change of path (or a reload). The server applies any pending intent change now, and the
  // page fetches its data only after that, so an event never changes the page already on screen.
  const [loaded, setLoaded] = useState<{ path: string; intent: Intent } | null>(null);
  const personaReady = persona !== null;
  useEffect(() => {
    if (!personaReady) return;
    let live = true;
    loadPage().then((s) => live && setLoaded({ path: route.path, intent: s?.intent.state ?? "low" }));
    return () => {
      live = false;
    };
  }, [route.path, personaReady, loadPage]);
  const intent = loaded && loaded.path === route.path ? loaded.intent : null;

  let page;
  let active: Dept | null = null;
  if (parts[0] === "p" && parts[1]) {
    page = (
      <PDP
        key={parts[1]}
        id={decodeURIComponent(parts[1])}
        occasion={route.params.get("occasion") || null}
        intent={intent}
      />
    );
  } else if (parts.length === 0 || (parts.length === 1 && DEPTS.includes(parts[0] as Dept))) {
    active = (parts[0] as Dept) || homeDept(persona, audience);
    page = <PLP dept={active} params={route.params} intent={intent} />;
  } else {
    page = (
      <div className="container notfound">
        <p>That page is not in the store.</p>
        <a href="/women">Back to Women</a>
      </div>
    );
  }

  return (
    <>
      <PersonaBar />
      <StoreHeader active={active} />
      <Toast />
      <main>{page}</main>
      <BehindTheScenes />
    </>
  );
}
