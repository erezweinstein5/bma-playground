import './style.css';
import { COLUMNS, STORAGE_KEY, completion, filterTasks, initialTasks, moveTask, restoreTasks, TEAMMATES, assignTask, setEffort, validEffort, workloadTotals } from './board.js';

const board = document.querySelector('#board');
const search = document.querySelector('#search');
const announcement = document.querySelector('#announcement');
// Appearance is independent of board persistence and never rerenders work in progress.
const APPEARANCE_KEY = 'bma-playground.appearance.v1';
let appearance = 'light';
let appearanceStorageAvailable = true;
try {
  const preference = localStorage.getItem(APPEARANCE_KEY);
  if (preference === 'light' || preference === 'dark') appearance = preference;
} catch {
  appearanceStorageAvailable = false;
}
const appearancePanel = document.createElement('div');
appearancePanel.className = 'appearance';
appearancePanel.innerHTML = `<div id="appearance-control" class="view-switch" role="group" aria-label="Appearance">
  <button type="button" data-appearance-option="light" aria-pressed="false">Light</button>
  <button type="button" data-appearance-option="dark" aria-pressed="false">Dark</button>
</div><span id="appearance-status" role="status"></span>`;
document.querySelector('.topbar').append(appearancePanel);
function applyAppearance() {
  document.documentElement.dataset.appearance = appearance;
  appearancePanel.querySelectorAll('button').forEach((button) =>
    button.setAttribute('aria-pressed', String(button.dataset.appearanceOption === appearance)));
  appearancePanel.querySelector('#appearance-status').textContent =
    `Appearance: ${appearance === 'dark' ? 'Dark' : 'Light'}`;
  if (!appearanceStorageAvailable) {
    announcement.textContent = 'Appearance lasts for this page; device storage is unavailable.';
  }
}
applyAppearance();
appearancePanel.addEventListener('click', (event) => {
  const button = event.target.closest('[data-appearance-option]');
  if (!button) return;
  appearance = button.dataset.appearanceOption;
  try {
    localStorage.setItem(APPEARANCE_KEY, appearance);
  } catch {
    appearanceStorageAvailable = false;
  }
  applyAppearance();
});
let persisted;
try { persisted = localStorage.getItem(STORAGE_KEY); } catch { /* The board works without storage. */ }
let tasks = restoreTasks(persisted);
let draggedId = null;
let selectedView = 'board';
const FOCUS_KEY = 'bma-playground.focus.v1';
const focusOptions = ['All', ...TEAMMATES.map(({ name }) => name)];
let selectedTeammate = 'All';
try {
  const preference = localStorage.getItem(FOCUS_KEY);
  if (focusOptions.includes(preference)) selectedTeammate = preference;
} catch { /* Focus remains usable without storage. */ }
function persistFocus() {
  try { localStorage.setItem(FOCUS_KEY, selectedTeammate); }
  catch { /* Do not claim the preference was saved when storage is unavailable. */ }
}
const filters = document.createElement('div');
filters.className = 'task-filters';
const searchField = search.closest('.search-field');
searchField.before(filters);
const teammateFilter = document.createElement('div');
teammateFilter.id = 'teammate-filter';
teammateFilter.className = 'view-switch teammate-filter';
teammateFilter.setAttribute('role', 'group');
teammateFilter.setAttribute('aria-label', 'Focus by teammate');
teammateFilter.innerHTML = focusOptions.map((name) =>
  `<button type="button" data-teammate="${name}" aria-pressed="${name === selectedTeammate}">${name}</button>`).join('');
filters.append(teammateFilter, searchField);
teammateFilter.addEventListener('click', (event) => {
  const button = event.target.closest('[data-teammate]');
  if (!button) return;
  selectedTeammate = button.dataset.teammate;
  persistFocus();
  render();
});
const filterStatus = document.createElement('p');
filterStatus.id = 'filter-status';
filterStatus.setAttribute('role', 'status');
filterStatus.setAttribute('aria-live', 'polite');
board.before(filterStatus);
const drafts = new Map();
const errors = new Set();
const workload = document.createElement('div');
workload.id = 'workload';
workload.hidden = true;
board.after(workload);
const filterEmpty = document.createElement('section');
filterEmpty.id = 'filter-empty';
filterEmpty.hidden = true;
filterEmpty.setAttribute('aria-labelledby', 'filter-empty-title');
filterEmpty.innerHTML = '<h3 id="filter-empty-title"></h3><p>Try another teammate or search. Your team totals still include all tasks.</p><button id="clear-filters" type="button">Clear filters</button>';
workload.after(filterEmpty);
filterEmpty.querySelector('button').addEventListener('click', () => {
  selectedTeammate = 'All';
  search.value = '';
  persistFocus();
  render();
  teammateFilter.querySelector('[data-teammate="All"]').focus();
});
const switcher = document.createElement('div');
switcher.className = 'view-switch';
switcher.setAttribute('role', 'group');
switcher.setAttribute('aria-label', 'Launch view');
switcher.innerHTML = ['board', 'workload'].map((view) =>
  `<button type="button" data-view="${view}" aria-controls="${view}" aria-pressed="${view === selectedView}">${view === 'board' ? 'Board' : 'Workload'}</button>`).join('');
document.querySelector('.board-view').replaceWith(switcher);
switcher.addEventListener('click', (event) => {
  const button = event.target.closest('[data-view]');
  if (!button) return;
  selectedView = button.dataset.view;
  board.hidden = selectedView !== 'board';
  workload.hidden = selectedView !== 'workload';
  switcher.querySelectorAll('button').forEach((control) => control.setAttribute('aria-pressed', String(control.dataset.view === selectedView)));
  document.querySelector('.skip-link').href = `#${selectedView}`;
  render();
});
workload.tabIndex = -1;

const icons = {
  grip: '<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="5" cy="4" r="1.2"/><circle cx="11" cy="4" r="1.2"/><circle cx="5" cy="8" r="1.2"/><circle cx="11" cy="8" r="1.2"/><circle cx="5" cy="12" r="1.2"/><circle cx="11" cy="12" r="1.2"/></svg>',
  check: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m4 8 3 3 5-6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

function card(task) {
  // Display copy is trusted local data; persisted editable fields are validated.
  return `<article class="task-card ${task.status === 'done' ? 'is-done' : ''}" draggable="true" data-task-id="${task.id}" aria-label="${task.title}">
    <div class="card-top"><span class="category category-${task.category.toLowerCase()}">${task.category}</span><span class="drag-handle">${icons.grip}</span></div>
    <h3>${task.title}</h3><p class="card-description">${task.description}</p>
    <div class="card-note">${task.status === 'done' ? icons.check : '<span class="note-line" aria-hidden="true"></span>'}<span>${task.note}</span></div>
    <div class="card-bottom"><span class="avatar avatar-${task.color}" title="${task.owner}">${task.initials}</span><span class="priority priority-${task.priority.toLowerCase()}"><span aria-hidden="true">◷</span>${task.priority === 'High' ? 'High priority' : 'No rush'}</span>
      <label class="status-control"><span class="sr-only">Status for ${task.title}</span><select data-status-for="${task.id}" aria-label="Status for ${task.title}">${COLUMNS.map((column) => `<option value="${column.id}" ${column.id === task.status ? 'selected' : ''}>${column.label}</option>`).join('')}</select></label>
    </div>
  </article>`;
}

function escapeAttribute(value) {
  return String(value).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}

function workloadRow(task) {
  const invalid = errors.has(task.id);
  return `<article class="task-card workload-card" data-workload-task="${task.id}">
    <div class="card-top"><span class="category category-${task.category.toLowerCase()}">${task.category}</span><span>${COLUMNS.find((column) => column.id === task.status).label}</span></div>
    <h3>${task.title}</h3><p class="card-description">${task.description}</p>
    <div class="workload-controls">
      <label>Owner<select data-owner-for="${task.id}" aria-label="Owner for ${task.title}">${TEAMMATES.map(({ name }) => `<option value="${name}" ${task.owner === name ? 'selected' : ''}>${name}</option>`).join('')}</select></label>
      <label>Effort<input type="text" inputmode="numeric" data-effort-for="${task.id}" aria-label="Effort for ${task.title}" value="${escapeAttribute(drafts.get(task.id) ?? task.effort)}" ${invalid ? `aria-invalid="true" aria-describedby="effort-error-${task.id}"` : ''}></label>
      <button type="button" data-save-effort-for="${task.id}" aria-label="Save effort for ${task.title}">Save</button>
    </div>
    ${invalid ? `<p class="effort-error" id="effort-error-${task.id}">Enter a whole number from 1 to 8. Previous saved effort remains ${task.effort} points.</p>` : ''}
  </article>`;
}

function renderWorkload(filtered) {
  const totals = workloadTotals(tasks);
  workload.innerHTML = `<p class="workload-context">Active effort · To do + In progress. Totals include all tasks regardless of filters; Done adds zero.</p><div class="workload-lanes">${TEAMMATES.map((person) => {
    const total = totals[person.name];
    const visible = filtered.filter((task) => task.owner === person.name && task.status !== 'done');
    return `<section data-workload-owner="${person.name}" aria-label="${person.name} workload">
      <div class="workload-summary ${total > person.capacity ? 'over-capacity' : ''}">
        <div class="workload-person"><span class="avatar avatar-${person.color}">${person.initials}</span><h3>${person.name}<small>Capacity · ${person.capacity} points</small></h3><strong data-workload-load>${total} / ${person.capacity} points</strong></div>
        <div class="capacity-bar" aria-hidden="true"><i style="width:${Math.min(total / person.capacity * 100, 100)}%"></i></div>
        ${total > person.capacity ? `<p data-overload-warning>! Over capacity · ${total - person.capacity} points over</p>` : `<p>${total === person.capacity ? 'At capacity · Not overloaded' : `${person.capacity - total} points available`}</p>`}
      </div>
      <div class="workload-list">${visible.map(workloadRow).join('') || '<p class="empty-column">No matching active tasks.</p>'}</div>
    </section>`;
  }).join('')}</div>`;
}

function render(focusTaskId) {
  const active = document.activeElement;
  const focusHook = ['data-owner-for', 'data-effort-for', 'data-save-effort-for', 'data-status-for'].find((hook) => active?.hasAttribute(hook));
  const focusId = focusHook ? active.getAttribute(focusHook) : null;
  const filtered = filterTasks(tasks, search.value, selectedTeammate);
  teammateFilter.querySelectorAll('button').forEach((button) =>
    button.setAttribute('aria-pressed', String(button.dataset.teammate === selectedTeammate)));
  const count = selectedView === 'workload'
    ? filtered.filter((task) => task.status !== 'done').length : filtered.length;
  filterStatus.textContent = `${count} ${selectedView === 'workload' ? 'active ' : ''}${count === 1 ? 'task' : 'tasks'} shown · ${selectedTeammate === 'All' ? 'All teammates' : selectedTeammate}`;
  filterEmpty.hidden = count !== 0;
  filterEmpty.querySelector('h3').textContent = selectedView === 'workload' ? 'No matching active tasks' : 'No matching tasks';
  board.innerHTML = COLUMNS.map((column) => {
    const visible = filtered.filter((task) => task.status === column.id);
    return `<section class="column column-${column.id}" data-column="${column.id}" aria-label="${column.label}">
      <div class="column-heading"><div><h3><span class="column-dot" aria-hidden="true"></span>${column.label}<span class="column-count">${visible.length}</span></h3><p>${column.caption}</p></div><span class="column-number" aria-hidden="true">${column.number}</span></div>
      <div class="card-list">${visible.map(card).join('')}<div class="drop-placeholder" aria-hidden="true">A little step forward</div>${!visible.length ? '<p class="empty-column">Room for what’s next.</p>' : ''}</div>
    </section>`;
  }).join('');
  renderWorkload(filtered);
  const progress = completion(tasks);
  document.querySelector('#progress-percent').textContent = `${progress.percent}%`;
  document.querySelector('#completion-count').textContent = `${progress.done} of ${progress.total} tasks`;
  document.querySelector('#ring-value').style.strokeDasharray = `${progress.percent} 100`;
  document.querySelector('.progress-panel').setAttribute('aria-label', `Launch progress: ${progress.done} of ${progress.total} tasks complete`);
  const result = document.querySelector('#search-result');
  result.hidden = !search.value.trim();
  result.textContent = `${filtered.length} ${filtered.length === 1 ? 'task matches' : 'tasks match'} “${search.value.trim()}”`;
  if (focusHook) {
    const replacement = document.querySelector(`[${focusHook}="${focusId}"]`);
    if (replacement) replacement.focus();
    else teammateFilter.querySelector(`[data-teammate="${selectedTeammate}"]`).focus();
  }
  if (focusTaskId) document.querySelector(`select[data-status-for="${focusTaskId}"]`)?.focus();
}

function save() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
    document.querySelector('#save-state').textContent = 'Saved on this device';
  } catch {
    document.querySelector('#save-state').textContent = 'Changes last until you close this page';
  }
}

function move(taskId, destination, keyboard = false) {
  const previous = tasks.find((task) => task.id === taskId);
  tasks = moveTask(tasks, taskId, destination);
  save();
  render(keyboard ? taskId : undefined);
  const updated = tasks.find((task) => task.id === taskId);
  announcement.textContent = updated && updated.status !== previous?.status
    ? `${updated.title} moved to ${COLUMNS.find((column) => column.id === updated.status)?.label}.`
    : 'The card stayed in its current column.';
}

board.addEventListener('change', (event) => {
  const select = event.target.closest('[data-status-for]');
  if (select) move(select.dataset.statusFor, select.value, true);
});
board.addEventListener('dragstart', (event) => {
  const element = event.target.closest('[data-task-id]');
  if (!element || event.target.closest('input, select, button')) return;
  draggedId = element.dataset.taskId;
  event.dataTransfer.setData('text/plain', draggedId);
  event.dataTransfer.effectAllowed = 'move';
  element.classList.add('is-dragging');
  board.classList.add('is-dragging');
});
board.addEventListener('dragover', (event) => {
  const column = event.target.closest('[data-column]');
  if (!column || !draggedId) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = 'move';
  document.querySelectorAll('.is-over').forEach((element) => element.classList.remove('is-over'));
  column.classList.add('is-over');
});
board.addEventListener('drop', (event) => {
  event.preventDefault();
  const column = event.target.closest('[data-column]');
  if (column && draggedId) move(draggedId, column.dataset.column);
  finishDrag();
});
board.addEventListener('dragend', finishDrag);
function finishDrag() {
  draggedId = null;
  board.classList.remove('is-dragging');
  document.querySelectorAll('.is-dragging, .is-over').forEach((element) => element.classList.remove('is-dragging', 'is-over'));
}
workload.addEventListener('input', (event) => {
  const input = event.target.closest('[data-effort-for]');
  if (input) drafts.set(input.dataset.effortFor, input.value);
});
workload.addEventListener('change', (event) => {
  const select = event.target.closest('[data-owner-for]');
  if (!select) return;
  const task = tasks.find((task) => task.id === select.dataset.ownerFor);
  tasks = assignTask(tasks, task.id, select.value);
  save();
  render();
  announcement.textContent = `${task.title} reassigned to ${select.value}.`;
});
function commitEffort(id) {
  const task = tasks.find((task) => task.id === id);
  const raw = drafts.get(id) ?? String(task.effort);
  const value = Number(raw);
  if (!raw.trim() || !validEffort(value)) {
    errors.add(id);
    render();
    announcement.textContent = `Effort for ${task.title} was not saved. Enter a whole number from 1 to 8.`;
    return;
  }
  tasks = setEffort(tasks, id, value);
  drafts.delete(id);
  errors.delete(id);
  save();
  render();
  announcement.textContent = `Effort for ${task.title} saved as ${value} points.`;
}
workload.addEventListener('click', (event) => {
  const button = event.target.closest('[data-save-effort-for]');
  if (button) commitEffort(button.dataset.saveEffortFor);
});
workload.addEventListener('keydown', (event) => {
  const input = event.target.closest('[data-effort-for]');
  if (input && event.key === 'Enter') {
    event.preventDefault();
    commitEffort(input.dataset.effortFor);
  }
});
search.addEventListener('input', () => render());
document.querySelector('#reset').addEventListener('click', () => {
  tasks = initialTasks();
  drafts.clear();
  errors.clear();
  search.value = '';
  selectedTeammate = 'All';
  persistFocus();
  save();
  render();
  announcement.textContent = 'Demo board reset. Two of six tasks complete.';
});
document.addEventListener('keydown', (event) => {
  if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !event.target.matches('input, select, textarea, [contenteditable]')) {
    event.preventDefault();
    search.focus();
  }
  if (event.key === 'Escape' && document.activeElement === search) {
    search.value = '';
    render();
    search.blur();
  }
});
render();

// Normalize migrated storage immediately; storage failure keeps in-memory edits usable.
save();
