export const teams = [
  'Service Desk',
  'Identity & Access',
  'Network',
  'Endpoint',
  'Business Applications',
  'Security Review',
] as const;
export const services = ['vpn', 'sso', 'wifi', 'laptop', 'atlas'] as const;
export type Team = (typeof teams)[number];
export type Service = (typeof services)[number];
export type Fact<T = string> = {
  value: T | null;
  origin:
    | 'user'
    | 'context'
    | 'hypothesis'
    | 'policy_default'
    | 'image'
    | 'unknown';
  evidenceIds: string[];
};
export const fact = <T>(
  value: T | null,
  origin: Fact<T>['origin'] = 'unknown',
  evidenceIds: string[] = [],
): Fact<T> => ({ value, origin, evidenceIds });
export type Pipeline = 'single' | 'multi' | 'deterministic';
export type ConfidenceSignal = {
  kind: string;
  label: string;
  value: number | null;
};
export type Confidence = {
  value: number;
  band: 'high' | 'medium' | 'low';
  raw: number;
  calibrated: boolean;
  degraded: boolean;
  signals: ConfidenceSignal[];
  why: string;
  agentRationale?: string | null;
};
export type TokenUsage = {
  input: number;
  output: number;
  cached?: number;
  reasoning?: number;
};
export type TraceStep = {
  seq: number;
  role: string;
  kind: 'model_call' | 'tool_call' | 'policy';
  model: string | null;
  promptVersion: string | null;
  inputSummary: string;
  outputSummary: string;
  toolName: string | null;
  usage: TokenUsage;
  costUsd: number | null;
  latencyMs: number;
  status: 'ok' | 'error' | 'timeout' | 'rejected' | 'skipped';
  toolArgs?: Record<string, unknown> | null;
  toolResultSummary?: string | null;
  error?: string | null;
  detail?: Record<string, unknown>;
};
export type TraceRun = {
  id: string;
  pipeline: Pipeline;
  scoring: string;
  status: 'completed' | 'failed' | 'budget_exhausted' | 'skipped';
  model: string | null;
  reasoningEffort: string | null;
  usage: TokenUsage;
  costUsd: number | null;
  latencyMs: number;
  createdAt: string;
  steps: TraceStep[];
  outcome?: Record<string, unknown>;
  budget?: Record<string, unknown>;
  pricingVersion?: string;
};
export type Trace = { runs: TraceRun[] };
export type MessageImage = {
  id: string;
  filename: string;
  contentType: string;
  size: number;
  url: string;
  hasContent: boolean;
};
export type User = {
  id: string;
  name: string;
  role: 'employee' | 'operator';
  location: string;
  device: string;
  scope: string;
  external_account: string | null;
};
export type Source = {
  id: string;
  kind: 'article' | 'case' | 'incident';
  title: string;
  body: string;
  service: Service;
  visibility: string;
  location: string | null;
  status: string;
  updated_at: string;
  created_at: string;
  metadata: Record<string, unknown>;
};
export type Decision = {
  team: Team;
  service: Service | null;
  accepted: boolean;
  reasons: string[];
  alternatives: { team: Team; score: number }[];
  priority: 'normal' | 'elevated' | 'urgent';
  escalation: 'none' | 'security' | 'urgent' | 'elevated';
  visibility: 'private' | 'restricted';
  facts: Record<string, Fact>;
  sources: string[];
  procedure: Source | null;
  related: Source | null;
  question: string | null;
  model: string;
  promptVersion?: string;
  reasoningEffort?: string;
  supportRequested?: boolean;
  pipeline?: Pipeline;
  confidence?: Confidence;
  proposal?: {
    team: string;
    service: string | null;
    abstain: boolean;
    probability: number;
    rationale: string;
    citedSourceIds: string[];
  };
  reviewer?: {
    verdict: 'accept' | 'revise' | 'human_review';
    agreementProbability: number;
    issues: { field: string; message: string }[];
  };
  scoring?: string;
  costUsd?: number | null;
  agentRunId?: string;
  version: string;
  latencyMs: number;
  usage: { input: number; output: number };
};
export type Report = {
  id: string;
  owner_id: string;
  summary: string;
  state: string;
  decision: Decision;
  clarifications: number;
  offered: string[];
  attempted: string[];
  provider_key: string | null;
  provider_url: string | null;
  provider_status: string | null;
  provider_team: string | null;
  provider_priority: string | null;
  synced_at: string | null;
  created_at: string;
  updated_at: string;
  acknowledged_at: string | null;
  related_id: string | null;
  mode: string;
};
export type Message = {
  id: string;
  report_id: string;
  role: 'user' | 'assistant';
  body: string;
  created_at: string;
  image?: MessageImage | null;
};
export type Operation = {
  id: string;
  report_id: string;
  operation_key: string;
  kind: 'create' | 'update';
  state: 'pending' | 'unknown' | 'succeeded' | 'failed';
  payload: Record<string, unknown>;
  payload_hash: string;
  external_key: string | null;
  attempts: number;
  last_error: string | null;
  next_attempt_at: string;
};
