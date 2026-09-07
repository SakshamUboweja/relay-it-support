import assert from 'node:assert/strict';
import test from 'node:test';
import {
  confidenceCopy,
  isConfidence,
  pipelineLabel,
  pipelineNote,
} from './confidence';
import type { Confidence } from './domain';

const sample: Confidence = {
  value: 0.82,
  band: 'high',
  raw: 0.79,
  calibrated: true,
  degraded: false,
  signals: [
    { kind: 'margin', label: 'Clear winner over the runner-up', value: 0.4 },
  ],
  why: 'The intake agent and the policy agreed.',
};

void test('confidenceCopy returns employee-facing copy per band', () => {
  assert.deepEqual(confidenceCopy('high'), {
    label: 'High confidence',
    sentence: 'This request is very likely to reach the right team first time.',
  });
  assert.deepEqual(confidenceCopy('medium'), {
    label: 'Medium confidence',
    sentence:
      'Support may move this request to another team after a first look.',
  });
  assert.deepEqual(confidenceCopy('low'), {
    label: 'Low confidence',
    sentence:
      'The Service Desk will confirm the right team before work starts.',
  });
});

void test('pipelineLabel names each pipeline', () => {
  assert.equal(pipelineLabel('single'), 'Single agent');
  assert.equal(pipelineLabel('multi'), 'Multi-agent');
  assert.equal(pipelineLabel('deterministic'), 'Rule-based');
  assert.equal(pipelineLabel(null), '');
  assert.equal(pipelineLabel(undefined), '');
});

void test('pipelineNote explains how the request was routed', () => {
  assert.equal(pipelineNote('single'), "Routed by Relay's intake agent.");
  assert.equal(
    pipelineNote('multi'),
    "Routed by Relay's intake, triage and review agents.",
  );
  assert.equal(
    pipelineNote('deterministic'),
    "Routed by Relay's rules, without an AI model.",
  );
  assert.equal(pipelineNote(null), '');
});

void test('isConfidence accepts a well-formed payload', () => {
  assert.equal(isConfidence(sample), true);
  assert.equal(isConfidence({ ...sample, value: 0 }), true);
  assert.equal(isConfidence({ ...sample, value: 1, band: 'low' }), true);
});

void test('isConfidence rejects malformed payloads', () => {
  assert.equal(isConfidence(null), false);
  assert.equal(isConfidence(undefined), false);
  assert.equal(isConfidence('high'), false);
  assert.equal(isConfidence({ ...sample, band: 'unknown' }), false);
  assert.equal(isConfidence({ ...sample, value: 1.4 }), false);
  assert.equal(isConfidence({ ...sample, value: -0.1 }), false);
  assert.equal(isConfidence({ ...sample, value: Number.NaN }), false);
  assert.equal(isConfidence({ ...sample, value: '0.8' }), false);
  assert.equal(isConfidence({ ...sample, signals: undefined }), false);
  assert.equal(isConfidence({ ...sample, signals: 'none' }), false);
});

void test('isConfidence validates every signal in the list', () => {
  assert.equal(isConfidence({ ...sample, signals: [] }), true);
  assert.equal(
    isConfidence({
      ...sample,
      signals: [
        { kind: 'margin', label: 'Clear winner', value: 0.4 },
        { kind: 'agreement', label: 'Agents agreed', value: null },
      ],
    }),
    true,
  );
  assert.equal(isConfidence({ ...sample, signals: [{}, {}] }), false);
  assert.equal(isConfidence({ ...sample, signals: [null] }), false);
  assert.equal(
    isConfidence({
      ...sample,
      signals: [{ kind: 'margin', label: 7, value: 0.4 }],
    }),
    false,
  );
  assert.equal(
    isConfidence({
      ...sample,
      signals: [{ kind: 2, label: 'Clear', value: 0.4 }],
    }),
    false,
  );
  assert.equal(
    isConfidence({
      ...sample,
      signals: [{ kind: 'margin', label: 'Clear', value: Number.NaN }],
    }),
    false,
  );
  assert.equal(
    isConfidence({
      ...sample,
      signals: [{ kind: 'margin', label: 'Clear', value: '0.4' }],
    }),
    false,
  );
  assert.equal(
    isConfidence({
      ...sample,
      signals: [
        { kind: 'margin', label: 'Clear', value: 0.4 },
        { kind: 'agreement', label: 'Agreed' },
      ],
    }),
    false,
  );
});
