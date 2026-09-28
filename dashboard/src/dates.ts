// The backend stores UTC timestamps without a zone ("2026-09-28T04:25:01.243063").
// new Date() reads those as local time, so in UTC-5 every trace looked 5 hours in the
// future ("-17989s ago"). Treat a zone-less timestamp as UTC.
const ZONELESS = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/;

export function parseApiDate(value: string | number | Date): Date {
  if (typeof value === 'string' && ZONELESS.test(value)) {
    return new Date(`${value}Z`);
  }
  return new Date(value);
}
