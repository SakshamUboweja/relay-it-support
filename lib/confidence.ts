import type { Confidence, Pipeline } from './domain';

const bands: Confidence['band'][] = ['high', 'medium', 'low'];
const copy: Record<Confidence['band'], { label: string; sentence: string }> = {
  high: {
    label: 'High confidence',
    sentence: 'This request is very likely to reach the right team first time.',
  },
  medium: {
    label: 'Medium confidence',
    sentence:
      'Support may move this request to another team after a first look.',
  },
  low: {
    label: 'Low confidence',
    sentence:
      'The Service Desk will confirm the right team before work starts.',
  },
};
const labels: Record<Pipeline, string> = {
  single: 'Single agent',
  multi: 'Multi-agent',
  deterministic: 'Rule-based',
};
const notes: Record<Pipeline, string> = {
  single: "Routed by Relay's intake agent.",
  multi: "Routed by Relay's intake, triage and review agents.",
  deterministic: "Routed by Relay's rules, without an AI model.",
};
export const confidenceCopy = (band: Confidence['band']) => copy[band];
export const pipelineLabel = (pipeline: Pipeline | null | undefined) =>
  pipeline ? labels[pipeline] : '';
export const pipelineNote = (pipeline: Pipeline | null | undefined) =>
  pipeline ? notes[pipeline] : '';
export function isConfidence(x: unknown): x is Confidence {
  if (typeof x !== 'object' || x === null) return false;
  const c = x as Partial<Confidence>;
  return (
    typeof c.value === 'number' &&
    Number.isFinite(c.value) &&
    c.value >= 0 &&
    c.value <= 1 &&
    bands.includes(c.band as Confidence['band']) &&
    Array.isArray(c.signals)
  );
}
