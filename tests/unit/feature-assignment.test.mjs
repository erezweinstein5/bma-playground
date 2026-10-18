import { test } from 'node:test';
import assert from 'node:assert/strict';
import { featureAssignment } from '../../scripts/feature-assignment.mjs';

const base = 'a'.repeat(40);
const event = () => ({
  action: 'labeled', label: { name: 'agent-feature' },
  sender: { login: 'demo-owner', type: 'User' },
  repository: { full_name: 'demo-owner/bma-playground' },
  issue: { id: 123, number: 5, state: 'open', title: 'Team workload',
    labels: [{ name: 'agent-feature' }],
    body: 'PM requirements with explicit behavior and no instructions to execute code.\n' +
      Array.from({ length: 7 }, (_, i) => `AC-${i + 1}: Required product behavior.`).join('\n') },
});

test('pins literal PRD and stable identity; shell content stays data', () => {
  const input = event();
  input.issue.body += '\n$(touch /tmp/do-not-execute) `exit 1`';
  const result = featureAssignment(input, 'issues', 'demo-owner', base);
  assert.equal(result.eventId, 'issue-feature-123');
  assert.equal(result.baseCommit, base);
  assert.match(result.prd, /\$\(touch/);
  assert.equal(result.prdSha256.length, 64);
  assert.equal(featureAssignment(input, 'issues', 'demo-owner', base).prdSha256, result.prdSha256);
});

test('untrusted actors, bots, PRs, and unrelated events cannot trigger planning', () => {
  for (const mutate of [
    e => { e.sender.login = 'outsider'; },
    e => { e.sender.type = 'Bot'; },
    e => { e.issue.pull_request = {}; },
    e => { e.label.name = 'unrelated'; },
    e => { e.issue.state = 'closed'; },
    e => { e.action = 'edited'; },
    e => { e.issue.labels = []; },
  ]) {
    const input = event();
    mutate(input);
    assert.equal(featureAssignment(input, 'issues', 'demo-owner', base), null);
  }
  assert.equal(featureAssignment(event(), 'issue_comment', 'demo-owner', base), null);
});

test('rejects malformed identity, missing criteria, and oversized UTF-8 input', () => {
  for (const mutate of [
    e => { e.issue.id = -1; },
    e => { e.repository.full_name = '../escape'; },
    e => { e.issue.body = e.issue.body.replace('AC-7', 'AC-8'); },
    e => { e.issue.body += '界'.repeat(11000); },
  ]) {
    const input = event();
    mutate(input);
    assert.throws(() => featureAssignment(input, 'issues', 'demo-owner', base));
  }
  assert.throws(() => featureAssignment(event(), 'issues', '', base));
  assert.throws(() => featureAssignment(event(), 'issues', 'demo-owner', 'main'));
});
