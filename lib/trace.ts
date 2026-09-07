import type { TokenUsage, TraceStep } from './domain';

const roles: Record<string, string> = {
  intake: 'Intake',
  triage: 'Triage',
  reviewer: 'Review',
  single: 'Single agent',
  policy: 'Policy',
  tool: 'Tool',
};

/** Operator-facing name for an agent step's role; unknown roles print as stored. */
export const roleLabel = (role: string): string => roles[role] ?? role;

const finite = (value: number | null | undefined): number =>
  typeof value === 'number' && Number.isFinite(value) ? value : 0;

/** Total step time, which can differ from the run's wall clock when work overlaps. */
export const sumStepLatency = (steps: TraceStep[]): number =>
  steps.reduce((total, step) => total + finite(step.latencyMs), 0);

/** Token totals across steps, used when a run record carries no usage of its own. */
export const sumUsage = (
  steps: TraceStep[],
): Pick<TokenUsage, 'input' | 'output'> =>
  steps.reduce(
    (total, step) => ({
      input: total.input + finite(step.usage?.input),
      output: total.output + finite(step.usage?.output),
    }),
    { input: 0, output: 0 },
  );
