import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, Cpu, Download, HardDrive, MemoryStick, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { modelsApi } from '../shared/api/models.api';
import { apiErrorMessage } from '../shared/api/errors';
import type { CatalogModel, Hardware, ModelFit, ModelsOverview, PullState } from '../shared/api/models.api';
import { refreshModelStatus } from '../shared/hooks/useModelStatus';
import { useAuth } from '../shared/providers/AuthContext';
import { useConfirm } from '../shared/providers/ConfirmContext';
import { Alert, Badge, Button, ErrorState, Panel, Skeleton, StatusDot } from '../shared/ui';
import { formatNumber } from '../shared/lib/format';
import './ModelManager.css';

const POLL_MS = 1000;
// Числа — общим форматтером интерфейса (format.ts), как на остальных экранах
const gb = (value: number) => `${formatNumber(value, 1)} ГБ`;

function formatBytes(n: number): string {
  if (n >= 1e9) return `${formatNumber(n / 1e9, 1)} ГБ`;
  return `${Math.round(n / 1e6)} МБ`;
}

function formatSpeed(bps: number): string {
  if (bps >= 1e6) return `${formatNumber(bps / 1e6, 1)} МБ/с`;
  return `${Math.max(1, Math.round(bps / 1e3))} КБ/с`;
}

function formatEta(seconds: number): string {
  if (seconds < 60) return `~${Math.max(1, Math.round(seconds))} с`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `~${minutes} мин`;
  const rest = minutes % 60;
  return `~${Math.floor(minutes / 60)} ч${rest ? ` ${rest} мин` : ''}`;
}

const FIT_TONE: Record<ModelFit, 'positive' | 'accent' | 'negative'> = { fast: 'positive', slow: 'accent', no: 'negative' };

function fitLabel(fit: ModelFit, hw: Hardware): string {
  if (fit === 'fast') return 'Быстро';
  if (fit === 'no') return 'Не хватит памяти';
  return hw.accelerator ? 'Медленно' : 'На процессоре';
}

function HardwareSummary({ hw }: { hw: Hardware }) {
  const memory =
    hw.accelerator === 'apple'
      ? `${gb(hw.ram_gb ?? 0)} общей памяти`
      : hw.accelerator === 'nvidia'
        ? `${gb(hw.vram_gb ?? 0)} видеопамяти · ${gb(hw.ram_gb ?? 0)} ОЗУ`
        : `${hw.ram_gb ? gb(hw.ram_gb) : '—'} ОЗУ`;
  const chip = hw.accelerator ? hw.gpu : `${hw.cpu ?? 'Процессор'} · видеокарта не найдена`;

  return (
    <ul className="models__hw" aria-label="Конфигурация компьютера">
      <li>
        <Cpu aria-hidden="true" />
        <span>{chip}</span>
      </li>
      <li>
        <MemoryStick aria-hidden="true" />
        <span>{memory}</span>
      </li>
      {hw.disk_free_gb != null && (
        <li>
          <HardDrive aria-hidden="true" />
          <span>{gb(hw.disk_free_gb)} свободно на диске</span>
        </li>
      )}
    </ul>
  );
}

function PullProgress({ pull, title }: { pull: PullState; title: string }) {
  const total = pull.total ?? 0;
  const completed = pull.completed ?? 0;
  const pct = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0;

  let text: string;
  switch (pull.phase) {
    case 'downloading': {
      const parts = [`${formatBytes(completed)} из ${formatBytes(total)}`];
      if (pull.speed_bps) parts.push(formatSpeed(pull.speed_bps));
      if (pull.eta_s) parts.push(`осталось ${formatEta(pull.eta_s)}`);
      text = parts.join(' · ');
      break;
    }
    case 'verifying':
      text = 'Проверяю целостность файлов…';
      break;
    case 'writing':
      text = 'Устанавливаю…';
      break;
    case 'activating':
      text = 'Переключаю ассистента на новую модель…';
      break;
    default:
      text = 'Подключаюсь к каталогу моделей…';
  }

  return (
    <div className="models__progress">
      <div
        className="models__bar"
        role="progressbar"
        aria-label={`Загрузка ${title}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <span style={{ width: `${pct}%` }} />
      </div>
      <span className="models__progress-text">
        {pull.phase === 'downloading' && <span className="models__pct">{pct}%</span>}
        {text}
      </span>
    </div>
  );
}

/** Подбор локальной модели под этот компьютер, скачивание и переключение */
export default function ModelManager() {
  const { isAdmin } = useAuth();
  const confirm = useConfirm();
  const [data, setData] = useState<ModelsOverview | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [pull, setPull] = useState<PullState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // Каталог свёрнут до текущей модели: остальные нужны, только когда модель меняют
  const [expanded, setExpanded] = useState(false);
  const titles = useRef<Record<string, string>>({});

  const load = useCallback(async () => {
    try {
      const overview = await modelsApi.overview();
      overview.models.forEach((m) => (titles.current[m.name] = m.title));
      setData(overview);
      setPull(overview.pull);
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const running = pull?.status === 'running';

  // Пока идёт загрузка — опрашиваем прогресс; по её окончании обновляем список и статус модели.
  // Цепочка setTimeout, а не setInterval: следующий запрос — только после ответа на предыдущий.
  // С интервалом запросы перекрывались, запоздавший «running» после «success» снова включал опрос —
  // отсюда повторные уведомления и лишние перезагрузки списка
  useEffect(() => {
    if (!running) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const next = await modelsApi.pullStatus();
        if (cancelled) return;
        setPull(next);
        if (next.status === 'running') {
          timer = setTimeout(tick, POLL_MS);
          return;
        }
        cancelled = true; // загрузка закончилась — этот эффект больше ничего не делает
        const title = (next.model && titles.current[next.model]) || next.model;
        if (next.status === 'success') {
          toast.success(next.activated ? `${title} установлена и выбрана основной` : `${title} скачана`);
        } else if (next.status === 'error') {
          toast.error(next.error || 'Не удалось скачать модель');
        }
        await load();
        refreshModelStatus();
      } catch {
        /* сервер мог перезапуститься — попробуем на следующем тике */
        if (!cancelled) timer = setTimeout(tick, POLL_MS);
      }
    };
    timer = setTimeout(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [running, load]);

  const startPull = async (m: CatalogModel) => {
    if (m.fit === 'no') {
      const ok = await confirm(
        `Модели нужно около ${gb(m.memory_gb)} памяти — больше, чем есть у этого компьютера. Она может не запуститься или отвечать очень медленно.`,
        `Всё равно скачать ${m.title}?`,
        { confirmLabel: `Скачать ${gb(m.size_gb)}`, tone: 'default' },
      );
      if (!ok) return;
    }
    setBusy(m.name);
    try {
      setPull(await modelsApi.pull(m.name, true));
    } catch (e) {
      toast.error(apiErrorMessage(e, 'Не удалось начать загрузку'));
    } finally {
      setBusy(null);
    }
  };

  const cancelPull = async () => {
    try {
      setPull(await modelsApi.cancelPull());
      toast('Загрузка остановлена. Скачанная часть сохранится — можно продолжить позже.');
    } catch (e) {
      toast.error(apiErrorMessage(e, 'Не удалось остановить загрузку'));
    }
  };

  const activate = async (name: string, title: string) => {
    setBusy(name);
    try {
      await modelsApi.activate(name);
      toast.success(`Основная модель — ${title}`);
      await load();
      refreshModelStatus();
    } catch (e) {
      toast.error(apiErrorMessage(e, 'Не удалось переключить модель'));
    } finally {
      setBusy(null);
    }
  };

  const panelProps = {
    title: 'Модель ассистента',
    description: 'Подбирается под этот компьютер. Модель работает локально — данные никуда не отправляются.',
  };

  if (loadError && !data) {
    return (
      <Panel {...panelProps}>
        <ErrorState compact title="Не удалось получить список моделей" onRetry={load} />
      </Panel>
    );
  }

  if (!data) {
    return (
      <Panel {...panelProps}>
        <div className="models__loading" aria-busy="true">
          <Skeleton width="60%" />
          <Skeleton height={56} />
          <Skeleton height={56} />
          <Skeleton height={56} />
        </div>
      </Panel>
    );
  }

  const hw = data.hardware;
  const ollamaOk = data.ollama.status === 'ok';
  const canManage = isAdmin && ollamaOk;
  const recommended = data.models.find((m) => m.recommended) ?? null;
  const activeInstalled = data.models.some((m) => m.active && m.installed) || data.other_installed.some((m) => m.active);

  // В свёрнутом виде — текущая модель и та, что качается или чья загрузка прервалась
  const inFocus = (name: string, active: boolean) => active || pull?.model === name;
  const catalog = expanded ? data.models : data.models.filter((m) => inFocus(m.name, m.active));
  const others = expanded ? data.other_installed : data.other_installed.filter((m) => inFocus(m.name, m.active));
  const hiddenCount = data.models.length + data.other_installed.length - catalog.length - others.length;

  /** prominent — главная кнопка в подсказке с рекомендацией */
  const renderAction = (m: CatalogModel, prominent = false) => {
    if (!canManage || (m.active && m.installed)) return null;
    if (running && pull?.model === m.name) {
      return (
        <Button size="sm" variant="ghost" icon={<X />} onClick={cancelPull}>
          Отменить
        </Button>
      );
    }
    if (m.installed) {
      return (
        <Button size="sm" variant={prominent ? 'primary' : 'secondary'} loading={busy === m.name} disabled={running} onClick={() => activate(m.name, m.title)}>
          Сделать основной
        </Button>
      );
    }
    const interrupted = pull?.model === m.name && (pull.status === 'error' || pull.status === 'cancelled');
    return (
      <Button
        size="sm"
        variant={m.recommended ? 'primary' : 'secondary'}
        icon={<Download />}
        loading={busy === m.name}
        disabled={running || !m.disk_ok}
        title={!m.disk_ok ? `Нужно ${gb(m.size_gb)} свободного места на диске` : running ? 'Дождитесь окончания текущей загрузки' : undefined}
        onClick={() => startPull(m)}
      >
        {interrupted ? 'Продолжить' : `Скачать · ${gb(m.size_gb)}`}
      </Button>
    );
  };

  return (
    <Panel {...panelProps}>
      <div className="models">
        <HardwareSummary hw={hw} />

        {!ollamaOk && (
          <Alert tone="negative" title="Ollama не запущена">
            Скачивать и переключать модели можно, когда Ollama работает. Запустите её или перезапустите приложение.
          </Alert>
        )}

        {ollamaOk && recommended && !(recommended.active && recommended.installed) && (
          <Alert
            tone="accent"
            title={`Этому компьютеру подходит ${recommended.title}`}
            action={canManage && !(running && pull?.model === recommended.name) ? renderAction(recommended, true) : undefined}
          >
            {recommended.installed
              ? 'Модель уже скачана — осталось сделать её основной.'
              : `${recommended.fit_reason}. Загрузка ${gb(recommended.size_gb)}, после неё ассистент сразу переключится на эту модель.`}
            {!activeInstalled &&
              (recommended.active
                ? ' Она уже выбрана основной, но ещё не скачана, поэтому ассистент пока не отвечает.'
                : ` Сейчас выбрана ${data.active_model}, но она не скачана, поэтому ассистент не отвечает.`)}
          </Alert>
        )}

        {ollamaOk && !recommended && (
          <Alert tone="negative" title="Памяти не хватает даже для самой лёгкой модели">
            Ассистенту нужно хотя бы {Math.min(...data.models.map((m) => m.min_ram_gb))} ГБ оперативной памяти. Закройте лишние программы или
            запустите приложение на более мощном компьютере.
          </Alert>
        )}

        {catalog.length + others.length > 0 && (
        <ul className="models__list">
          {catalog.map((m) => {
            const pulling = running && pull?.model === m.name;
            const failed = !running && pull?.model === m.name && pull.status === 'error' && !m.installed;
            const stopped = !running && pull?.model === m.name && pull.status === 'cancelled' && !m.installed;
            return (
              <li key={m.name} className={`models__item ${m.recommended ? 'models__item--recommended' : ''}`}>
                <div className="models__main">
                  <div className="models__head">
                    <span className="models__title">{m.title}</span>
                    {m.recommended && <Badge tone="accent">Рекомендуем</Badge>}
                    {m.active && m.installed && <Badge tone="positive">Используется</Badge>}
                    {m.active && !m.installed && <Badge tone="negative">Выбрана, но не скачана</Badge>}
                    {m.installed && !m.active && <Badge>Скачана</Badge>}
                  </div>
                  <p className="models__desc">{m.description}</p>
                  <p className="models__meta">
                    <span>{m.installed ? 'На диске' : `Загрузка ${gb(m.size_gb)}`}</span>
                    <span>Нужно ~{gb(m.memory_gb)} памяти</span>
                    <span className={`models__fit models__fit--${m.fit}`} title={m.fit_reason}>
                      <StatusDot tone={FIT_TONE[m.fit]} />
                      {fitLabel(m.fit, hw)}
                    </span>
                  </p>
                  {m.fit !== 'fast' && !m.active && <p className="models__reason">{m.fit_reason}</p>}
                  {!m.installed && !m.disk_ok && (
                    <p className="models__reason models__reason--negative">
                      Недостаточно места: нужно {gb(m.size_gb)}, свободно {gb(hw.disk_free_gb ?? 0)}
                    </p>
                  )}
                  {pulling && pull && <PullProgress pull={pull} title={m.title} />}
                  {failed && <p className="models__reason models__reason--negative">{pull?.error}</p>}
                  {stopped && <p className="models__reason">Загрузка остановлена — продолжится с того же места.</p>}
                </div>
                <div className="models__action">{renderAction(m)}</div>
              </li>
            );
          })}

          {others.map((m) => (
            <li key={m.name} className="models__item">
              <div className="models__main">
                <div className="models__head">
                  <span className="models__title models__title--mono">{m.name}</span>
                  {m.active && <Badge tone="positive">Используется</Badge>}
                  {!m.active && <Badge>Скачана</Badge>}
                </div>
                <p className="models__desc">Модель не из списка рекомендуемых — качество ответов не проверялось.</p>
                <p className="models__meta">
                  <span>{gb(m.size_gb)} на диске</span>
                  {m.params && <span>{m.params} параметров</span>}
                </p>
              </div>
              <div className="models__action">
                {canManage && !m.active && (
                  <Button size="sm" loading={busy === m.name} disabled={running} onClick={() => activate(m.name, m.name)}>
                    Сделать основной
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
        )}

        {(expanded || hiddenCount > 0) && (
          <Button
            size="sm"
            variant="ghost"
            className={`models__toggle ${expanded ? 'is-open' : ''}`}
            iconRight={<ChevronDown />}
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? 'Свернуть список' : `Другие модели · ${hiddenCount}`}
          </Button>
        )}

        {!isAdmin && <p className="models__note">Скачивать и переключать модели может администратор.</p>}
      </div>
    </Panel>
  );
}
