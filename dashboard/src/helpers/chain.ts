export interface Attempt { backend: string; model: string; result: string; ms: number | null }

/** `groq:qwen/qwen3.8-27b=429@412ms>llamacpp:-=ok` -> attempts. Mirrors the backend parser; unreadable parts are skipped. */
export function parseAttempts(text: string): Attempt[] {
  const out: Attempt[] = [];
  for (const part of (text || '').split('>')) {
    const eq = part.lastIndexOf('=');
    if (eq < 0) continue;
    const head = part.slice(0, eq);
    let tail = part.slice(eq + 1);
    const colon = head.indexOf(':');
    if (colon < 0) continue;
    let ms: number | null = null;
    const at = tail.lastIndexOf('@');
    if (at >= 0 && tail.endsWith('ms')) {
      const n = Number(tail.slice(at + 1, -2));
      ms = Number.isFinite(n) ? n : null;
      tail = tail.slice(0, at);
    }
    const model = head.slice(colon + 1);
    if (!tail) continue;
    out.push({ backend: head.slice(0, colon).trim().toLowerCase(), model: model === '-' ? '' : model, result: tail, ms });
  }
  return out;
}

export function resultTone(result: string): 'ok' | 'warn' | 'bad' | 'skip' {
  if (result === 'ok') return 'ok';
  if (result === '429' || result === 'timeout') return 'warn';
  if (result.startsWith('skipped')) return 'skip';
  return 'bad';
}

export function resultLabel(result: string): string {
  if (result === 'ok') return 'respondió';
  if (result === '429') return 'límite (429)';
  if (result === 'timeout') return 'tiempo agotado';
  if (result.startsWith('skipped')) return 'omitido (sin cuota)';
  return result.replace(/^error:/, 'error ');
}

/** The same model appears as "groq · qwen/qwen3.8-27b" (what answered) and "qwen3.8-27b-groq" (registry id): one key for both. */
export function canonicalModel(key: string): string {
  let k = key.toLowerCase();
  if (k.includes(' · ')) k = k.split(' · ').slice(1).join(' · ');
  k = k.replace(/^[^/]+\//, '').replace(/-(groq|ollama|cloud)$/, '');
  return k.replace(/\.gguf$/, '');
}

export function mergeModelRows<T extends { key: string; count: number; rate: number }>(rows: T[]): T[] {
  const merged = new Map<string, T>();
  for (const r of rows) {
    const k = canonicalModel(r.key);
    const prev = merged.get(k);
    if (!prev) merged.set(k, { ...r });
    else {
      prev.count += r.count;
      prev.rate += r.rate;
      if (r.key.includes(' · ') && !prev.key.includes(' · ')) prev.key = r.key;
    }
  }
  return Array.from(merged.values()).sort((a, b) => b.count - a.count);
}
