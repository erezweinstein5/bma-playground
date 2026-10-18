export const STORAGE_KEY = 'bma-playground.board.v1';
export const COLUMNS = [
  { id: 'todo', label: 'To do', caption: 'A good place to start', number: '01' },
  { id: 'doing', label: 'In progress', caption: 'Taking shape', number: '02' },
  { id: 'done', label: 'Done', caption: 'Ready for the world', number: '03' },
];

export const EFFORT_DEFAULTS = { welcome: 8, mobile: 4, shortcuts: 2, onboarding: 2, tokens: 3, repo: 3 };
export const TEAMMATES = [
  { name: 'Maya', capacity: 8, initials: 'MK', color: 'lilac' },
  { name: 'Erez', capacity: 8, initials: 'EW', color: 'blue' },
];

const INITIAL_TASKS = [
  { id: 'welcome', title: 'Polish the welcome screen', description: 'Make that first hello feel a little more human.', status: 'todo', category: 'Design', priority: 'High', owner: 'Maya', initials: 'MK', color: 'lilac', note: 'First impressions' },
  { id: 'shortcuts', title: 'Add keyboard shortcuts', description: 'A faster way around for people who know where they’re going.', status: 'todo', category: 'Product', priority: 'Normal', owner: 'Erez', initials: 'EW', color: 'blue', note: 'Quality of life' },
  { id: 'onboarding', title: 'Build the onboarding flow', description: 'Three small steps from a blank page to a first win.', status: 'doing', category: 'Engineering', priority: 'High', owner: 'Erez', initials: 'EW', color: 'blue', note: 'Ready to wrap up' },
  { id: 'mobile', title: 'Give mobile some love', description: 'The same little workspace. A much smaller screen.', status: 'doing', category: 'Design', priority: 'Normal', owner: 'Maya', initials: 'MK', color: 'lilac', note: 'Small-screen details' },
  { id: 'tokens', title: 'Set the visual direction', description: 'A considered palette and a type system with room to breathe.', status: 'done', category: 'Design', priority: 'Normal', owner: 'Maya', initials: 'MK', color: 'lilac', note: 'Looking good' },
  { id: 'repo', title: 'Lay the groundwork', description: 'One repository, a working build, and a place to begin.', status: 'done', category: 'Engineering', priority: 'Normal', owner: 'Erez', initials: 'EW', color: 'blue', note: 'Foundations in place' },
];

export function initialTasks() {
  return INITIAL_TASKS.map((task) => ({ ...task, effort: EFFORT_DEFAULTS[task.id] }));
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

export function filterTasks(tasks, query) {
  const normalized = query.trim().toLowerCase();
  return tasks.filter((task) => `${task.title} ${task.description} ${task.category} ${task.owner}`.toLowerCase().includes(normalized));
}

export function restoreTasks(raw) {
  if (!raw) return initialTasks();
  try {
    const saved = JSON.parse(raw);
    if (!Array.isArray(saved) || saved.length !== INITIAL_TASKS.length) return initialTasks();
    // Invalid envelopes reset completely. Within a valid envelope fields fall back
    // independently; display copy is always local, and avatars follow the owner.
    const byId = new Map(saved.map((task) => [task?.id, task]));
    const statuses = new Set(COLUMNS.map((column) => column.id));
    if (byId.size !== INITIAL_TASKS.length || INITIAL_TASKS.some((task) => !byId.has(task.id))) return initialTasks();
    return initialTasks().map((task) => {
      const entry = byId.get(task.id);
      const person = TEAMMATES.find((person) => person.name === entry.owner);
      return { ...task,
        status: statuses.has(entry.status) ? entry.status : task.status,
        owner: person ? person.name : task.owner,
        initials: person ? person.initials : task.initials,
        color: person ? person.color : task.color,
        effort: validEffort(entry.effort) ? entry.effort : task.effort,
      };
    });
  } catch {
    return initialTasks();
  }
}

export function validEffort(value) {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= 8;
}

export function assignTask(tasks, taskId, owner) {
  const person = TEAMMATES.find((person) => person.name === owner);
  if (!person) return tasks;
  return tasks.map((task) => task.id === taskId
    ? { ...task, owner, initials: person.initials, color: person.color } : task);
}

export function setEffort(tasks, taskId, effort) {
  if (!validEffort(effort)) return tasks;
  return tasks.map((task) => task.id === taskId ? { ...task, effort } : task);
}

export function workloadTotals(tasks) {
  return TEAMMATES.map((person) => {
    const active = tasks.filter((task) => task.owner === person.name && ['todo', 'doing'].includes(task.status))
      .reduce((sum, task) => sum + task.effort, 0);
    return { ...person, active, overloaded: active > person.capacity };
  });
}
