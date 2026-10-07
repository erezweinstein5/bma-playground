import test from 'node:test';
import assert from 'node:assert/strict';
import { completion, filterTasks, initialTasks, moveTask, restoreTasks } from '../../src/board.js';

test('initial board contains six tasks and two completed tasks', () => {
  assert.deepEqual(completion(initialTasks()), { done: 2, total: 6, percent: 33 });
});

test('moving between active columns keeps unrelated tasks and input intact', () => {
  const before = initialTasks();
  const moved = moveTask(before, 'welcome', 'doing');
  assert.equal(moved.find((task) => task.id === 'welcome').status, 'doing');
  assert.equal(before.find((task) => task.id === 'welcome').status, 'todo');
  assert.deepEqual(moved.filter((task) => task.id !== 'welcome'), before.filter((task) => task.id !== 'welcome'));
});

test('unknown destinations and task ids cannot lose a card', () => {
  const before = initialTasks();
  assert.deepEqual(moveTask(before, 'welcome', 'missing'), before);
  assert.deepEqual(moveTask(before, 'missing', 'doing'), before);
});

test('search is case insensitive and matches task copy and people', () => {
  assert.equal(filterTasks(initialTasks(), '  WELCOME ').length, 1);
  assert.equal(filterTasks(initialTasks(), 'maya').length, 3);
  assert.equal(filterTasks(initialTasks(), 'does not exist').length, 0);
});

test('restore preserves valid moves while never trusting stored display text', () => {
  const saved = moveTask(initialTasks(), 'welcome', 'doing');
  saved[0].title = '<script>doSomething()</script>';
  const restored = restoreTasks(JSON.stringify(saved));
  assert.equal(restored[0].status, 'doing');
  assert.equal(restored[0].title, 'Polish the welcome screen');
});

test('invalid, incomplete, or duplicate persisted state resets safely', () => {
  for (const raw of ['bad json', '{}', 'null', '[]', '[null]', JSON.stringify(Array(6).fill(initialTasks()[0]))]) {
    assert.deepEqual(restoreTasks(raw), initialTasks());
  }
});

test('each reset returns independent data; an empty board has zero completion', () => {
  const first = initialTasks();
  first[0].status = 'doing';
  assert.equal(initialTasks()[0].status, 'todo');
  assert.deepEqual(completion([]), { done: 0, total: 0, percent: 0 });
});
