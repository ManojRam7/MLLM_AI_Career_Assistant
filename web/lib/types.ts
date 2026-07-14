export type Job = {
  dedupe_key: string;
  title: string;
  company: string | null;
  location: string | null;
  locations: string | null;
  source: string | null;
  in_bucket: boolean | null;
  bucket_tier: string | null;
  category: string | null;
  sector: string | null;
  fit_score: number | null;
  fit_reasoning: string | null;
  seniority: string | null;
  status: string | null;
  url: string | null;
  posted_date: string | null;
  first_seen_at: string | null;
  is_custom: boolean | null;
  tracked: boolean | null;
  ghost_flag: boolean | null;
  recommendations: string | null;
  cover_text: string | null;
};

export type PipelineRun = {
  id: number;
  run_at: string | null;
  mode: string | null;
  discovered: number | null;
  stored_new: number | null;
  scored: number | null;
  summary_json: any;
};

export const TRACKER_STAGES = [
  "To apply",
  "Applied",
  "Assessment",
  "Interview",
  "Offer",
  "Rejected",
] as const;
export type Stage = (typeof TRACKER_STAGES)[number];
