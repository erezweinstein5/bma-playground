import { readFile, mkdir, writeFile, appendFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { assignmentFromEvent, buildBmaRequests } from './assignment.mjs';

async function main() {
  const eventPath = process.env.GITHUB_EVENT_PATH;
  if (!eventPath) throw new Error('Set GITHUB_EVENT_PATH to a GitHub event JSON file.');
  const event = JSON.parse(await readFile(eventPath, 'utf8'));
  const assignment = assignmentFromEvent(event, process.env.GITHUB_EVENT_NAME, process.env.DEMO_OPERATORS || '');
  if (!assignment) {
    console.log('No authorized repair assignment in this event.');
    return;
  }
  const baseCommit = process.env.SOURCE_COMMIT;
  if (!/^[a-f0-9]{40}$/.test(baseCommit || '')) throw new Error('SOURCE_COMMIT must be the checked-out source SHA.');
  assignment.baseCommit = baseCommit;
  const output = resolve('artifacts', assignment.eventId);
  await mkdir(output, { recursive: true });
  await writeFile(resolve(output, 'assignment.json'), `${JSON.stringify(assignment, null, 2)}\n`);
  const configuration = {
    region: process.env.AWS_REGION,
    model: process.env.BMA_MODEL,
    runtimeArn: process.env.BMA_RUNTIME_ARN,
    sessionRoleArn: process.env.BMA_SESSION_ROLE_ARN,
    workspace: process.env.BMA_WORKSPACE,
    baseCommit,
  };
  let configured = false;
  if (configuration.runtimeArn && configuration.sessionRoleArn && configuration.model && configuration.region) {
    const requests = buildBmaRequests(assignment, configuration);
    await writeFile(resolve(output, 'bma-requests.json'), `${JSON.stringify(requests, null, 2)}\n`);
    configured = true;
  }
  const status = `Assignment prepared for ${assignment.repository} issue #${assignment.issueNumber}.\n\n${configured ? 'BMA request payloads were also generated.' : 'BMA configuration is incomplete; only the assignment was generated.'}\n\nBMA execution, source transfer, PR publication, merging, and deployment are not connected yet. No agent session was created.\n`;
  console.log(status);
  if (process.env.GITHUB_STEP_SUMMARY) await appendFile(process.env.GITHUB_STEP_SUMMARY, status);
  if (process.env.GITHUB_OUTPUT) await appendFile(process.env.GITHUB_OUTPUT, `prepared=true\nartifact_path=${output}\nevent_id=${assignment.eventId}\n`);
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
