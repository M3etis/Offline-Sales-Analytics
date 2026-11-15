/**
 * Единые форматтеры чисел и дат. Один формат на весь интерфейс:
 * «74,1 млн», «2 000», «+12,4%», «15 ноя 2025».
 */

const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const MONTHS_NOMINATIVE = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const MONTHS_FULL = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
const MINUS = '−';

const intFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
const oneDecimal = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const upToTwo = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });

// Конструктор Intl.NumberFormat дорогой, а форматтеры вызываются на каждую ячейку таблиц — кэшируем
const numberFormats = new Map<string, Intl.NumberFormat>();
function numberFormat(minFractionDigits: number, maxFractionDigits: number): Intl.NumberFormat {
  const key = `${minFractionDigits}:${maxFractionDigits}`;
  let fmt = numberFormats.get(key);
  if (!fmt) {
    fmt = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: minFractionDigits, maximumFractionDigits: maxFractionDigits });
    numberFormats.set(key, fmt);
  }
  return fmt;
}

function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

function withMinus(text: string): string {
  return text.replace(/^-/, MINUS);
}

/** «2 000», «1 234,56» */
export function formatNumber(value: unknown, maxFractionDigits = 0): string {
  const n = toNumber(value);
  if (n === null) return '—';
  const fmt = maxFractionDigits === 0 ? intFormat : maxFractionDigits === 2 ? upToTwo : numberFormat(0, maxFractionDigits);
  return withMinus(fmt.format(n));
}

/** Точное значение суммы: «74 105 225,3» */
export function formatExact(value: unknown): string {
  const n = toNumber(value);
  if (n === null) return '—';
  return withMinus(upToTwo.format(n));
}

/** Компактная сумма: «74,1 млн», «37,1 тыс.», «812» */
export function formatMoney(value: unknown): string {
  const n = toNumber(value);
  if (n === null) return '—';
  const abs = Math.abs(n);
  const sign = n < 0 ? MINUS : '';
  // Единицу выбираем после округления до десятых: 999 960 — это «1,0 млн», а не «1 000,0 тыс.»
  const roundsUpTo = (unit: number) => Math.round((abs / unit) * 10) / 10 >= 1000;
  if (abs >= 1e9 || roundsUpTo(1e6)) return `${sign}${oneDecimal.format(abs / 1e9)} млрд`;
  if (abs >= 1e6 || roundsUpTo(1e3)) return `${sign}${oneDecimal.format(abs / 1e6)} млн`;
  if (abs >= 1e4) return `${sign}${oneDecimal.format(abs / 1e3)} тыс.`;
  return `${sign}${intFormat.format(abs)}`;
}

/** Короткие подписи осей: «8 млн», «500 тыс.» */
export function formatAxisMoney(value: unknown): string {
  const n = toNumber(value);
  if (n === null) return '';
  const abs = Math.abs(n);
  const sign = n < 0 ? MINUS : '';
  const short = (v: number) => (Number.isInteger(v) ? intFormat.format(v) : oneDecimal.format(v));
  if (abs >= 1e9) return `${sign}${short(abs / 1e9)} млрд`;
  if (abs >= 1e6) return `${sign}${short(abs / 1e6)} млн`;
  if (abs >= 1e3) return `${sign}${short(abs / 1e3)} тыс.`;
  return `${sign}${intFormat.format(abs)}`;
}

/** «12,4%» */
export function formatPercent(value: unknown, digits = 1): string {
  const n = toNumber(value);
  if (n === null) return '—';
  return `${withMinus(numberFormat(digits, digits).format(n))}%`;
}

/** «+12,4%» / «−5,0%» */
export function formatChange(value: unknown, digits = 1): string {
  const n = toNumber(value);
  if (n === null) return '—';
  const body = numberFormat(digits, digits).format(Math.abs(n));
  if (n > 0) return `+${body}%`;
  if (n < 0) return `${MINUS}${body}%`;
  return `${body}%`;
}

const ISO_PREFIX = /^(\d{4})-(\d{2})-(\d{2})/;

/** Существует ли такая календарная дата: Date молча превращает 30 февраля в 2 марта */
function isRealDate(year: number, month: number, day: number): boolean {
  const d = new Date(year, month - 1, day);
  return d.getFullYear() === year && d.getMonth() === month - 1 && d.getDate() === day;
}

/** Дата из значения API: «2025-11-15», «2025-11-15 10:00:00», Date. Несуществующая дата → null */
export function parseDate(value: unknown): Date | null {
  if (!value) return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  const str = String(value).trim();
  const ymd = ISO_PREFIX.exec(str);
  if (ymd && !isRealDate(Number(ymd[1]), Number(ymd[2]), Number(ymd[3]))) return null;
  // «2025-11-15» без времени — считаем локальной датой, а не UTC
  const dateOnly = str.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (dateOnly) return new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]));
  const d = new Date(str.includes('T') || str.includes(' ') ? str.replace(' ', 'T') : str);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** «15 ноя 2025» */
export function formatDate(value: unknown): string {
  const d = parseDate(value);
  if (!d) return value ? String(value) : '—';
  return `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]} ${d.getFullYear()}`;
}

/** «15 ноя 2025, 14:32» */
export function formatDateTime(value: unknown): string {
  const d = parseDate(value);
  if (!d) return value ? String(value) : '—';
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${formatDate(d)}, ${hh}:${mm}`;
}

/** «ноя ’25» — подписи месяцев на осях графиков */
export function formatMonthTick(value: unknown): string {
  const d = parseDate(value);
  if (!d) return value ? String(value) : '';
  return `${MONTHS_NOMINATIVE[d.getMonth()]} ’${String(d.getFullYear()).slice(2)}`;
}

/** «ноя 2025» — месяц с полным годом (подпись периода) */
export function formatMonthYear(value: unknown): string {
  const d = parseDate(value);
  if (!d) return value ? String(value) : '';
  return `${MONTHS_NOMINATIVE[d.getMonth()]} ${d.getFullYear()}`;
}

/** «ноябрь 2025» — полное название месяца (подсказка графика) */
export function formatMonthLong(value: unknown): string {
  const d = parseDate(value);
  if (!d) return value ? String(value) : '';
  return `${MONTHS_FULL[d.getMonth()]} ${d.getFullYear()}`;
}

/** «15 ноя 2025 — 15 ноя 2026» */
export function formatPeriod(from: unknown, to: unknown): string {
  const a = parseDate(from);
  const b = parseDate(to);
  if (!a && !b) return '—';
  if (a && b) return `${formatDate(a)} — ${formatDate(b)}`;
  return formatDate(a ?? b);
}

/**
 * Если строка похожа на дату (ISO), вернуть «15 ноя 2025», иначе null.
 * Это сырое значение из данных: показываем записанные дату и время как есть, без пересчёта
 * часового пояса (Z или +03:00 игнорируем), а несуществующую дату не выдаём за другую.
 */
export function formatIfDate(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  // Вся строка должна быть датой: «2024-01-15-0042» — это номер, а не 15 января
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}(?::?\d{2})?)?)?$/.exec(value.trim());
  if (!m) return null;
  const [year, month, day] = [Number(m[1]), Number(m[2]), Number(m[3])];
  if (!isRealDate(year, month, day)) return null;
  const hours = m[4] ? Number(m[4]) : 0;
  const minutes = m[5] ? Number(m[5]) : 0;
  if (hours > 23 || minutes > 59) return null;
  const d = new Date(year, month - 1, day, hours, minutes);
  return hours !== 0 || minutes !== 0 ? formatDateTime(d) : formatDate(d);
}

/** pluralRu(5, ['строка', 'строки', 'строк']) → «строк» */
export function pluralRu(n: number, forms: [string, string, string]): string {
  const abs = Math.abs(Math.trunc(n));
  const mod10 = abs % 10;
  const mod100 = abs % 100;
  if (mod10 === 1 && mod100 !== 11) return forms[0];
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1];
  return forms[2];
}

/** «2 000 строк» */
export function formatCount(n: number | null | undefined, forms: [string, string, string]): string {
  const value = n ?? 0;
  return `${formatNumber(value)} ${pluralRu(value, forms)}`;
}

export const ROWS: [string, string, string] = ['строка', 'строки', 'строк'];
export const TABLES: [string, string, string] = ['таблица', 'таблицы', 'таблиц'];
