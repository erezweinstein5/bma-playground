import './style.css';
import { COLUMNS, STORAGE_KEY, completion, filterTasks, initialTasks, moveTask, restoreTasks } from './board.js';

const board = document.querySelector('#board');
const search = document.querySelector('#search');
const announcement = document.querySelector('#announcement');
let persisted;
try { persisted = localStorage.getItem(STORAGE_KEY); } catch { /* The board works without storage. */ }
let tasks = restoreTasks(persisted);
let draggedId = null;

const icons = {
  grip: '<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="5" cy="4" r="1.2"/><circle cx="11" cy="4" r="1.2"/><circle cx="5" cy="8" r="1.2"/><circle cx="11" cy="8" r="1.2"/><circle cx="5" cy="12" r="1.2"/><circle cx="11" cy="12" r="1.2"/></svg>',
  check: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m4 8 3 3 5-6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

function card(task) {
  // Task copy is a fixed local dataset; persisted input only restores validated status IDs.
  return `<article class="task-card ${task.status === 'done' ? 'is-done' : ''}" draggable="true" data-task-id="${task.id}" aria-label="${task.title}">
    <div class="card-top"><span class="category category-${task.category.toLowerCase()}">${task.category}</span><span class="drag-handle">${icons.grip}</span></div>
    <h3>${task.title}</h3><p class="card-description">${task.description}</p>
    <div class="card-note">${task.status === 'done' ? icons.check : '<span class="note-line" aria-hidden="true"></span>'}<span>${task.note}</span></div>
    <div class="card-bottom"><span class="avatar avatar-${task.color}" title="${task.owner}">${task.initials}</span><span class="priority priority-${task.priority.toLowerCase()}"><span aria-hidden="true">◷</span>${task.priority === 'High' ? 'High priority' : 'No rush'}</span>
      <label class="status-control"><span class="sr-only">Status for ${task.title}</span><select data-status-for="${task.id}">${COLUMNS.map((column) => `<option value="${column.id}" ${column.id === task.status ? 'selected' : ''}>${column.label}</option>`).join('')}</select></label>
    </div>
  </article>`;
}

function render(focusTaskId) {
  const filtered = filterTasks(tasks, search.value);
  board.innerHTML = COLUMNS.map((column) => {
    const visible = filtered.filter((task) => task.status === column.id);
    return `<section class="column column-${column.id}" data-column="${column.id}" aria-label="${column.label}">
      <div class="column-heading"><div><h3><span class="column-dot" aria-hidden="true"></span>${column.label}<span class="column-count">${visible.length}</span></h3><p>${column.caption}</p></div><span class="column-number" aria-hidden="true">${column.number}</span></div>
      <div class="card-list">${visible.map(card).join('')}<div class="drop-placeholder" aria-hidden="true">A little step forward</div>${!visible.length ? '<p class="empty-column">Room for what’s next.</p>' : ''}</div>
    </section>`;
  }).join('');
  const progress = completion(tasks);
  document.querySelector('#progress-percent').textContent = `${progress.percent}%`;
  document.querySelector('#completion-count').textContent = `${progress.done} of ${progress.total} tasks`;
  document.querySelector('#ring-value').style.strokeDasharray = `${progress.percent} 100`;
  document.querySelector('.progress-panel').setAttribute('aria-label', `Launch progress: ${progress.done} of ${progress.total} tasks complete`);
  const result = document.querySelector('#search-result');
  result.hidden = !search.value.trim();
  result.textContent = `${filtered.length} ${filtered.length === 1 ? 'task matches' : 'tasks match'} “${search.value.trim()}”`;
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
  if (!element || event.target.closest('select')) return;
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
  search.value = '';
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
