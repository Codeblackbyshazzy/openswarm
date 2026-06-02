// Run: node --test frontend/src/shared/interactiveRanking.test.ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { rankAndCapInteractives, goalKeywords, type RankItem } from './interactiveRanking.ts';

const mk = (role: string, name: string, id = 0): RankItem => ({ role, name, backendNodeId: id });

test('consecutive twins with same role+name collapse', () => {
  const { shown } = rankAndCapInteractives([
    mk('button', 'Like', 1),
    mk('button', 'Like', 2),
    mk('button', 'Like', 3),
    mk('button', 'Share', 4),
  ]);
  assert.deepEqual(shown.map((x) => x.backendNodeId), [1, 4]);
});

test('same role+name in different frames (sessionId) is NOT collapsed at the seam', () => {
  const root: RankItem = { role: 'button', name: 'Close', backendNodeId: 1 };
  const child: RankItem = { role: 'button', name: 'Close', backendNodeId: 2, sessionId: 'frameA' };
  const { shown } = rankAndCapInteractives([root, child]);
  // both survive: they are genuinely different elements across a frame boundary
  assert.equal(shown.length, 2);
  assert.ok(shown.some((x) => x.sessionId === 'frameA'));
});

test('non-consecutive same name is preserved (real list items)', () => {
  const { shown } = rankAndCapInteractives([
    mk('button', 'Add to cart', 1),
    mk('link', 'Widget A', 2),
    mk('button', 'Add to cart', 3),
    mk('link', 'Widget B', 4),
    mk('button', 'Add to cart', 5),
  ]);
  // all three "Add to cart" survive because they are not back-to-back
  const carts = shown.filter((x) => x.name === 'Add to cart');
  assert.equal(carts.length, 3);
});

test('ranks by role priority: input > button/link > toggle > option', () => {
  const { shown } = rankAndCapInteractives([
    mk('option', 'opt', 1),
    mk('checkbox', 'agree', 2),
    mk('button', 'Go', 3),
    mk('textbox', 'email', 4),
  ]);
  assert.deepEqual(shown.map((x) => x.role), ['textbox', 'button', 'checkbox', 'option']);
});

test('preserves document order within the same priority tier', () => {
  const { shown } = rankAndCapInteractives([
    mk('button', 'First', 1),
    mk('link', 'Second', 2),
    mk('button', 'Third', 3),
  ]);
  // button and link share tier 1; original order First, Second, Third holds
  assert.deepEqual(shown.map((x) => x.backendNodeId), [1, 2, 3]);
});

test('caps to N and reports the truncated remainder', () => {
  const items = Array.from({ length: 150 }, (_, i) => mk('link', `L${i}`, i));
  const { shown, truncated } = rankAndCapInteractives(items, { cap: 60 });
  assert.equal(shown.length, 60);
  assert.equal(truncated, 90);
});

test('cap of 0 means no cap', () => {
  const items = Array.from({ length: 5 }, (_, i) => mk('link', `L${i}`, i));
  const { shown, truncated } = rankAndCapInteractives(items, { cap: 0 });
  assert.equal(shown.length, 5);
  assert.equal(truncated, 0);
});

test('goal-matched elements float to the top, above role priority', () => {
  const { shown } = rankAndCapInteractives([
    mk('textbox', 'Search', 1),
    mk('link', 'Account settings', 2),
    mk('button', 'Save', 3),
  ], { goal: 'open the settings page' });
  // "Account settings" matches "settings" and jumps ahead of the textbox
  assert.equal(shown[0].backendNodeId, 2);
});

test('goal match survives the cap even when buried deep', () => {
  const items = Array.from({ length: 100 }, (_, i) => mk('link', `Item ${i}`, i));
  items.push(mk('button', 'Checkout now', 999));
  const { shown } = rankAndCapInteractives(items, { cap: 30, goal: 'click checkout' });
  assert.ok(shown.some((x) => x.backendNodeId === 999), 'checkout should be retained');
  assert.equal(shown[0].backendNodeId, 999);
});

test('no goal leaves pure role-priority ordering', () => {
  const { shown } = rankAndCapInteractives([
    mk('option', 'opt', 1),
    mk('button', 'Go', 2),
    mk('textbox', 'email', 3),
  ]);
  assert.deepEqual(shown.map((x) => x.role), ['textbox', 'button', 'option']);
});

test('goalKeywords strips stopwords, action verbs, and short tokens', () => {
  assert.deepEqual(goalKeywords('Click the Submit button to send'), ['submit', 'send']);
  assert.deepEqual(goalKeywords('type into the search box'), ['search']);
  assert.deepEqual(goalKeywords(''), []);
});

test('empty input yields empty result', () => {
  const { shown, truncated } = rankAndCapInteractives([]);
  assert.equal(shown.length, 0);
  assert.equal(truncated, 0);
});

test('unknown role falls into the middle tier, not dropped', () => {
  const { shown } = rankAndCapInteractives([
    mk('option', 'opt', 1),
    mk('weirdrole', 'mystery', 2),
    mk('textbox', 'field', 3),
  ]);
  assert.deepEqual(shown.map((x) => x.role), ['textbox', 'weirdrole', 'option']);
});
