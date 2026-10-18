import './style.css';
import { COLUMNS, STORAGE_KEY, completion, filterTasks, initialTasks, moveTask, restoreTasks, TEAMMATES, assignTask, setEffort, validEffort, workloadTotals } from './board.js';

const board = document.querySelector('#board');
const search = document.querySelector('#search');
const announcement = document.querySelector('#announcement');
const workload = document.createElement('div');
workload.id = 'workload';
workload.tabIndex = -1;
workload.hidden = true;
board.after(workload);
const views = document.createElement('div');
views.className = 'view-buttons';
views.innerHTML = ['board', 'workload'].map((view) => `<button type="button" data-view="${view}" aria-controls="${view}" aria-pressed="${view === 'board'}">${view === 'board' ? 'Board' : 'Workload'}</button>`).join('');
document.querySelector('.board-view').replaceWith(views);
let selectedView = 'board';
const drafts = new Map();
const errors = new Set();
views.addEventListener('click', (event) => {
  const button = event.target.closest('[data-view]');
  if (!button) return;
  selectedView = button.dataset.view;
  render();
});

let persisted;
try { persisted = localStorage.getItem(STORAGE_KEY); } catch { /* The board works without storage. */ }
let tasks = restoreTasks(persisted);
let draggedId = null;

const icons = {
  grip: '<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="5" cy="4" r="1.2"/><circle cx="11" cy="4" r="1.2"/><circle cx="5" cy="8" r="1.2"/><circle cx="11" cy="8" r="1.2"/><circle cx="5" cy="12" r="1.2"/><circle cx="11" cy="12" r="1.2"/></svg>',
  check: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m4 8 3 3 5-6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

function card(task) {
  // All display copy is rebuilt from the local seed; edited fields are validated.
  return `<article class="task-card ${task.status === 'done' ? 'is-done' : ''}" draggable="true" data-task-id="${task.id}" aria-label="${task.title}">
    <div class="card-top"><span class="category category-${task.category.toLowerCase()}">${task.category}</span><span class="drag-handle">${icons.grip}</span></div>
    <h3>${task.title}</h3><p class="card-description">${task.description}</p>
    <div class="card-note">${task.status === 'done' ? icons.check : '<span class="note-line" aria-hidden="true"></span>'}<span>${task.note}</span></div>
    <div class="card-bottom"><span class="avatar avatar-${task.color}" title="${task.owner}">${task.initials}</span><span class="priority priority-${task.priority.toLowerCase()}"><span aria-hidden="true">◷</span>${task.priority === 'High' ? 'High priority' : 'No rush'}</span>
      <label class="status-control"><span class="sr-only">Status for ${task.title}</span><select data-status-for="${task.id}">${COLUMNS.map((column) => `<option value="${column.id}" ${column.id === task.status ? 'selected' : ''}>${column.label}</option>`).join('')}</select></label>
    </div>
    ${editors(task, 'board')}
  </article>`;
}

function escapeAttribute(value) {
  return String(value).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}

function editors(task, view) {
  const invalid = errors.has(task.id);
  const errorId = `${view}-effort-error-${task.id}`;
  return `<div class="task-editors">
    <label>Owner<select data-owner-for="${task.id}" aria-label="Owner for ${task.title}">${TEAMMATES.map((person) => `<option value="${person.name}" ${person.name === task.owner ? 'selected' : ''}>${person.name}</option>`).join('')}</select></label>
    <label>Effort<input type="text" inputmode="numeric" data-effort-for="${task.id}" aria-label="Effort for ${task.title}" value="${escapeAttribute(drafts.get(task.id) ?? task.effort)}" ${invalid ? `aria-invalid="true" aria-describedby="${errorId}"` : ''}></label>
    <button type="button" data-save-effort-for="${task.id}" aria-label="Save effort for ${task.title}">Apply</button>
  </div>${invalid ? `<p class="effort-error" id="${errorId}">Enter a whole number from 1 to 8. Previous effort, ${task.effort} points, kept.</p>` : ''}`;
}

function workloadCard(task) {
  return `<article class="task-card ${task.status === 'done' ? 'is-done' : ''}" ${task.status === 'done' ? 'data-workload-done' : 'data-workload-task'}="${task.id}">
    <div class="card-top"><span class="category category-${task.category.toLowerCase()}">${task.category}</span><span class="workload-status">${task.status === 'done' ? '✓ Done · 0 active points' : COLUMNS.find((column) => column.id === task.status).label}</span></div>
    <h3>${task.title}</h3><p class="card-description">${task.description}</p>
    ${editors(task, 'workload')}
    <label class="workload-status-control">Status<select data-status-for="${task.id}" aria-label="Status for ${task.title}">${COLUMNS.map((column) => `<option value="${column.id}" ${column.id === task.status ? 'selected' : ''}>${column.label}</option>`).join('')}</select></label>
  </article>`;
}

function renderWorkload(filtered) {
  workload.innerHTML = `<p class="workload-context">Active points only · To do + In progress. Totals include all active work, not just search matches.</p><div class="workload-columns">${workloadTotals(tasks).map((person) => `<section data-workload-owner="${person.name}" aria-label="${person.name} workload">
    <div class="workload-summary ${person.overloaded ? 'overloaded' : person.active === 8 ? 'at-capacity' : ''}">
      <div class="workload-person"><span class="avatar avatar-${person.color}">${person.initials}</span><h2>${person.name}<small>Capacity 8 points</small></h2><strong data-workload-load>${person.active} / 8 points</strong></div>
      <div class="capacity-bar" role="img" aria-label="${person.name}: ${person.active} active points, capacity 8${person.overloaded ? ', over by ' + (person.active - 8) : ', not overloaded'}"><i style="width:${Math.min(person.active, 8) / Math.max(person.active, 8) * 100}%"></i>${person.overloaded ? `<b style="width:${(person.active - 8) / person.active * 100}%"></b>` : ''}</div>
      <p data-overload-warning ${person.overloaded ? '' : 'hidden'}>⚠ Over capacity by ${person.active - 8} · Warning only</p>
      ${!person.overloaded ? `<p>${person.active === 8 ? '✓ At capacity' : `${8 - person.active} points available`}</p>` : ''}
    </div>
    ${filtered.filter((task) => task.owner === person.name && task.status !== 'done').map(workloadCard).join('')}
    ${filtered.filter((task) => task.owner === person.name && task.status === 'done').map(workloadCard).join('')}
    ${!filtered.some((task) => task.owner === person.name) ? '<p class="empty-column">Room for what’s next.</p>' : ''}
  </section>`).join('')}</div>`;
}

function render(focusTaskId) {
  const active = document.activeElement;
  const panel = selectedView === 'board' ? board : workload;
  const focusAttribute = ['data-owner-for', 'data-effort-for', 'data-save-effort-for', 'data-status-for'].find((attribute) => active?.hasAttribute(attribute));
  const focusId = focusAttribute ? active.getAttribute(focusAttribute) : null;
  const selection = active?.tagName === 'INPUT' ? [active.selectionStart, active.selectionEnd] : null;
  board.hidden = selectedView !== 'board';
  workload.hidden = selectedView !== 'workload';
  views.querySelectorAll('button').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.view === selectedView)));
  const filtered = filterTasks(tasks, search.value);
  // Mount task controls only in the selected view. The canonical model and
  // draft/error state live outside either panel, so switching loses no edits.
  // This also keeps each task's accessible control labels unique in the DOM.
  board.innerHTML = selectedView === 'board' ? COLUMNS.map((column) => {
    const visible = filtered.filter((task) => task.status === column.id);
    return `<section class="column column-${column.id}" data-column="${column.id}" aria-label="${column.label}">
      <div class="column-heading"><div><h3><span class="column-dot" aria-hidden="true"></span>${column.label}<span class="column-count">${visible.length}</span></h3><p>${column.caption}</p></div><span class="column-number" aria-hidden="true">${column.number}</span></div>
      <div class="card-list">${visible.map(card).join('')}<div class="drop-placeholder" aria-hidden="true">A little step forward</div>${!visible.length ? '<p class="empty-column">Room for what’s next.</p>' : ''}</div>
    </section>`;
  }).join('') : '';
  if (selectedView === 'workload') renderWorkload(filtered);
  else workload.replaceChildren();
  const progress = completion(tasks);
  document.querySelector('#progress-percent').textContent = `${progress.percent}%`;
  document.querySelector('#completion-count').textContent = `${progress.done} of ${progress.total} tasks`;
  document.querySelector('#ring-value').style.strokeDasharray = `${progress.percent} 100`;
  document.querySelector('.progress-panel').setAttribute('aria-label', `Launch progress: ${progress.done} of ${progress.total} tasks complete`);
  const result = document.querySelector('#search-result');
  result.hidden = !search.value.trim();
  result.textContent = `${filtered.length} ${filtered.length === 1 ? 'task matches' : 'tasks match'} “${search.value.trim()}”`;
  const target = focusAttribute ? panel.querySelector(`[${focusAttribute}="${focusId}"]`)
    : focusTaskId ? panel.querySelector(`[data-status-for="${focusTaskId}"]`) : null;
  if (target) {
    target.focus();
    if (selection && target.tagName === 'INPUT') target.setSelectionRange(...selection);
  } else if (focusAttribute) search.focus(); // Owner-search may remove the edited card.

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

function totalsAnnouncement() {
  return workloadTotals(tasks).map((person) => `${person.name} ${person.active}/8`).join('; ') + '.';
}

function applyEffort(id) {
  const task = tasks.find((task) => task.id === id);
  const draft = drafts.get(id) ?? String(task.effort);
  const effort = /^[0-9]+$/.test(draft.trim()) ? Number(draft.trim()) : NaN;
  if (!validEffort(effort)) {
    errors.add(id);
    render();
    announcement.textContent = `Effort for ${task.title} must be a whole number from 1 to 8. Previous effort, ${task.effort} points, kept.`;
    return;
  }
  tasks = setEffort(tasks, id, effort);
  drafts.delete(id);
  errors.delete(id);
  save();
  render();
  announcement.textContent = `${task.title} effort saved as ${effort} points. ${totalsAnnouncement()}`;
}

for (const panel of [board, workload]) {
  panel.addEventListener('change', (event) => {
    const select = event.target;
    if (select.matches('[data-status-for]')) move(select.dataset.statusFor, select.value, true);
    if (select.matches('[data-owner-for]')) {
      const task = tasks.find((task) => task.id === select.dataset.ownerFor);
      tasks = assignTask(tasks, task.id, select.value);
      save();
      render();
      announcement.textContent = `${task.title} assigned to ${tasks.find((item) => item.id === task.id).owner}. ${totalsAnnouncement()}`;
    }
  });
  panel.addEventListener('input', (event) => {
    if (event.target.matches('[data-effort-for]')) drafts.set(event.target.dataset.effortFor, event.target.value);
  });
  panel.addEventListener('click', (event) => {
    const button = event.target.closest('[data-save-effort-for]');
    if (button) applyEffort(button.dataset.saveEffortFor);
  });
  panel.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.target.matches('[data-effort-for]')) {
      event.preventDefault();
      applyEffort(event.target.dataset.effortFor);
    }
  });
}
board.addEventListener('dragstart', (event) => {
  const element = event.target.closest('[data-task-id]');
  if (!element || event.target.closest('select, input, button, a, textarea, [contenteditable]')) { event.preventDefault(); return; }
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
search.addEventListener('input', () => render());
document.querySelector('#reset').addEventListener('click', () => {
  tasks = initialTasks();
  drafts.clear();
  errors.clear();
  search.value = '';
  save();
  render();
  announcement.textContent = 'Demo board reset. Two of six tasks complete.';
});
document.addEventListener('keydown', (event) => {
  if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !event.target.matches('input, select, textarea, [contenteditable], [data-save-effort-for]')) {
    event.preventDefault();
    search.focus();
  }
  if (event.key === 'Escape' && document.activeElement === search) {
    search.value = '';
    render();
    search.blur();
  }
});
save(); // Normalize migrated fields through the same storage-failure-safe path.
render();
