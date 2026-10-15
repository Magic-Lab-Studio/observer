import type { ReactNode } from 'react';

export const PALETTE = ['#6c8cff', '#3fb68b', '#e5a33b', '#d6657e', '#9b7bea', '#4fb7d6', '#8a94a8'];

export function Icon({ name, className = 'size-4' }: { name: string; className?: string }) {
  const paths: Record<string, string> = {
    overview: 'M3 3h7v7H3zM14 3h7v4h-7zM14 11h7v10h-7zM3 14h7v7H3z',
    traces: 'M4 6h16M4 12h10M4 18h13',
    evals: 'M5 12l4 4 10-10',
    refresh: 'M20 11a8 8 0 10-2.3 5.7M20 4v7h-7',
    search: 'M11 19a8 8 0 100-16 8 8 0 000 16zM21 21l-4.3-4.3',
    back: 'M15 18l-6-6 6-6',
    chevron: 'M9 6l6 6-6 6',
    warn: 'M12 3l10 18H2zM12 10v5M12 18v.01',
    menu: 'M4 6h16M4 12h16M4 18h16',
    bell: 'M6 8a6 6 0 1112 0c0 7 3 8 3 8H3s3-1 3-8zM10 20a2 2 0 004 0',
    providers: 'M5 12h4M15 12h4M9 12a3 3 0 106 0 3 3 0 00-6 0zM12 5V3M12 21v-2',
  };
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={paths[name] ?? ''} />
    </svg>
  );
}

export function Card({
  title, subtitle, actions, children, className = '', bodyClass = 'p-4',
}: {
  title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode;
  className?: string; bodyClass?: string;
}) {
  return (
    <section className={`rounded-xl border border-line bg-panel min-w-0 ${className}`}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 px-4 pt-3.5">
          <div className="min-w-0">
            <h2 className="text-[13px] font-semibold text-ink">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-faint">{subtitle}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className={bodyClass}>{children}</div>
    </section>
  );
}

export function Sparkline({ data, color = '#6c8cff', height = 32 }: { data: number[]; color?: string; height?: number }) {
  if (data.length < 2) return <div style={{ height }} />;
  const w = 120;
  const max = Math.max(...data, 1e-9);
  const min = Math.min(...data, 0);
  const range = max - min || 1;
  const pts = data.map((v, i) => [(i / (data.length - 1)) * w, height - 2 - ((v - min) / range) * (height - 4)]);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const area = `${line} L${w},${height} L0,${height} Z`;
  const id = `sp${color.replace('#', '')}${data.length}`;
  return (
    <svg viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" className="w-full" style={{ height }} aria-hidden="true">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity=".28" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function Kpi({
  label, value, hint, series, color, tone = 'neutral',
}: {
  label: string; value: ReactNode; hint?: ReactNode; series?: number[]; color?: string;
  tone?: 'neutral' | 'good' | 'bad' | 'warn';
}) {
  const toneClass = { neutral: 'text-ink', good: 'text-ok', bad: 'text-bad', warn: 'text-warn' }[tone];
  return (
    <div className="rounded-xl border border-line bg-panel p-4 min-w-0 flex flex-col">
      <dt className="text-xs font-medium text-dim truncate">{label}</dt>
      <dd className={`mt-1.5 text-[22px] sm:text-[26px] leading-none truncate font-semibold tracking-tight ${toneClass}`}>{value}</dd>
      <div className="mt-1 text-xs text-faint min-h-4 truncate">{hint}</div>
      {series && <div className="mt-2 -mx-1"><Sparkline data={series} color={color} /></div>}
    </div>
  );
}

export function Segmented<T extends string | number>({
  options, value, onChange, label,
}: { options: { label: string; value: T }[]; value: T; onChange: (v: T) => void; label: string }) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-lg border border-line bg-panel p-0.5">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
          className={`whitespace-nowrap px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
            o.value === value ? 'bg-raised text-ink shadow-sm' : 'text-dim hover:text-ink'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function StatusDot({ status }: { status: string }) {
  const color = status === 'ok' ? 'bg-ok' : status === 'error' ? 'bg-bad' : 'bg-faint';
  return <span className={`inline-block size-2 rounded-full ${color}`} aria-hidden="true" />;
}

export function StatusPill({ status }: { status: string }) {
  const cls = status === 'ok'
    ? 'bg-ok/10 text-ok border-ok/25'
    : status === 'error'
      ? 'bg-bad/10 text-bad border-bad/25'
      : 'bg-raised text-dim border-line-strong';
  const text = status === 'ok' ? 'OK' : status === 'error' ? 'Error' : status;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium ${cls}`}>
      <StatusDot status={status} />{text}
    </span>
  );
}

export function Chip({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'accent' | 'warn' }) {
  const cls = { neutral: 'bg-raised text-dim', accent: 'bg-accent/15 text-accent', warn: 'bg-warn/15 text-warn' }[tone];
  return <span className={`inline-block rounded-md px-1.5 py-0.5 text-[11px] font-medium ${cls}`}>{children}</span>;
}

export function Meter({ rate, tone }: { rate: number; tone?: 'bad' | 'warn' | 'accent' }) {
  const color = { bad: 'bg-bad', warn: 'bg-warn', accent: 'bg-accent' }[tone ?? 'accent'];
  return (
    <div className="h-1.5 w-full rounded-full bg-raised overflow-hidden">
      <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.min(100, Math.max(rate > 0 ? 1.5 : 0, rate * 100))}%` }} />
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 py-12 text-center">
      <p className="text-sm text-dim">{title}</p>
      {hint && <p className="text-xs text-faint">{hint}</p>}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-xl border border-bad/30 bg-bad/5 p-4 flex items-start gap-3">
      <Icon name="warn" className="size-5 text-bad shrink-0 mt-0.5" />
      <div className="min-w-0">
        <p className="text-sm text-bad font-medium">No se pudo cargar: {message}</p>
        <p className="text-xs text-faint mt-1">Verifica que el backend del Observer esté corriendo.</p>
        {onRetry && (
          <button type="button" onClick={onRetry}
            className="mt-2 rounded-md border border-line-strong px-2.5 py-1 text-xs text-ink hover:bg-raised">
            Reintentar
          </button>
        )}
      </div>
    </div>
  );
}

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`skeleton ${className}`} />;
}

export function ChartTooltip({
  active, payload, label, valueFormatter,
}: {
  active?: boolean; label?: string | number;
  payload?: { name?: string; value?: number | string; color?: string; dataKey?: string; payload?: Record<string, unknown> }[];
  valueFormatter?: (v: number | string, name: string) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-line-strong bg-raised/95 px-3 py-2 text-xs shadow-xl backdrop-blur">
      {label != null && <div className="mb-1 font-medium text-ink">{String(label)}</div>}
      {payload.map((p) => (
        <div key={String(p.dataKey ?? p.name)} className="flex items-center gap-2 text-dim">
          <span className="size-2 rounded-sm" style={{ background: p.color }} />
          <span>{p.name}</span>
          <span className="ml-auto pl-3 font-medium text-ink">
            {valueFormatter ? valueFormatter(p.value ?? 0, p.name ?? '') : String(p.value)}
          </span>
        </div>
      ))}
    </div>
  );
}
