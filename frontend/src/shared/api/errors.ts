/**
 * Текст ошибки API для показа пользователю.
 *
 * FastAPI отдаёт `detail` строкой (HTTPException) или массивом объектов
 * `{ msg, loc, type }` (ошибки валидации 422). Массив нельзя рендерить как
 * React-child, поэтому всегда сводим ответ к строке.
 */
export function apiErrorMessage(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail || fallback;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item as { msg?: unknown } | null)?.msg)
      .filter((msg): msg is string => typeof msg === 'string' && msg.length > 0)
      // pydantic v2 добавляет к ValueError из валидатора префикс «Value error, »
      .map((msg) => msg.replace(/^Value error, /, ''));
    if (messages.length > 0) return messages.join('; ');
  }
  return fallback;
}
