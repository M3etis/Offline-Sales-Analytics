import { useEffect, useState } from 'react';
import { Database, History, RotateCcw } from 'lucide-react';
import toast from 'react-hot-toast';
import { settingsApi } from '../shared/api/settings.api';
import { dataApi } from '../shared/api/data.api';
import type { UserOut } from '../shared/types';
import { useConfirm } from '../shared/providers/ConfirmContext';
import { useDataset } from '../shared/providers/DatasetContext';
import { Button, Field, Page, PageHeader, Panel, Select } from '../shared/ui';
import { formatCount, TABLES } from '../shared/lib/format';
import { hardNavigate } from '../shared/lib/navigation';
import './CleanupPage.css';

type Action = 'sessions' | 'datasets' | 'all';

export default function CleanupPage() {
  const confirm = useConfirm();
  const { groups, groupsLoading, groupsError, reloadGroups } = useDataset();
  const [users, setUsers] = useState<UserOut[]>([]);
  const [selectedUsername, setSelectedUsername] = useState('');
  const [busy, setBusy] = useState<Action | null>(null);

  useEffect(() => {
    settingsApi
      .getUsers()
      .then(setUsers)
      .catch((e) => console.error('Failed to load users', e));
  }, []);

  const selectedUser = users.find((u) => u.username === selectedUsername);
  const scopeText = selectedUser ? `пользователя ${selectedUser.full_name || selectedUser.username}` : 'всех пользователей';
  const tableCount = groups.reduce((n, g) => n + g.tables.length, 0);
  // Кнопку блокируем, только когда точно известно, что баз нет; при ошибке загрузки списка удаление доступно
  const noTables = tableCount === 0 && !groupsLoading && !groupsError;
  const tablesText =
    tableCount > 0
      ? `Сейчас загружено ${formatCount(tableCount, TABLES)}`
      : groupsLoading
        ? 'Загружаю список баз…'
        : groupsError
          ? 'Не удалось получить список баз'
          : 'Базы не загружены';

  const handleClearSessions = async () => {
    const ok = await confirm(
      `Будут удалены диалоги и кэш ответов ${scopeText}. Загруженные базы и настройки не изменятся.`,
      'Очистить историю диалогов?',
      { confirmLabel: selectedUser ? 'Очистить историю' : 'Очистить у всех' },
    );
    if (!ok) return;
    setBusy('sessions');
    try {
      await settingsApi.clearSessions(selectedUsername || undefined);
      await settingsApi.clearCache(selectedUsername || undefined);
      toast.success(`История ${scopeText} очищена`);
    } catch (e) {
      console.error(e);
      toast.error('Не удалось очистить историю');
    } finally {
      setBusy(null);
    }
  };

  const handleDeleteDatasets = async () => {
    const ok = await confirm(
      `Все загруженные файлы и таблицы${tableCount > 0 ? ` (${formatCount(tableCount, TABLES)})` : ''} будут удалены без возможности восстановления. Вместе с ними удалится история диалогов всех пользователей и кэш ответов. Пользователи и настройки ассистента сохранятся.`,
      'Удалить все базы?',
      { confirmLabel: 'Удалить все базы' },
    );
    if (!ok) return;
    setBusy('datasets');
    try {
      await dataApi.deleteAllDatasets();
      await reloadGroups();
      toast.success('Все базы удалены');
    } catch (e) {
      console.error(e);
      toast.error('Не удалось удалить базы');
    } finally {
      setBusy(null);
    }
  };

  const handleReset = async () => {
    const ok = await confirm(
      'Будут удалены все базы, вся история диалогов и кэш ответов. Приложение вернётся к состоянию после установки. Пользователи и настройки ассистента сохранятся.',
      'Сбросить всё?',
      { confirmLabel: 'Сбросить всё' },
    );
    if (!ok) return;
    setBusy('all');
    try {
      await dataApi.fullCleanup();
      toast.success('Данные, история и кэш удалены');
      setTimeout(() => {
        hardNavigate('/dashboard');
      }, 1200);
    } catch (e) {
      console.error(e);
      toast.error('Не удалось выполнить сброс');
      setBusy(null);
    }
  };

  return (
    <Page width="narrow" className="cleanup">
      <PageHeader title="Очистка" description="Удаление истории и данных. Эти действия нельзя отменить" />

      <Panel padding="none" title="История диалогов" description="Диалоги и кэш ответов ассистента">
        <div className="cleanup__row">
          <span className="cleanup__icon">
            <History aria-hidden="true" />
          </span>
          <Field label="Чью историю очистить" className="cleanup__field">
            {(id) => (
              <Select id={id} value={selectedUsername} onChange={(e) => setSelectedUsername(e.target.value)}>
                <option value="">Все пользователи</option>
                {users.map((u) => (
                  <option key={u.id} value={u.username}>
                    {u.full_name ? `${u.full_name} (${u.username})` : u.username}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Button variant="danger-outline" onClick={handleClearSessions} loading={busy === 'sessions'} disabled={busy !== null && busy !== 'sessions'}>
            Очистить историю
          </Button>
        </div>
      </Panel>

      <Panel padding="none" title="Загруженные базы" description="Файлы и таблицы вместе с историей диалогов. Пользователи и настройки останутся">
        <div className="cleanup__row">
          <span className="cleanup__icon">
            <Database aria-hidden="true" />
          </span>
          <p className="cleanup__text">{tablesText}</p>
          <Button variant="danger-outline" onClick={handleDeleteDatasets} loading={busy === 'datasets'} disabled={noTables || (busy !== null && busy !== 'datasets')}>
            Удалить все базы
          </Button>
        </div>
      </Panel>

      <Panel padding="none" tone="danger" title="Полный сброс" description="Базы, история и кэш — всё сразу">
        <div className="cleanup__row">
          <span className="cleanup__icon cleanup__icon--danger">
            <RotateCcw aria-hidden="true" />
          </span>
          <p className="cleanup__text">Приложение вернётся к состоянию сразу после установки</p>
          <Button variant="danger" onClick={handleReset} loading={busy === 'all'} disabled={busy !== null && busy !== 'all'}>
            Сбросить всё
          </Button>
        </div>
      </Panel>
    </Page>
  );
}
