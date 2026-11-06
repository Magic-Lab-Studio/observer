/**
 * Every page in Spanish and English: it loads without JavaScript errors, and the main flows work
 * (overview with traces, trace detail with "why did it end like this", incomplete evaluation,
 * groundedness mode saved, Explore query). External evaluation B3.
 */
import { expect, test, type Page } from '@playwright/test';
import { DATASET, PROJECT } from './global-setup';

type Lang = 'es' | 'en';
const LANGS: Lang[] = ['es', 'en'];

const T: Record<string, Record<Lang, string>> = {
  resumen: { es: 'Resumen', en: 'Overview' },
  why: { es: '¿Por qué terminó así?', en: 'Why did it end like this?' },
  incompleta: { es: 'Incompleta', en: 'Incomplete' },
  sustento: { es: 'Sustento de las respuestas', en: 'Groundedness of replies' },
  guardar: { es: 'Guardar', en: 'Save' },
  guardado: { es: 'Guardado', en: 'Saved' },
  modo: { es: 'Modo', en: 'Mode' },
  proyecto: { es: 'Proyecto', en: 'Project' },
};

const PAGES = ['/', '/traces', '/providers', '/alerts', '/explore', '/evaluations', '/datasets',
  '/governance', '/privacy'];

/** Collects uncaught exceptions and console errors; the live socket may log reconnects. */
function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/WebSocket|\/ws\/live/.test(m.text())) errors.push(`console: ${m.text()}`);
  });
  return errors;
}

async function open(page: Page, lang: Lang, path: string): Promise<void> {
  await page.addInitScript(([l, p]) => {
    localStorage.setItem('observer.lang', l);
    localStorage.setItem('observer.project', p);
  }, [lang, PROJECT]);
  const sep = path.includes('?') ? '&' : '?';
  await page.goto(`${path}${sep}project=${PROJECT}`);
  await expect(page.locator('html')).toHaveAttribute('lang', lang);
}

for (const lang of LANGS) {
  test.describe(`dashboard (${lang})`, () => {
    for (const path of PAGES) {
      test(`${path} loads without JavaScript errors`, async ({ page }) => {
        const errors = watchErrors(page);
        await open(page, lang, path);
        await expect(page.locator('main')).toBeVisible();
        await page.waitForLoadState('networkidle');
        expect(errors).toEqual([]);
      });
    }

    test("the cases page lists the selected project's datasets", async ({ page }) => {
      await open(page, lang, '/datasets');
      await expect(page.getByText(DATASET)).toBeVisible();
    });

    test('the overview shows the planted traces', async ({ page }) => {
      await open(page, lang, '/');
      await expect(page.getByRole('heading', { name: T.resumen[lang], exact: true })).toBeVisible();
      await expect(page.getByText('e2e_answer_question').first()).toBeVisible();
    });

    test('a trace opens its detail with the "why did it end" panel', async ({ page }) => {
      const errors = watchErrors(page);
      await open(page, lang, '/traces');
      await page.getByRole('link', { name: 'e2e_failed_turn', exact: true }).first().click();
      await expect(page).toHaveURL(/\/traces\/[0-9a-f-]{36}/);
      await expect(page.getByRole('region', { name: T.why[lang] })).toBeVisible();
      await expect(page.getByText('search_prices').first()).toBeVisible();
      expect(errors).toEqual([]);
    });

    test('an evaluation the judge could not complete reads "incomplete"', async ({ page }) => {
      await open(page, lang, '/evaluations');
      await expect(page.getByText(T.incompleta[lang], { exact: true }).first()).toBeVisible();
    });

    test('the groundedness mode is saved and survives a reload', async ({ page }) => {
      // The card with that title and a form (its title alone is in an inner element).
      const card = () => page.locator('div')
        .filter({ has: page.getByText(T.sustento[lang], { exact: true }) })
        .filter({ has: page.locator('select') }).last();
      // A label wraps its select, so its text also holds the options: match how it starts.
      const field = (label: string) =>
        card().locator('label').filter({ hasText: new RegExp(`^${label}`) }).locator('select');
      await open(page, lang, '/privacy');
      await field(T.proyecto[lang]).selectOption(PROJECT);
      const mode = field(T.modo[lang]);
      const next = (await mode.inputValue()) === 'rules' ? 'off' : 'rules';
      await mode.selectOption(next);
      await card().getByRole('button', { name: T.guardar[lang], exact: true }).click();
      await expect(page.getByRole('status').filter({ hasText: T.guardado[lang] })).toBeVisible();
      await page.reload();
      await field(T.proyecto[lang]).selectOption(PROJECT);
      await expect(field(T.modo[lang])).toHaveValue(next);
    });

    test('Explore runs a query and shows its result', async ({ page }) => {
      const answered = page.waitForResponse((r) => r.url().includes('/v1/analytics/query') &&
        !r.url().includes('/schema') && r.request().method() === 'POST');
      await open(page, lang, '/explore');
      expect((await answered).status()).toBe(200);
      await expect(page.locator('table').first()).toBeVisible();
    });
  });
}
