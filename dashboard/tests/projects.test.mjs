import assert from 'node:assert/strict';
import { test } from 'node:test';
import { FALLBACK_PROJECT, cleanProject, pickProject, projectOptions } from '../src/helpers/projects.ts';

const projects = [
  { project_id: 'manitos', last_trace_at: '2026-10-24T10:00:00+00:00' },
  { project_id: 'my-agent', last_trace_at: '2026-10-25T09:30:00+00:00' },
];

test('without a choice the project with the most recent trace is shown', () => {
  assert.equal(pickProject(projects, null, null), 'my-agent');
  assert.equal(pickProject([{ project_id: 'my-agent', last_trace_at: '2026-10-25T09:30:00+00:00' }], '', ''), 'my-agent');
  assert.equal(pickProject([...projects].reverse(), null, null), 'my-agent');
});

test('the URL wins, then a stored choice that still has traces', () => {
  assert.equal(pickProject(projects, 'manitos', 'my-agent'), 'manitos');
  assert.equal(pickProject(projects, null, 'manitos'), 'manitos');
  assert.equal(pickProject(projects, '  ', 'gone'), 'my-agent'); // the stored project has no traces now
  assert.equal(pickProject(projects, 'not-yet', null), 'not-yet'); // a shared link to a new project
});

test('a fresh install falls back to the stored or the default project', () => {
  assert.equal(pickProject([], null, null), FALLBACK_PROJECT);
  assert.equal(pickProject([], null, 'my-agent'), 'my-agent');
});

test('ids are trimmed and bounded, and the current project is always an option', () => {
  assert.equal(cleanProject(' my-agent '), 'my-agent');
  assert.equal(cleanProject('x'.repeat(129)), '');
  assert.equal(cleanProject(undefined), '');
  assert.deepEqual(projectOptions(projects, 'manitos'), ['manitos', 'my-agent']);
  assert.deepEqual(projectOptions(projects, 'new'), ['new', 'manitos', 'my-agent']);
});
