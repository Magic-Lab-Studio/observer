import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import { test } from 'node:test';
import ts from 'typescript';
import { parseApiDate } from '../src/dates.ts';

for (const zone of ['UTC', 'America/Bogota', 'Asia/Tokyo', 'America/New_York']) {
  test(`API timestamps remain UTC in ${zone}`, (t) => {
    const previous = process.env.TZ;
    process.env.TZ = zone;
    t.after(() => {
      if (previous === undefined) delete process.env.TZ;
      else process.env.TZ = previous;
    });
    const expected = Date.UTC(2026, 9, 7, 18, 2, 55, 72);
    for (const value of [
      '2026-10-07T18:02:55.072921',
      '2026-10-07T18:02:55.072921Z',
      '2026-10-07T13:02:55.072921-05:00',
      expected,
      new Date(expected),
    ]) assert.equal(parseApiDate(value).getTime(), expected);
    assert.equal(parseApiDate('2026-10-07T18:02').toISOString(), '2026-10-07T18:02:00.000Z');
    assert.equal(parseApiDate('2026-10-07T18:02:55').toISOString(), '2026-10-07T18:02:55.000Z');
    // The DST jump must not change an API duration or interpret UTC as local time.
    assert.equal(parseApiDate('2026-03-08T07:00:00') - parseApiDate('2026-03-08T06:59:59'), 1000);
    if (zone === 'America/Bogota') assert.equal(parseApiDate(expected).getHours(), 13);
    assert.ok(Number.isNaN(parseApiDate('invalid').getTime()));
  });
}

test('display code routes date values through parseApiDate', async () => {
  const root = new URL('../src/', import.meta.url);
  const files = await readdir(root, { recursive: true });
  for (const file of files.filter((name) => /\.tsx?$/.test(name) && name !== 'dates.ts')) {
    const source = ts.createSourceFile(file, await readFile(new URL(file, root), 'utf8'), ts.ScriptTarget.Latest, true);
    const visit = (node) => {
      if (ts.isNewExpression(node) && node.expression.getText(source) === 'Date') {
        assert.equal(node.arguments?.length ?? 0, 0, `${file}: use parseApiDate for timestamp values`);
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }
});
