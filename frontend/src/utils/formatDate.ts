/** Format ISO date string (2021-01-01T...) as dd.mm.yyyy */
export function formatDateRu(value: unknown): string | null {
  if (!value) return null;
  const str = String(value);
  const m = str.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[3]}.${m[2]}.${m[1]}`;
  return null;
}
