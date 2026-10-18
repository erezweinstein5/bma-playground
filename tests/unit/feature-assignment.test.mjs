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
  assert.equal(result.schemaVersion, 2);
  assert.equal(result.kind, 'prd-feature-v1');
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

test('accepts unrelated prose PRDs and preserves optional, variable criteria', () => {
  for (const body of [
    'People need to find tasks by the assigned teammate. Provide a visible selector and an all-teammates option. Preserve the selection after reload.',
    'Provide a compact display preference. AC-1: Toggle compact mode. AC-2: Remember the setting after a reload. Explain the active setting visibly.',
    'AC-3: Let users archive a completed task from its menu. AC-8: Restore an archived task from a dedicated list without losing its original owner.',
  ]) {
    const input = event();
    input.issue.title = 'A product request';
    input.issue.body = body;
    const result = featureAssignment(input, 'issues', 'demo-owner', base);
    assert.equal(result.prd, `# A product request\n\n${body}\n`);
    assert.equal(result.kind, 'prd-feature-v1');
  }
});

test('rejects malformed identity, malformed PRDs, and bounded UTF-8 input', () => {
  for (const mutate of [
    e => { e.issue.id = -1; },
    e => { e.repository.full_name = '../escape'; },
    e => { e.issue.number = 2 ** 53; },
    e => { e.issue.title = {}; },
    e => { e.issue.body = {}; },
    e => { e.issue.body = false; },
    e => { e.issue.title = ''; },
    e => { e.issue.body = 'too short'; },
    e => { e.issue.body += '界'.repeat(11000); },
  ]) {
    const input = event();
    mutate(input);
    assert.throws(() => featureAssignment(input, 'issues', 'demo-owner', base));
  }
  assert.throws(() => featureAssignment(event(), 'issues', '', base));
  assert.throws(() => featureAssignment(event(), 'issues', 'demo-owner', 'main'));
});
