export const STORAGE_KEY = 'bma-playground.board.v1';
export const COLUMNS = [
  { id: 'todo', label: 'To do', caption: 'A good place to start', number: '01' },
  { id: 'doing', label: 'In progress', caption: 'Taking shape', number: '02' },
  { id: 'done', label: 'Done', caption: 'Ready for the world', number: '03' },
];

const INITIAL_TASKS = [
  { id: 'welcome', title: 'Polish the welcome screen', description: 'Make that first hello feel a little more human.', status: 'todo', category: 'Design', priority: 'High', owner: 'Maya', initials: 'MK', color: 'lilac', note: 'First impressions' },
  { id: 'shortcuts', title: 'Add keyboard shortcuts', description: 'A faster way around for people who know where they’re going.', status: 'todo', category: 'Product', priority: 'Normal', owner: 'Erez', initials: 'EW', color: 'blue', note: 'Quality of life' },
  { id: 'onboarding', title: 'Build the onboarding flow', description: 'Three small steps from a blank page to a first win.', status: 'doing', category: 'Engineering', priority: 'High', owner: 'Erez', initials: 'EW', color: 'blue', note: 'Ready to wrap up' },
  { id: 'mobile', title: 'Give mobile some love', description: 'The same little workspace. A much smaller screen.', status: 'doing', category: 'Design', priority: 'Normal', owner: 'Maya', initials: 'MK', color: 'lilac', note: 'Small-screen details' },
  { id: 'tokens', title: 'Set the visual direction', description: 'A considered palette and a type system with room to breathe.', status: 'done', category: 'Design', priority: 'Normal', owner: 'Maya', initials: 'MK', color: 'lilac', note: 'Looking good' },
  { id: 'repo', title: 'Lay the groundwork', description: 'One repository, a working build, and a place to begin.', status: 'done', category: 'Engineering', priority: 'Normal', owner: 'Erez', initials: 'EW', color: 'blue', note: 'Foundations in place' },
];

export const TEAMMATES = [
  { name: 'Maya', initials: 'MK', color: 'lilac', capacity: 8 },
  { name: 'Erez', initials: 'EW', color: 'blue', capacity: 8 },
];
const EFFORT_DEFAULTS = { welcome: 8, mobile: 4, shortcuts: 2, onboarding: 2, tokens: 3, repo: 3 };

export function validEffort(value) {
  return Number.isInteger(value) && value >= 1 && value <= 8;
}

export function assignTask(tasks, taskId, owner) {
  const teammate = TEAMMATES.find((person) => person.name === owner);
  if (!teammate) return tasks;
  return tasks.map((task) => task.id === taskId
    ? { ...task, owner, initials: teammate.initials, color: teammate.color } : task);
}

export function setEffort(tasks, taskId, effort) {
  if (!validEffort(effort)) return tasks;
  return tasks.map((task) => task.id === taskId ? { ...task, effort } : task);
}

export function workloadTotals(tasks) {
  return Object.fromEntries(TEAMMATES.map(({ name }) => [name,
    tasks.filter((task) => task.owner === name && ['todo', 'doing'].includes(task.status))
      .reduce((total, task) => total + task.effort, 0)]));
}

export function initialTasks() {
  return structuredClone(INITIAL_TASKS).map((task) => ({ ...task, effort: EFFORT_DEFAULTS[task.id] }));
}

export function moveTask(tasks, taskId, destination) {
  const knownStatuses = COLUMNS.map((column) => column.id);
  if (!knownStatuses.includes(destination)) return tasks;
  return tasks.map((task) => task.id === taskId ? { ...task, status: destination } : task);
}

export function completion(tasks) {
  const done = tasks.filter((task) => task.status === 'done').length;
  return { done, total: tasks.length, percent: tasks.length ? Math.round(done / tasks.length * 100) : 0 };
}

export function filterTasks(tasks, query, teammate = 'All') {
  const normalized = query.trim().toLowerCase();
  return tasks.filter((task) => (teammate === 'All' || task.owner === teammate)
    && `${task.title} ${task.description} ${task.category} ${task.owner}`.toLowerCase().includes(normalized));
}

export function restoreTasks(raw) {
  if (!raw) return initialTasks();
  try {
    const saved = JSON.parse(raw);
    if (!Array.isArray(saved) || saved.length !== INITIAL_TASKS.length) return initialTasks();
    // Structural corruption resets the board. Individual invalid/missing fields
    // fall back independently; saved display copy and avatar data are never trusted.
    const byId = new Map(saved.map((task) => [task?.id, task]));
    const statuses = new Set(COLUMNS.map((column) => column.id));
    if (byId.size !== INITIAL_TASKS.length || INITIAL_TASKS.some((task) => !byId.has(task.id))) return initialTasks();
    return initialTasks().map((task) => {
      const stored = byId.get(task.id);
      const person = TEAMMATES.find((person) => person.name === stored.owner);
      return {
        ...task,
        status: statuses.has(stored.status) ? stored.status : task.status,
        owner: person?.name ?? task.owner,
        initials: person?.initials ?? task.initials,
        color: person?.color ?? task.color,
        effort: validEffort(stored.effort) ? stored.effort : task.effort,
      };
    });
  } catch {
    return initialTasks();
  }
}
