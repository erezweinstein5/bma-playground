import { readFile, mkdir, writeFile, appendFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { featureAssignment, restoreFeatureAssignment } from './feature-assignment.mjs';

async function main() {
  const event = JSON.parse(await readFile(process.env.GITHUB_EVENT_PATH, 'utf8'));
  const attempt = process.env.GITHUB_RUN_ATTEMPT || '1';
  if (!/^[1-9][0-9]*$/.test(attempt)) throw new Error('Invalid GitHub run attempt.');
  const restorePath = process.argv[2] === '--restore-planning' && process.argv[3];
  if (process.argv.length > 2 && (!restorePath || process.argv.length !== 4))
    throw new Error('Usage: prepare-feature.mjs [--restore-planning checkpoint.json]');
  if (restorePath && Number(attempt) <= 1)
    throw new Error('Restoration requires an explicit rerun of the original GitHub run.');
  const restoreDirectory = restorePath && dirname(resolve(restorePath));
  const current = restorePath
    ? JSON.parse(await readFile(resolve(restoreDirectory, 'assignment.json'), 'utf8')) : null;
  // The original YAML exposes DEMO_OPERATORS only to the initial preparation
  // step. Its controller-issued snapshot has already passed the allowlist.
  // Recovery binds that exact actor and every other field to the original event;
  // publication independently checks the configured allowlist again.
  const operators = process.env.DEMO_OPERATORS || (restorePath ? current.actor : '');
  const assignment = restorePath
    ? restoreFeatureAssignment(event, process.env.GITHUB_EVENT_NAME, operators,
      JSON.parse(await readFile(restorePath, 'utf8')), process.env.GITHUB_RUN_ID)
    : featureAssignment(event, process.env.GITHUB_EVENT_NAME,
      process.env.DEMO_OPERATORS || '', process.env.SOURCE_COMMIT);
  // Retain opened-event parsing for historical reruns and publication validation.
  // New executions are authorized only by the label event.
  if (!restorePath && Number(attempt) === 1 && event.action === 'opened') {
    const message = 'Feature planning skipped: waiting for the agent-feature labeled event.';
    if (process.env.GITHUB_OUTPUT) await appendFile(process.env.GITHUB_OUTPUT, 'prepared=false\n');
    if (process.env.GITHUB_STEP_SUMMARY) await appendFile(process.env.GITHUB_STEP_SUMMARY, message + '\n');
    console.log(message);
    return;
  }
  if (!assignment) {
    if (process.env.GITHUB_OUTPUT) await appendFile(process.env.GITHUB_OUTPUT, 'prepared=false\n');
    console.log('No authorized feature assignment in this event.');
    return;
  }
  const directory = restorePath ? restoreDirectory : resolve('artifacts', assignment.eventId);
  if (restorePath) {
    const expected = { ...assignment, baseCommit: current.baseCommit };
    // A historical v1 run is initially prepared by the current v2 intake.
    if (current.schemaVersion === 2) {
      expected.schemaVersion = 2;
      expected.kind = 'prd-feature-v1';
    }
    if (!/^[0-9a-f]{40}$/.test(current.baseCommit || '') ||
        Object.keys(current).length !== Object.keys(expected).length ||
        Object.keys(expected).some(key => current[key] !== expected[key]))
      throw new Error('Prepared assignment differs from the original authorized issue snapshot.');
  }
  await mkdir(directory, { recursive: true });
  await writeFile(resolve(directory, 'assignment.json'), JSON.stringify(assignment, null, 2));
  await writeFile(resolve(directory, 'prd.md'), assignment.prd);
  if (process.env.GITHUB_OUTPUT && !restorePath) await appendFile(process.env.GITHUB_OUTPUT,
    `prepared=true\nevent_id=${assignment.eventId}\nartifact_path=${directory}\n`);
  console.log(`Pinned PRD for issue #${assignment.issueNumber}: ${assignment.prdSha256}`);
}

main().catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
