import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { featureAssignment, restoreFeatureAssignment } from '../../scripts/feature-assignment.mjs';

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

test('recovery reauthorizes the original event and preserves every field except a newer source pin', () => {
  const input = event();
  const original = featureAssignment(input, 'issues', 'demo-owner', base);
  const state = { schemaVersion: 2, planSchemaVersion: 2,
    eventId: original.eventId, prdSha256: original.prdSha256, baseCommit: base,
    assignment: original, githubRunId: '12345' };
  assert.deepEqual(restoreFeatureAssignment(input, 'issues', 'demo-owner', state, '12345'), original);
  for (const mutate of [
    e => { e.issue.body += '\nChanged requirements.'; },
    e => { e.issue.id += 1; },
    e => { e.issue.number += 1; },
    e => { e.repository.full_name = 'owner/different'; },
    e => { e.sender.login = 'outsider'; },
    e => { e.sender.type = 'Bot'; },
    e => { e.issue.labels = []; },
  ]) {
    const changed = structuredClone(input);
    mutate(changed);
    assert.throws(() => restoreFeatureAssignment(changed, 'issues', 'demo-owner', state, '12345'));
  }
  assert.throws(() => restoreFeatureAssignment(input, 'issues', 'demo-owner', state, '12346'),
    /original GitHub run/);
  assert.throws(() => restoreFeatureAssignment(input, 'issues', 'demo-owner',
    { ...state, planSchemaVersion: 1 }, '12345'), /schema/);
  assert.throws(() => restoreFeatureAssignment(input, 'issues', 'demo-owner',
    { ...state, assignment: { ...original, unexpected: true } }, '12345'), /assignment/);
});

test('historical opened events and v1 claims remain recoverable without upgrading the assignment', () => {
  const input = event();
  input.action = 'opened';
  delete input.label;
  const original = featureAssignment(input, 'issues', 'demo-owner', base);
  const restored = restoreFeatureAssignment(input, 'issues', 'demo-owner',
    { schemaVersion: 1, eventId: original.eventId, prdSha256: original.prdSha256, baseCommit: base });
  assert.deepEqual(restored, { ...original, schemaVersion: 1, kind: 'team-workload-v1' });
});

const prepareScript = fileURLToPath(new URL('../../scripts/prepare-feature.mjs', import.meta.url));

async function prepareFixture(t, input, attempt = '1') {
  const directory = await mkdtemp(join(tmpdir(), 'feature-intake-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const eventPath = join(directory, 'event.json');
  await writeFile(eventPath, JSON.stringify(input));
  const output = join(directory, 'output');
  const summary = join(directory, 'summary');
  const env = { ...process.env, GITHUB_EVENT_PATH: eventPath, GITHUB_EVENT_NAME: 'issues',
    SOURCE_COMMIT: base, DEMO_OPERATORS: 'demo-owner', GITHUB_RUN_ATTEMPT: attempt,
    GITHUB_RUN_ID: '12345', GITHUB_OUTPUT: output, GITHUB_STEP_SUMMARY: summary };
  return { directory, output, summary, env,
    run: (...args) => spawnSync(process.execPath, [prepareScript, ...args],
      { cwd: directory, env, encoding: 'utf8' }) };
}

test('opened plus labeled creates one assignment and unrelated labels cannot start execution', async t => {
  const opened = event();
  opened.action = 'opened';
  delete opened.label;
  const fixture = await prepareFixture(t, opened);
  assert.equal(fixture.run().status, 0);
  assert.equal(await readFile(fixture.output, 'utf8'), 'prepared=false\n');
  assert.match(await readFile(fixture.summary, 'utf8'), /waiting for.*labeled event/);
  await assert.rejects(readFile(join(fixture.directory, 'artifacts/issue-feature-123/assignment.json')));
  await writeFile(fixture.env.GITHUB_EVENT_PATH, JSON.stringify(event()));
  assert.equal(fixture.run().status, 0);
  assert.match(await readFile(fixture.output, 'utf8'), /prepared=true/);
  const unrelated = event();
  unrelated.label.name = 'documentation';
  const other = await prepareFixture(t, unrelated);
  assert.equal(other.run().status, 0);
  assert.equal(await readFile(other.output, 'utf8'), 'prepared=false\n');
});

test('old workflow prepare invocation allows explicit reruns of opened events', async t => {
  const opened = event();
  opened.action = 'opened';
  delete opened.label;
  const fixture = await prepareFixture(t, opened, '2');
  assert.equal(fixture.run().status, 0);
  const assignmentPath = join(fixture.directory, 'artifacts/issue-feature-123/assignment.json');
  const prepared = JSON.parse(await readFile(assignmentPath, 'utf8'));
  assert.deepEqual(prepared, featureAssignment(opened, 'issues', 'demo-owner', base));
});

test('restoration updates the adjacent assignment only after exact authorization and identity checks', async t => {
  const input = event();
  const fixture = await prepareFixture(t, input, '2');
  const original = featureAssignment(input, 'issues', 'demo-owner', base);
  const adjacent = join(fixture.directory, 'adjacent');
  await mkdir(adjacent);
  const statePath = join(adjacent, 'planning-state.json');
  const assignmentPath = join(adjacent, 'assignment.json');
  const state = { schemaVersion: 2, planSchemaVersion: 2, eventId: original.eventId,
    baseCommit: base, prdSha256: original.prdSha256, assignment: original, githubRunId: '12345' };
  await writeFile(statePath, JSON.stringify(state));
  const prepared = { ...original, baseCommit: 'c'.repeat(40) };
  await writeFile(assignmentPath, JSON.stringify(prepared));
  const run = fixture.run('--restore-planning', statePath);
  assert.equal(run.status, 0, run.stderr);
  assert.deepEqual(JSON.parse(await readFile(assignmentPath, 'utf8')), original);
  assert.equal(await readFile(join(adjacent, 'prd.md'), 'utf8'), original.prd);
  await assert.rejects(readFile(fixture.output)); // Do not replace old step outputs.
  delete fixture.env.DEMO_OPERATORS; // The original YAML sets this only on initial preparation.
  await writeFile(assignmentPath, JSON.stringify(prepared));
  const legacyRun = fixture.run('--restore-planning', statePath);
  assert.equal(legacyRun.status, 0, legacyRun.stderr);
  assert.deepEqual(JSON.parse(await readFile(assignmentPath, 'utf8')), original);
  fixture.env.DEMO_OPERATORS = 'demo-owner';
  for (const changed of [
    { ...prepared, actor: 'someone-else' }, { ...prepared, repository: 'other/repo' },
    { ...prepared, issueNumber: 999 }, { ...prepared, extra: true },
  ]) {
    const raw = JSON.stringify(changed);
    await writeFile(assignmentPath, raw);
    const rejected = fixture.run('--restore-planning', statePath);
    assert.equal(rejected.status, 1);
    assert.match(rejected.stderr, /authorized issue snapshot/);
    assert.equal(await readFile(assignmentPath, 'utf8'), raw);
  }
  fixture.env.GITHUB_RUN_ATTEMPT = '1';
  assert.match(fixture.run('--restore-planning', statePath).stderr, /explicit rerun/);
});
