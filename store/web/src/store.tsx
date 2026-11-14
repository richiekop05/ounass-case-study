import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "./api";
import type { EventType, Persona, Product, StoreState } from "./types";

// Shared shop state for the frame and pages: persona, session state, bag count, saved pieces, toast,
// and the product on screen (for the behind-the-scenes drawer).

export type Audience = "me" | "her" | "him";

type Store = {
  personas: Persona[];
  persona: Persona | null;
  session: string | null;
  state: StoreState | null;
  refreshState: () => void;
  /** Page load: tells the server (intent changes take effect now) and returns the state in force. */
  loadPage: () => Promise<StoreState | null>;
  /** James's answer to "Who is it for?", remembered for the session. */
  audience: Audience | null;
  setAudience: (a: Audience | null) => void;
  btsOpen: boolean;
  setBtsOpen: (open: boolean) => void;
  logEvent: (type: EventType, productId: string | null, context?: Record<string, unknown>) => Promise<void>;
  switchPersona: (id: string) => void;
  bag: number;
  addToBag: (n?: number) => void;
  saved: Set<string>;
  toggleSaved: (id: string) => boolean;
  toast: string | null;
  showToast: (msg: string) => void;
  product: Product | null;
  setProduct: (p: Product | null) => void;
};

const Ctx = createContext<Store | null>(null);

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore outside StoreProvider");
  return s;
}

function read(storage: () => Storage, key: string): string | null {
  try {
    return storage().getItem(key);
  } catch {
    return null;
  }
}

function write(storage: () => Storage, key: string, value: string) {
  try {
    storage().setItem(key, value);
  } catch {
    /* storage unavailable: the value lives in memory only */
  }
}

const local = () => window.localStorage;
const sessionStore = () => window.sessionStorage;

export function StoreProvider({ children }: { children: ReactNode }) {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [session, setSession] = useState<string | null>(null);
  const [state, setState] = useState<StoreState | null>(null);
  const [bag, setBag] = useState(0);
  const [saved, setSaved] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState<string | null>(null);
  const [product, setProduct] = useState<Product | null>(null);
  const [btsOpen, setBtsOpen] = useState(false);
  const [audience, setAudienceState] = useState<Audience | null>(null);
  const toastTimer = useRef<number | undefined>(undefined);

  const refreshState = useCallback(() => {
    api.state().then(setState, () => undefined);
  }, []);

  const loadPage = useCallback(
    () =>
      api.state(true, audience).then(
        (s) => {
          setState(s);
          return s;
        },
        () => null,
      ),
    [audience],
  );

  const setAudience = useCallback(
    (a: Audience | null) => {
      setAudienceState(a);
      if (session) write(sessionStore, `richies.audience.${session}`, a ?? "");
    },
    [session],
  );

  // Personas first (this also settles the session cookie), then the page loads its state and data.
  useEffect(() => {
    api.personas().then(
      (r) => {
        setPersonas(r.personas);
        setCurrent(r.current);
        setSession(r.session);
        setBag(Number(read(local, `richies.bag.${r.session}`)) || 0);
        try {
          setSaved(new Set(JSON.parse(read(local, `richies.saved.${r.session}`) || "[]") as string[]));
        } catch {
          setSaved(new Set());
        }
        const a = read(sessionStore, `richies.audience.${r.session}`);
        setAudienceState(a === "me" || a === "her" || a === "him" ? a : null);
      },
      () => undefined,
    );
  }, []);

  const logEvent = useCallback(
    (type: EventType, productId: string | null, context: Record<string, unknown> = {}) => {
      return api.event(type, productId, context).then(refreshState, () => undefined);
    },
    [refreshState],
  );

  const switchPersona = useCallback((id: string) => {
    api.session(id).then(
      () => window.location.reload(),
      () => window.location.reload(),
    );
  }, []);

  const addToBag = useCallback(
    (n = 1) => {
      setBag((b) => {
        const next = b + n;
        if (session) write(local, `richies.bag.${session}`, String(next));
        return next;
      });
    },
    [session],
  );

  const toggleSaved = useCallback(
    (id: string) => {
      const next = new Set(saved);
      const on = !next.has(id);
      if (on) next.add(id);
      else next.delete(id);
      setSaved(next);
      if (session) write(local, `richies.saved.${session}`, JSON.stringify([...next]));
      return on;
    },
    [saved, session],
  );

  const showToast = useCallback((msg: string) => {
    window.clearTimeout(toastTimer.current);
    setToast(msg);
    toastTimer.current = window.setTimeout(() => setToast(null), 1800);
  }, []);

  const persona = personas.find((p) => p.id === current) ?? null;

  return (
    <Ctx.Provider
      value={{
        personas, persona, session, state, refreshState, loadPage, audience, setAudience, btsOpen, setBtsOpen, logEvent, switchPersona, bag, addToBag,
        saved, toggleSaved, toast, showToast, product, setProduct,
      }}
    >
      {children}
    </Ctx.Provider>
  );
}
