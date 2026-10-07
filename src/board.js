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

export function initialTasks() {
  return structuredClone(INITIAL_TASKS);
}

export function moveTask(tasks, taskId, destination) {
  const knownStatuses = ['todo', 'doing', 'complete'];
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
    const byId = new Map(saved.map((task) => [task?.id, task?.status]));
    const statuses = new Set(COLUMNS.map((column) => column.id));
    if (byId.size !== INITIAL_TASKS.length || INITIAL_TASKS.some((task) => !statuses.has(byId.get(task.id)))) return initialTasks();
    return initialTasks().map((task) => ({ ...task, status: byId.get(task.id) }));
  } catch {
    return initialTasks();
  }
}
