import { readFile, mkdir, writeFile, appendFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { featureAssignment } from './feature-assignment.mjs';

async function main() {
  const event = JSON.parse(await readFile(process.env.GITHUB_EVENT_PATH, 'utf8'));
  const assignment = featureAssignment(event, process.env.GITHUB_EVENT_NAME,
    process.env.DEMO_OPERATORS || '', process.env.SOURCE_COMMIT);
  if (!assignment) {
    console.log('No authorized feature assignment in this event.');
    return;
  }
  const directory = resolve('artifacts', assignment.eventId);
  await mkdir(directory, { recursive: true });
  await writeFile(resolve(directory, 'assignment.json'), JSON.stringify(assignment, null, 2));
  await writeFile(resolve(directory, 'prd.md'), assignment.prd);
  if (process.env.GITHUB_OUTPUT) await appendFile(process.env.GITHUB_OUTPUT,
    `prepared=true\nevent_id=${assignment.eventId}\nartifact_path=${directory}\n`);
  console.log(`Pinned PRD for issue #${assignment.issueNumber}: ${assignment.prdSha256}`);
}

main().catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
