import { parseApiDate } from '../dates';
import { fmtMs, timeAgo } from '../helpers/format';
import { StatusPill } from './ui';

export function StatusBadge({ status }: { status: string }) {
  return <StatusPill status={status} />;
}

export function formatDuration(start: string, end: string | null) {
  if (!end) return '–';
  return fmtMs(parseApiDate(end).getTime() - parseApiDate(start).getTime());
}

export function formatTimeAgo(dateStr: string) {
  return timeAgo(dateStr);
}
