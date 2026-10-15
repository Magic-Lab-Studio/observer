import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import { test } from 'node:test';
import ts from 'typescript';
import { EN } from '../src/i18n-en.ts';
import { detectLang, getLang, localeTag, setLang, t, translate } from '../src/i18n.ts';

test('the browser language picks Spanish for es-*, English otherwise; a stored choice wins', () => {
  assert.equal(detectLang(null, 'es-CO'), 'es');
  assert.equal(detectLang(undefined, 'en-GB'), 'en');
  assert.equal(detectLang('', 'fr'), 'en');
  assert.equal(detectLang('en', 'es-ES'), 'en');
  assert.equal(detectLang('es', 'en-US'), 'es');
  assert.equal(detectLang('xx', 'es'), 'es');
});

test('translate falls back to the Spanish text and fills {params}', () => {
  assert.equal(translate('es', 'hace {n} min', { n: 5 }), 'hace 5 min');
  assert.equal(translate('en', 'hace {n} min', { n: 5 }), '5 min ago');
  assert.equal(translate('en', 'texto que no existe'), 'texto que no existe');
  assert.equal(translate('en', 'Página {p} de {n}', { p: 2, n: 9 }), 'Page 2 of 9');
});

test('the current language drives t() and the number/date locale', () => {
  setLang('en');
  assert.equal(getLang(), 'en');
  assert.equal(t('Resumen'), 'Overview');
  assert.equal(localeTag(), 'en-US');
  setLang('es');
  assert.equal(t('Resumen'), 'Resumen');
  assert.equal(localeTag(), 'es-CO');
});

test('every placeholder survives translation', () => {
  for (const [key, value] of Object.entries(EN)) {
    const want = [...key.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
    const got = [...value.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
    assert.deepEqual(got, want, `placeholders differ for "${key}"`);
  }
});

test('every t("literal") in the source has an English entry', async () => {
  const root = new URL('../src/', import.meta.url);
  const files = (await readdir(root, { recursive: true })).filter((n) => /\.tsx?$/.test(n) && !/i18n/.test(n));
  const missing = [];
  for (const file of files) {
    const sf = ts.createSourceFile(file, await readFile(new URL(file, root), 'utf8'), ts.ScriptTarget.Latest, true,
      file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
    const visit = (node) => {
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 't') {
        const arg = node.arguments[0];
        if (arg && (ts.isStringLiteral(arg) || ts.isNoSubstitutionTemplateLiteral(arg)) && !(arg.text in EN)) {
          missing.push(`${file}: ${arg.text}`);
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(sf);
  }
  assert.deepEqual(missing, [], `Add these to src/i18n-en.ts:\n${missing.join('\n')}`);
});
