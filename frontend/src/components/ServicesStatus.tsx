import { useCallback, useEffect, useRef, useState } from 'react';
import { Cpu, RefreshCw, Server } from 'lucide-react';
import toast from 'react-hot-toast';
import { useConfirm } from '../shared/providers/ConfirmContext';
import { refreshModelStatus, useModelStatus } from '../shared/hooks/useModelStatus';
import { Alert, Button, Panel, StatusDot } from '../shared/ui';
import './ServicesStatus.css';

type ServiceKey = 'backend' | 'ollama';
type State = 'checking' | 'online' | 'offline';

const STATE_TEXT: Record<State, string> = { checking: 'Проверка…', online: 'Работает', offline: 'Не отвечает' };
const STATE_TONE = { checking: 'neutral', online: 'positive', offline: 'negative' } as const;

/** Службы приложения. Перезапуск доступен только при запуске через dev-сервер (vite) */
export default function ServicesStatus() {
  const confirm = useConfirm();
  const model = useModelStatus();
  const [status, setStatus] = useState<Record<ServiceKey, State>>({ backend: 'checking', ollama: 'checking' });
  const [managerAvailable, setManagerAvailable] = useState(true);
  const [restarting, setRestarting] = useState<ServiceKey | null>(null);
  // Менеджер служб хоть раз ответил — значит, он есть (dev-сервер), и сетевая ошибка временная
  const managerSeenRef = useRef(false);

  const checkStatus = useCallback(async () => {
    try {
      const res = await fetch('/__internal/services/status');
      const type = res.headers.get('content-type') || '';
      if (!res.ok || !type.includes('json')) {
        setManagerAvailable(false);
        return;
      }
      const data: { backend: boolean; ollama: boolean } = await res.json();
      managerSeenRef.current = true;
      setManagerAvailable(true);
      setStatus({ backend: data.backend ? 'online' : 'offline', ollama: data.ollama ? 'online' : 'offline' });
    } catch (e) {
      console.error('Failed to fetch services status', e);
      // Сбой сети у работавшего менеджера (например, dev-сервер перезапускается) — опрос продолжаем.
      // Без единого ответа считаем, что менеджера нет: в собранном приложении его и не бывает
      if (!managerSeenRef.current) setManagerAvailable(false);
    }
  }, []);

  useEffect(() => {
    checkStatus();
  }, [checkStatus]);

  // Менеджер служб есть только у dev-сервера: если его нет, опрашивать адрес каждые 5 с бессмысленно
  useEffect(() => {
    if (!managerAvailable) return;
    const interval = setInterval(checkStatus, 5000);
    return () => clearInterval(interval);
  }, [checkStatus, managerAvailable]);

  const handleRestart = async (service: ServiceKey) => {
    const name = service === 'backend' ? 'сервер приложения' : 'Ollama';
    const ok = await confirm(`Текущие запросы к ассистенту прервутся. Перезапуск обычно занимает несколько секунд.`, `Перезапустить ${name}?`, {
      confirmLabel: 'Перезапустить',
      tone: 'default',
    });
    if (!ok) return;
    setRestarting(service);
    try {
      // Заголовок обязателен: без него dev-сервер отклоняет запрос (защита от вызова с чужих сайтов)
      const res = await fetch(`/__internal/services/restart?service=${service}`, { method: 'POST', headers: { 'X-OSA-Dev': '1' } });
      if (res.ok) {
        setStatus((prev) => ({ ...prev, [service]: 'checking' }));
        // Статус Ollama берётся из опроса модели — перепроверяем его, когда служба успеет подняться
        if (service === 'ollama') setTimeout(() => void refreshModelStatus(), 3000);
        toast.success('Команда перезапуска отправлена');
      } else {
        toast.error('Не удалось отправить команду перезапуска');
      }
    } catch (e) {
      console.error('Restart failed', e);
      toast.error('Не удалось перезапустить службу');
    } finally {
      setTimeout(() => setRestarting(null), 3000);
    }
  };

  // Без менеджера служб (сборка Electron, vite preview) о сервере судим по опросу модели через API:
  // страница грузится с диска и открывается, даже когда сервер упал
  const backendState: State = managerAvailable
    ? status.backend
    : model.serverReachable === null
      ? 'checking'
      : model.serverReachable
        ? 'online'
        : 'offline';
  // Состояние Ollama — из того же API, что и описание строки (сервер сам проверяет Ollama).
  // pgrep менеджера служб не видит Ollama в Docker или на другом хосте и противоречил описанию
  const ollamaState: State = model.state === 'checking' ? 'checking' : model.state === 'offline' ? 'offline' : 'online';

  const modelDetail =
    model.state === 'ready'
      ? `Модель ${model.model} загружена`
      : model.state === 'missing'
        ? `Модель ${model.model ?? ''} не скачана — выберите и установите её в «Настройки ассистента»`
        : model.state === 'offline'
          ? model.detail ?? 'Ollama не запущена'
          : 'Проверяю модель…';

  const rows: { key: ServiceKey; name: string; detail: string; icon: typeof Server; state: State }[] = [
    { key: 'backend', name: 'Сервер приложения', detail: 'FastAPI, база DuckDB', icon: Server, state: backendState },
    { key: 'ollama', name: 'Локальная модель', detail: `Ollama · ${modelDetail}`, icon: Cpu, state: ollamaState },
  ];

  return (
    <div className="services">
      {!managerAvailable && (
        <Alert tone="neutral" title="Перезапуск недоступен в этой сборке">
          Службами управляет само приложение. Если что-то не отвечает, перезапустите приложение целиком.
        </Alert>
      )}
      <Panel padding="none">
        <ul className="services__list">
          {rows.map(({ key, name, detail, icon: Icon, state }) => (
            <li key={key} className="services__item">
              <span className="services__icon">
                <Icon aria-hidden="true" />
              </span>
              <div className="services__text">
                <span className="services__name">{name}</span>
                <span className="services__detail">{detail}</span>
              </div>
              <span className={`services__state services__state--${state}`}>
                <StatusDot tone={STATE_TONE[state]} />
                {STATE_TEXT[state]}
              </span>
              {managerAvailable && (
                <Button size="sm" icon={<RefreshCw className={restarting === key ? 'services__spin' : undefined} />} onClick={() => handleRestart(key)} disabled={restarting === key}>
                  {restarting === key ? 'Перезапуск' : state === 'offline' ? 'Запустить' : 'Перезапустить'}
                </Button>
              )}
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
