import { OUTCOME_LABEL, outcomeTone } from '../helpers/outcomes';
import { t } from '../i18n';

const TONE = {
  good: 'bg-ok/15 text-ok',
  warn: 'bg-warn/15 text-warn',
  bad: 'bg-bad/15 text-bad',
  neutral: 'bg-raised text-dim',
} as const;

/** How a turn ended, as a small chip; nothing for an unknown outcome. */
export default function OutcomeChip({ outcome }: { outcome?: string | null }) {
  if (!outcome || outcome === 'unknown') return null;
  return (
    <span className={`ml-2 inline-block whitespace-nowrap rounded-md px-1.5 py-0.5 align-middle text-[10px] font-medium ${TONE[outcomeTone(outcome)]}`}>
      {t(OUTCOME_LABEL[outcome] ?? outcome)}
    </span>
  );
}
