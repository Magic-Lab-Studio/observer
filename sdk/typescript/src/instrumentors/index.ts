/**
 * Auto-instrumentation for LLM libraries
 */

import { instrument as instrumentOpenAI, uninstrument as uninstrumentOpenAI } from "./openai";
import { instrument as instrumentAnthropic, uninstrument as uninstrumentAnthropic } from "./anthropic";

export interface InstrumentOptions {
  openai?: boolean;
  anthropic?: boolean;
  /**
   * The provider modules, for ES modules and bundlers where `require` is not available:
   * `instrument({ openai: true, modules: { openai: await import("openai") } })`.
   */
  modules?: { openai?: unknown; anthropic?: unknown };
}

export function instrument(options: InstrumentOptions = {}): void {
  if (options.openai === true) {
    instrumentOpenAI(options.modules?.openai);
  }
  if (options.anthropic === true) {
    instrumentAnthropic(options.modules?.anthropic);
  }
}

export function uninstrument(options: InstrumentOptions = {}): void {
  if (options.openai === true) {
    uninstrumentOpenAI();
  }
  if (options.anthropic === true) {
    uninstrumentAnthropic();
  }
}
