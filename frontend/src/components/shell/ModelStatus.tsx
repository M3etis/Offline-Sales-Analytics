import { Link } from 'react-router-dom';
import { StatusDot } from '../../shared/ui';
import { useModelStatus } from '../../shared/hooks/useModelStatus';
import { useCanOpen } from './useCanOpen';

/**
 * Сигнатура продукта: «● Локально · qwen2.5:14b» — живой статус локальной модели.
 */
export function ModelStatus({ compact = false, className = '' }: { compact?: boolean; className?: string }) {
  const { state, model, detail } = useModelStatus();
  const canOpen = useCanOpen();

  const tone = state === 'ready' ? 'positive' : state === 'missing' ? 'accent' : state === 'offline' ? 'negative' : 'neutral';
  const text =
    state === 'ready'
      ? `Локально · ${model}`
      : state === 'missing'
        ? `Нет модели · ${model ?? '—'}`
        : state === 'offline'
          ? 'Модель недоступна'
          : 'Проверка модели…';
  const title = detail ?? (state === 'ready' ? `Модель ${model} работает на этом компьютере, данные не покидают его` : text);

  const content = (
    <>
      <StatusDot tone={tone} />
      {!compact && <span className="model-status__text">{text}</span>}
    </>
  );

  const cls = `model-status model-status--${state} ${compact ? 'model-status--compact' : ''} ${className}`;

  // Выбор модели — в настройках ассистента; без доступа к ним ведём к статусу служб (там состояние Ollama)
  const target = ['/settings/assistant', '/settings/status'].find((path) => canOpen(path));
  if (target) {
    return (
      <Link to={target} className={cls} title={title} aria-label={compact ? text : undefined}>
        {content}
      </Link>
    );
  }
  return (
    <span className={cls} title={title} role="status" aria-label={compact ? text : undefined}>
      {content}
    </span>
  );
}
