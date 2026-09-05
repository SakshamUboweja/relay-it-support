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
  origin: 'user' | 'context' | 'hypothesis' | 'policy_default' | 'unknown';
  evidenceIds: string[];
};
export const fact = <T>(
  value: T | null,
  origin: Fact<T>['origin'] = 'unknown',
  evidenceIds: string[] = [],
): Fact<T> => ({ value, origin, evidenceIds });
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
