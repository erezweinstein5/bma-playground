import { createHash } from 'node:crypto';

/** Pin an authorized, bounded prose PRD without choosing its business rules. */
export function featureAssignment(event, eventName, allowedOperators, baseCommit) {
  const operators = new Set(allowedOperators.split(',').map(x => x.trim().toLowerCase()).filter(Boolean));
  if (!operators.size) throw new Error('DEMO_OPERATORS must be configured.');
  if (!/^[0-9a-f]{40}$/.test(baseCommit || '')) throw new Error('Pin a full source revision.');
  if (eventName !== 'issues' || !['opened', 'labeled'].includes(event.action)) return null;
  if (event.sender?.type !== 'User' || !operators.has(event.sender.login?.toLowerCase())) return null;
  const issue = event.issue;
  if (!issue || 'pull_request' in issue || issue.state !== 'open') return null;
  if (!issue.labels?.some(label => label.name === 'agent-feature')) return null;
  if (event.action === 'labeled' && event.label?.name !== 'agent-feature') return null;
  if (!Number.isSafeInteger(issue.id) || issue.id <= 0 ||
      !Number.isSafeInteger(issue.number) || issue.number <= 0) throw new Error('Invalid issue identity.');
  if (!/^[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9_.-]+$/.test(event.repository?.full_name || ''))
    throw new Error('Invalid repository.');
  if (typeof issue.title !== 'string' || !issue.title.trim() ||
      (issue.body != null && typeof issue.body !== 'string'))
    throw new Error('Malformed PRD.');
  const prd = `# ${issue.title || ''}\n\n${issue.body || ''}\n`;
  if (Buffer.byteLength(prd, 'utf8') < 100 || Buffer.byteLength(prd, 'utf8') > 32000)
    throw new Error('PRD must be 100–32000 UTF-8 bytes.');
  return {
    schemaVersion: 2, kind: 'prd-feature-v1', repository: event.repository.full_name,
    issueNumber: issue.number, eventId: `issue-feature-${issue.id}`,
    actor: event.sender.login, baseCommit, title: issue.title, prd,
    prdSha256: createHash('sha256').update(prd).digest('hex'), status: 'prepared',
  };
}

/** Reauthorize the original issue snapshot while retaining its durable pin. */
export function restoreFeatureAssignment(event, eventName, allowedOperators, state, runId) {
  const assignment = featureAssignment(event, eventName, allowedOperators, state.baseCommit);
  if (!assignment) throw new Error('Original feature issue is no longer authorized.');
  const version = state.schemaVersion;
  if (![1, 2].includes(version) || (state.planSchemaVersion ?? 1) !== version)
    throw new Error('Unsupported planning checkpoint schema.');
  if (state.eventId !== assignment.eventId || state.prdSha256 !== assignment.prdSha256)
    throw new Error('Existing planning claim has a different identity or PRD; use a new issue.');
  if (version === 1) {
    assignment.schemaVersion = 1;
    assignment.kind = 'team-workload-v1';
    if (!Array.from({ length: 7 }, (_, i) => `AC-${i + 1}`)
      .every(id => new RegExp(`\\b${id}\\b`).test(assignment.prd)))
      throw new Error('Legacy planning claim requires its original seven criteria.');
  }
  if (state.githubRunId && state.githubRunId !== runId)
    throw new Error(`Rerun the original GitHub run ${state.githubRunId}; this is a different run.`);
  if (state.assignment && (Object.keys(state.assignment).length !== Object.keys(assignment).length ||
      Object.keys(assignment).some(key => assignment[key] !== state.assignment[key])))
    throw new Error('Planning claim differs from its original authorized assignment.');
  return assignment;
}
