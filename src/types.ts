export type Glyph = {
  id: string;
  text: string;
  bbox: [number, number, number, number];
  size: number;
};
export type PdfPage = {
  number: number;
  width: number;
  height: number;
  rotation: number;
  quality: string;
  text: string;
  glyphs: Glyph[];
};
export type Candidate = {
  id: string;
  word?: string;
  lemma?: string;
  hanja?: string;
  pronunciation?: string;
  inflections?: { form: string; pronunciations: string[] }[];
  pronunciations?: string[];
  definition?: string;
  meaning?: string;
  pos?: string;
  queried_at?: string;
  url?: string;
  source_url?: string;
  long_positions?: number[];
  [key: string]: unknown;
};
export type Occurrence = {
  source?: string;
  id: string;
  page: number;
  text: string;
  lemma: string;
  glyph_ids: string[];
  status: "unresolved" | "long" | "none";
  reason: string;
  reason_code?: string;
  meaning_status?: string;
  evidence_type?: string;
  evidence_candidates?: string[];
  pronunciations?: string[];
  applied_rules?: {
    id: string;
    label: string;
    url: string;
    explanation?: string;
  }[];
  morphology?: {
    lemma?: string;
    kind?: string;
    surface?: string;
    particle?: string;
    components?: MorphologyComponent[];
  };
  pronunciation_scope?: string;
  component_results?: ComponentResult[];
  coverage?: {
    complete: boolean;
    checked_ranges: [number, number][];
    unresolved_ranges: [number, number][];
  };
  candidates: Candidate[];
  selected_candidate_id?: string | null;
  note?: string;
  context?: string;
  group?: string;
};
export type MorphologyComponent = {
  lemma: string;
  pos: string;
  start: number;
  end: number;
  kind?: string;
};
export type ComponentResult = MorphologyComponent & {
  candidates: Candidate[];
  evidence_candidates?: string[];
  status: "unresolved" | "long" | "none";
  long_positions?: number[];
  reason?: string;
};
export type Mark = {
  id: string;
  glyph_id: string;
  page: number;
  source:
    | "manual"
    | "dictionary"
    | "dictionary_consensus"
    | "rule"
    | "rule_consensus"
    | "dictionary_rule";
  occurrence_id?: string;
  dx: number;
  dy: number;
  scale: number;
};
export type Settings = { color: string; opacity: number; size: number };
export type Job = {
  id: string;
  filename: string;
  sha256: string;
  pages: PdfPage[];
  occurrences: Occurrence[];
  marks: Mark[];
  settings: Settings;
  analysis: { state: string; done: number; total: number; message: string };
  [key: string]: unknown;
};
export type Snapshot = Pick<Job, "settings" | "marks" | "occurrences">;
