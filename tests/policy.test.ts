import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decide, securityEvidence } from '../server/policy';
import { users } from '../server/fixtures';
import { sanitize } from '../server/sanitize';
import { validateExtraction } from '../server/model';
test('known VPN password change routes without fabricated facts', () => {
  const d = decide('VPN broke after I changed my password', [], users[0]);
  assert.equal(d.team, 'Identity & Access');
  assert.equal(d.question, null);
  assert.equal(d.facts.impact.value, null);
  assert.equal(d.facts.urgency.value, null);
  assert.equal(d.facts.rootCause.origin, 'hypothesis');
});
test('material ambiguity asks at most one question', () => {
  assert.ok(decide('I cannot get in', [], users[0]).question);
  const d = decide('I cannot get in, it is still broken', [], users[0], 1);
  assert.equal(d.question, null);
  assert.equal(d.team, 'Service Desk');
  assert.equal(d.accepted, false);
});
test('security suspicion bypasses ordinary help', () => {
  const d = decide('I approved an unexpected MFA prompt', [], users[0]);
  assert.equal(d.team, 'Security Review');
  assert.equal(d.visibility, 'restricted');
  assert.equal(d.procedure, null);
  assert.equal(d.question, null);
});
test('informational, negated and hypothetical security text does not escalate', () => {
  for (const s of [
    'How do I report phishing?',
    'My account is not compromised. VPN is down.',
    'This is a phishing training example.',
    'I did not click a phishing link.',
  ])
    assert.equal(securityEvidence(s), false, s);
});
test('separate sentence security evidence survives negation elsewhere', () => {
  assert.equal(
    securityEvidence(
      'VPN is not working. I approved an unexpected MFA prompt.',
    ),
    true,
  );
});
test('broad critical loss remains user reported', () => {
  const d = decide('SSO is down for everyone', [], users[0]);
  assert.equal(d.priority, 'urgent');
  assert.equal(d.facts.impact.origin, 'user');
  assert.equal(d.related, null);
});
test('explicit blocked work elevates while unknown urgency stays unknown', () => {
  assert.equal(
    decide('VPN fails; work is blocked with no workaround', [], users[0])
      .priority,
    'elevated',
  );
  assert.equal(decide('VPN fails', [], users[0]).priority, 'normal');
});
test('known secrets are removed before storage/model use', () => {
  const text = sanitize(
    'my password is Secret123 and MFA code 123456. sk-abcdefghijklmnopqrstuvwx',
  );
  assert.ok(!text.includes('Secret123'));
  assert.ok(!text.includes('123456'));
  assert.ok(!text.includes('sk-'));
});
test('model evidence IDs and quotes cannot invent facts', () => {
  const data = {
    service: null,
    serviceQuote: null,
    symptomQuote: 'invented',
    impactQuote: null,
    urgencyQuote: null,
    securityQuote: null,
    evidenceIds: ['message'],
  };
  assert.throws(
    () => validateExtraction(data, 'VPN fails', ['message']),
    /unsupported/,
  );
  assert.throws(
    () =>
      validateExtraction(
        { ...data, symptomQuote: null, evidenceIds: ['private-secret'] },
        'VPN fails',
        ['message'],
      ),
    /disallowed/,
  );
});
