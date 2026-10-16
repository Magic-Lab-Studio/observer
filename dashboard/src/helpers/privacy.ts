/** Pure helpers for the privacy page (no React imports, so Node can test them). */

import type { CaptureMode, PrivacyReport } from '../types';

export const MODE_LABEL: Record<CaptureMode, string> = {
  metadata: 'Solo metadatos',
  redacted: 'Redactado',
  full: 'Completo',
};

export const MODE_HINT: Record<CaptureMode, string> = {
  metadata: 'No guarda entradas ni salidas y descarta atributos con nombre de contenido.',
  redacted: 'Guarda todo pero enmascara correos, teléfonos, tarjetas, identificaciones, tokens y claves.',
  full: 'Guarda todo tal como llega.',
};

/** How much of the latest spans a mode would still have to clean: "stored content" counts. */
export function exposure(r: PrivacyReport): number {
  return r.with_input + r.with_output + r.with_content_like_attributes;
}

/** Whether stored data contradicts the mode the project is set to (so "apply to stored" matters). */
export function needsEnforcement(mode: CaptureMode | null, r: PrivacyReport): boolean {
  if (mode === 'metadata') return exposure(r) > 0;
  if (mode === 'redacted') return r.with_sensitive_patterns > 0;
  return false;
}
