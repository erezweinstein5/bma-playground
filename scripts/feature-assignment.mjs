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
