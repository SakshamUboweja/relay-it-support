import assert from 'node:assert/strict';
import test from 'node:test';
import type { TraceStep } from './domain';
import { roleLabel, sumStepLatency, sumUsage } from './trace';

const step = (over: Partial<TraceStep> = {}): TraceStep => ({
  seq: 1,
  role: 'intake',
  kind: 'model_call',
  model: 'gpt-5-mini',
  promptVersion: 'intake.v3',
  inputSummary: 'in',
  outputSummary: 'out',
  toolName: null,
  usage: { input: 0, output: 0 },
  costUsd: 0,
  latencyMs: 0,
  status: 'ok',
  ...over,
});

void test('roleLabel names every known agent role', () => {
  assert.equal(roleLabel('intake'), 'Intake');
  assert.equal(roleLabel('triage'), 'Triage');
  assert.equal(roleLabel('reviewer'), 'Review');
  assert.equal(roleLabel('single'), 'Single agent');
  assert.equal(roleLabel('policy'), 'Policy');
  assert.equal(roleLabel('tool'), 'Tool');
});

void test('roleLabel falls back to the raw role', () => {
  assert.equal(roleLabel('summarizer'), 'summarizer');
  assert.equal(roleLabel(''), '');
});

void test('sumStepLatency adds the step durations', () => {
  assert.equal(sumStepLatency([]), 0);
  assert.equal(
    sumStepLatency([
      step({ latencyMs: 120 }),
      step({ seq: 2, latencyMs: 880 }),
    ]),
    1000,
  );
});

void test('sumStepLatency ignores non-finite durations', () => {
  assert.equal(
    sumStepLatency([
      step({ latencyMs: 400 }),
      step({ seq: 2, latencyMs: Number.NaN }),
    ]),
    400,
  );
});

void test('sumUsage adds input and output tokens across steps', () => {
  assert.deepEqual(sumUsage([]), { input: 0, output: 0 });
  assert.deepEqual(
    sumUsage([
      step({ usage: { input: 900, output: 120 } }),
      step({ seq: 2, usage: { input: 340, output: 60 } }),
    ]),
    { input: 1240, output: 180 },
  );
});

void test('sumUsage tolerates steps without a usage record', () => {
  const missing = step({ seq: 3 });
  delete (missing as Partial<TraceStep>).usage;
  assert.deepEqual(
    sumUsage([step({ usage: { input: 10, output: 5 } }), missing]),
    {
      input: 10,
      output: 5,
    },
  );
});
