// Shapes of what the store endpoints return.

export type Dept = "women" | "men" | "all";

export type Persona = {
  id: string;
  name: string;
  archetype: string | null;
  fullName: string;
  department: Dept;
  /** Starting-session signals, plain statements of behaviour (Design PRD 4). */
  signals: string[];
  /** Computed by the intent rule over the persona's starting events. */
  startingIntent: "low" | "high";
  startingLead: LeadModule;
  /** What the store does, computed from the lead-module rule. */
  does: string;
};

export type LeadModule = "looks" | "occasion_picker" | "none";

export type Lead = {
  module: LeadModule;
  suggestedOccasion: string | null;
  /** Where the occasion in play came from: chosen (its shortlist reopens) or entry (suggested only). */
  occasionSource: "chosen" | "entry" | null;
  showOpener: boolean;
  /** Looks lead only: whether the one-line "Shopping for an occasion?" toggle sits under the row. */
  occasionToggle: boolean;
  code: string;
  reason: string;
};

export type LooksState = {
  state: "eligible" | "not_yet" | "never";
  code: string;
  reason: string;
  count: number;
  threshold: number;
  pending: boolean;
  since: string | null;
};

export type Card = { id: string; brand: string; name: string; price: number; image: string | null; colour: string | null };

export type FacetValue = { value: string; label: string; count: number };
export type Facet = { id: string; label: string; values: FacetValue[] };

export type Tag = { facet: string; facetLabel: string; value: string; label: string; confidence: number | null };

export type Product = {
  id: string;
  brand: string;
  name: string;
  price: number;
  currency: string;
  images: string[];
  description: string | null;
  details: unknown;
  sizeAndFit: unknown;
  sizes: string[];
  category: string;
  department: Dept;
  role: string;
  tags: Tag[];
  sample: boolean;
  inStock: boolean;
};

export type StoreEvent = {
  type: string;
  product: string | null;
  context: Record<string, unknown> | null;
  at: string;
  seed?: boolean;
  effect?: string | null;
};

export type ProfileDimension = { label: string; rows: { label: string; share: number }[] };

export type Intent = "low" | "high";

export type StoreState = {
  persona: string;
  session: string;
  intent: { state: Intent; reason: string; pending: Intent | null; pendingReason?: string | null };
  profile: ProfileDimension[] | null;
  events: StoreEvent[];
  looks: LooksState;
  lead: Lead;
};

export type Occasion = { value: string; label: string; display: string; phrase: string; suggested: boolean };

export type Refine = { budget?: boolean; neutral?: boolean; covered?: boolean };

export type ShortlistItem = Card & { reasons: string[]; reasonCodes: string[] };

export type Shortlist = {
  occasion: string;
  display: string;
  phrase: string;
  refine: Refine;
  count: number;
  tasteRanked: boolean;
  items: ShortlistItem[];
};

export type LookForYou = { occasion: string; title: string; hero: Card | null; thumbs: Card[]; sub: string };

export type EventType =
  | "view"
  | "save"
  | "add_to_bag"
  | "size_select"
  | "filter_apply"
  | "look_view"
  | "swap_out"
  | "swap_in"
  | "add_look_to_bag"
  | "occasion_select"
  | "refine_apply"
  | "shortlist_view"
  | "look_card_view"
  | "look_card_tap";

export type LookPlacement = "top_band" | "grid" | "product_page";

export type LookItem = Card & { reasonCode: string | null; reason: string | null };
export type LookSlot = { slot: string; label: string; items: LookItem[] };
export type OccasionOption = { value: string; label: string; phrase: string };

export type Look = {
  anchor: string;
  occasion: string | null;
  occasionLabel: string | null;
  occasionInPlay: string | null;
  /** The product-page occasion row's choices; empty means no row. */
  occasionOptions: OccasionOption[];
  filled: number;
  highIntent?: boolean;
  title: string | null;
  slots: LookSlot[];
};
