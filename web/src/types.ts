export type Status = 'QUEUED' | 'RUNNING' | 'PENDING_REVIEW' | 'APPROVED' | 'REJECTED' | 'FAILED';

export interface Content {
  title: string;
  short_description: string;
  long_description: string;
  bullets: string[];
  seo_keywords: string[];
  cited_spec_keys: string[];
}

export interface Finding { rule: string; severity: 'error' | 'warning'; detail: string }
export interface Claim { claim: string; supported: boolean; evidence: string }
export interface Judge {
  claims: Claim[]; unsupported_count: number; score: number; explanation: string; passed: boolean;
  needs_attention?: boolean;
}
export interface Passage { chunk_id: number; ordinal: number; score: number; text: string }
export interface Product { sku: string; brand: string; category: string; name: string; specs: Record<string, string> }
export interface ModelCall {
  step: string; tier: string; deployment: string; cached: boolean; latency_ms: number; cost_usd: number;
  attempts: string[];
}
export interface Step { step: string; ms: number; [k: string]: unknown }

export interface JobSummary {
  id: string; sku: string; category: string | null; channel: string; status: Status; attempts: number;
  cost_usd: number; faithfulness: number | null; needs_attention: boolean; title: string | null;
  requested_by: string; reviewed_by: string | null; created_at: string; updated_at: string;
}

export interface Job extends JobSummary {
  tone: string;
  content: Content | null;
  guardrails: { passed: boolean; findings: Finding[] } | null;
  judge: Judge | null;
  sources: { passages: Passage[]; withheld: { chunk_id: number; reason: string }[]; facts: Product;
             guidance: string[] } | null;
  steps: Step[];
  model_calls: ModelCall[];
  prompt_tokens: number; completion_tokens: number;
  error: string | null;
  review_comment: string | null; review_category: string | null;
  final_content: Content | null; edited: boolean | null; draft_version: number | null;
}

export interface Metrics {
  reviewed: number; approved: number; approved_with_edits: number; rejected: number; pending_review: number;
  failed: number; avg_faithfulness: number | null; avg_attempts: number | null; avg_cost_per_job: number | null;
  total_cost: number | null; approval_rate: number | null; first_pass_acceptance: number | null;
  rejection_reasons: Record<string, number>;
}

export interface UsageRow {
  key: string | null; requests: number; prompt_tokens: number; completion_tokens: number; cost_usd: number;
  saved_usd: number; cache_hit_rate: number; p95_latency_ms: number; fallbacks: number;
}
