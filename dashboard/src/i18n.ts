// Tiny i18n: the Spanish text is the key, English comes from a dictionary, a missing entry falls
// back to the Spanish text. Pure and dependency-free so Node can load it in tests.
import { EN } from './i18n-en.ts';

export type Lang = 'es' | 'en';
export const LANGS: { value: Lang; label: string }[] = [
  { value: 'es', label: 'ES' },
  { value: 'en', label: 'EN' },
];

let current: Lang = 'es';

export function setLang(lang: Lang): void {
  current = lang;
}

export function getLang(): Lang {
  return current;
}

/** BCP 47 tag used for dates and numbers. */
export function localeTag(lang: Lang = current): string {
  return lang === 'en' ? 'en-US' : 'es-CO';
}

/** Stored choice first, then the browser language (Spanish if it starts with "es", else English). */
export function detectLang(stored: string | null | undefined, browser: string | null | undefined): Lang {
  if (stored === 'es' || stored === 'en') return stored;
  return (browser ?? '').toLowerCase().startsWith('es') ? 'es' : 'en';
}

export function translate(lang: Lang, key: string, params?: Record<string, string | number>): string {
  let text = lang === 'en' ? (EN[key] ?? key) : key;
  if (params) {
    for (const [name, value] of Object.entries(params)) text = text.split(`{${name}}`).join(String(value));
  }
  return text;
}

/** Translate into the current language: `t('Resumen')`, `t('hace {n} min', { n: 5 })`. */
export function t(key: string, params?: Record<string, string | number>): string {
  return translate(current, key, params);
}
