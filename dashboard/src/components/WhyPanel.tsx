import type { OutcomeInfo, Tone } from '../helpers/outcomes';
import { whyLines } from '../helpers/outcomes';
import { t } from '../i18n';

const TONE: Record<Tone, string> = {
  good: 'border-ok/30 bg-ok/5 text-ok',
  warn: 'border-warn/30 bg-warn/5 text-warn',
  bad: 'border-bad/30 bg-bad/5 text-bad',
  neutral: 'border-line bg-panel text-dim',
};

/** "Why did the turn end like this?": outcome, source, reason, the gate that stopped it. Metadata only. */
export default function WhyPanel({ info }: { info: OutcomeInfo | null | undefined }) {
  const lines = whyLines(info);
  if (!lines.length) return null;
  const [head, ...rest] = lines;
  return (
    <section aria-label={t('¿Por qué terminó así?')} className={`rounded-xl border p-3 text-xs ${TONE[head.tone]}`}>
      <p className="font-semibold">
        {t('¿Por qué terminó así?')} <span className="font-normal">{t(head.label)} · {t(head.detail)}</span>
      </p>
      {rest.length > 0 && (
        <ul className="mt-1.5 space-y-0.5 text-dim">
          {rest.map((line) => (
            <li key={line.key}>
              <span className="text-ink">{t(line.label)}:</span> <span className="font-mono">{line.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
