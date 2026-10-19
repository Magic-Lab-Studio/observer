import { t } from '../i18n.ts';

/** Why a request failed, so the page can say what to do instead of always blaming the backend. */
export type ApiErrorKind = 'network' | 'auth' | 'not_found' | 'invalid' | 'rate_limited' | 'server' | 'unknown';

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly detail: string | null;

  constructor(kind: ApiErrorKind, status: number | null, detail: string | null) {
    super(describeApiError(kind, status, detail));
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
    this.detail = detail;
  }
}

export function classifyStatus(status: number): ApiErrorKind {
  if (status === 401 || status === 403) return 'auth';
  if (status === 404) return 'not_found';
  if (status === 429) return 'rate_limited';
  if (status === 400 || status === 409 || status === 413 || status === 422) return 'invalid';
  if (status >= 500) return 'server';
  return 'unknown';
}

/** FastAPI answers {"detail": "..."} or {"detail": [{"msg": "..."}]}; keep one short line. */
export function detailFromBody(body: unknown): string | null {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') return detail.slice(0, 200);
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { msg?: unknown };
    if (typeof first?.msg === 'string') return first.msg.slice(0, 200);
  }
  return null;
}

export function describeApiError(kind: ApiErrorKind, status: number | null, detail: string | null): string {
  const tail = detail ? `: ${detail}` : '';
  switch (kind) {
    case 'network':
      return t('No hay conexión con el backend del Observer. Verifica que esté corriendo.');
    case 'auth':
      return t('El backend rechazó el acceso (HTTP {s}): falta la clave de API o no es válida.', { s: status ?? '' });
    case 'not_found':
      return t('No encontrado (HTTP 404)') + tail;
    case 'invalid':
      return t('Solicitud inválida (HTTP {s})', { s: status ?? '' }) + tail;
    case 'rate_limited':
      return t('Demasiadas solicitudes (HTTP 429); espera un momento y reintenta.');
    case 'server':
      return t('Error interno del backend (HTTP {s}).', { s: status ?? '' }) + tail;
    default:
      return t('Respuesta inesperada del backend (HTTP {s})', { s: status ?? '' }) + tail;
  }
}
