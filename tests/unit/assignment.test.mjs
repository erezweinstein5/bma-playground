import test from 'node:test';
import assert from 'node:assert/strict';
import { assignmentFromEvent, buildBmaRequests } from '../../scripts/assignment.mjs';

function event() {
  return {
    action: 'opened',
    sender: { login: 'demo-owner', type: 'User' },
    repository: { full_name: 'demo-owner/bma-playground' },
    issue: { id: 123456, number: 7, title: 'Done is broken', body: 'Move a card to Done.', labels: [{ name: 'agent-fix' }] },
  };
}
const configured = {
  region: 'us-east-1',
  model: 'openai.example-model',
  runtimeArn: 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/demo_123',
  sessionRoleArn: 'arn:aws:iam::123456789012:role/DemoSession',
  baseCommit: 'a'.repeat(40),
};

test('authorized labeled issue becomes a deterministic assignment', () => {
  const opened = event();
  const first = assignmentFromEvent(opened, 'issues', 'demo-owner');
  assert.equal(first.issueNumber, 7);
  assert.equal(first.status, 'prepared');
  assert.equal(first.eventId, 'issue-123456');
  opened.action = 'labeled';
  opened.label = { name: 'agent-fix' };
  assert.deepEqual(assignmentFromEvent(opened, 'issues', 'demo-owner'), first);
});

test('ignores unauthorized actors, bots, PR comments, and unlabeled issues', () => {
  for (const change of [
    (e) => { e.sender.login = 'outsider'; },
    (e) => { e.sender.type = 'Bot'; },
    (e) => { e.issue.pull_request = { url: 'irrelevant' }; },
    (e) => { e.issue.labels = []; },
    (e) => { e.action = 'closed'; },
  ]) {
    const input = event();
    change(input);
    assert.equal(assignmentFromEvent(input, 'issues', 'demo-owner'), null);
  }
});

test('follow-up command has a distinct stable event identity', () => {
  const input = event();
  input.action = 'created';
  input.comment = { id: 999, body: '/codex Make the progress clearer.', user: { type: 'User' } };
  const result = assignmentFromEvent(input, 'issue_comment', 'demo-owner');
  assert.equal(result.eventId, 'comment-999');
  assert.equal(result.kind, 'follow_up');
  assert.equal(result.request, 'Make the progress clearer.');
  input.comment.body = 'Looks great. /codex hello';
  assert.equal(assignmentFromEvent(input, 'issue_comment', 'demo-owner'), null);
  input.comment.body = '/codex';
  assert.equal(assignmentFromEvent(input, 'issue_comment', 'demo-owner'), null);
});

test('missing operator allowlist and invalid source identities fail closed', () => {
  assert.throws(() => assignmentFromEvent(event(), 'issues', ''), /DEMO_OPERATORS/);
  const input = event();
  input.repository.full_name = '../outside';
  assert.throws(() => assignmentFromEvent(input, 'issues', 'demo-owner'), /repository/);
});

test('BMA payloads preserve issue text as data and pin source metadata', () => {
  const input = event();
  input.issue.body = 'Please inspect `x`; $(touch /tmp/nope)\n"quoted"';
  const assignment = assignmentFromEvent(input, 'issues', 'demo-owner');
  const request = buildBmaRequests(assignment, configured);
  const text = request.submitInput.events[0].input[0].content[0].text;
  assert.equal(JSON.parse(text).task, assignment.request);
  assert.equal(request.createSession.environment.type, 'aws_bedrock_agentcore');
  assert.equal(request.source.baseCommit, configured.baseCommit);
  assert.equal(request.createSession.role_arn, configured.sessionRoleArn);
  assert.equal(request.createSession.stream, false);
});

test('BMA config rejects missing model, region/account mismatch, and short SHA', () => {
  const assignment = assignmentFromEvent(event(), 'issues', 'demo-owner');
  for (const replacement of [
    { model: '' },
    { region: 'eu-central-1' },
    { region: 'us-west-2' },
    { sessionRoleArn: 'arn:aws:iam::999999999999:role/DemoSession' },
    { baseCommit: 'abc123' },
    { workspace: '/tmp/../etc' },
  ]) assert.throws(() => buildBmaRequests(assignment, { ...configured, ...replacement }));
});
