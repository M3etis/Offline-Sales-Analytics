import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { KeyRound, Pencil, Plus, Trash2, Users } from 'lucide-react';
import toast from 'react-hot-toast';
import { useAuth } from '../shared/providers/AuthContext';
import { useConfirm } from '../shared/providers/ConfirmContext';
import { usersApi } from '../shared/api/users.api';
import { apiErrorMessage as errorText } from '../shared/api/errors';
import type { UserOut } from '../shared/types';
import { Alert, Button, Checkbox, EmptyState, ErrorState, Field, IconButton, Input, Modal, Page, PageHeader, Panel, Segmented, SkeletonLines, Table } from '../shared/ui';
import { formatCount } from '../shared/lib/format';
import './UsersPage.css';

const MAIN_PERMISSIONS = [
  { id: 'dashboard', label: 'Дашборд' },
  { id: 'analyst', label: 'Ассистент' },
  { id: 'sessions', label: 'История' },
  { id: 'details', label: 'Данные' },
];

const SETTINGS_PERMISSIONS = [
  { id: 'settings_data', label: 'Загрузка данных' },
  // «Оценки ответов» входят в settings_assistant (так их закрывают меню и маршрут /settings/feedback).
  // Отдельное право settings_feedback ничего не проверяло, поэтому его галочки здесь нет
  { id: 'settings_assistant', label: 'Правила, словарь и оценки' },
  { id: 'settings_status', label: 'Статус служб' },
  // «Очистки» здесь нет: удалять базы и историю всех пользователей может только администратор
  // (так проверяет бэкенд), а галочка открывала бы страницу, где каждое действие падает с 403
];

const ALL_PERMISSIONS = [...MAIN_PERMISSIONS, ...SETTINGS_PERMISSIONS];
const DEFAULT_PERMISSIONS = ['dashboard', 'analyst', 'sessions', 'details'];
const SECTIONS: [string, string, string] = ['раздел', 'раздела', 'разделов'];

type Role = 'user' | 'admin';

interface FormState {
  username: string;
  full_name: string;
  password: string;
  role: Role;
  permissions: string[];
}

export default function UsersPage() {
  const { username: me } = useAuth();
  const confirm = useConfirm();
  const [users, setUsers] = useState<UserOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  const [mode, setMode] = useState<'create' | 'edit' | 'password' | null>(null);
  const [selected, setSelected] = useState<UserOut | null>(null);
  const [form, setForm] = useState<FormState>({ username: '', full_name: '', password: '', role: 'user', permissions: DEFAULT_PERMISSIONS });
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      setUsers(await usersApi.getUsers());
    } catch (e) {
      console.error(e);
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const close = () => {
    setMode(null);
    setSelected(null);
    setFormError('');
  };

  const openCreate = () => {
    setForm({ username: '', full_name: '', password: '', role: 'user', permissions: DEFAULT_PERMISSIONS });
    setFormError('');
    setMode('create');
  };

  const openEdit = (user: UserOut) => {
    setSelected(user);
    setForm({ username: user.username, full_name: user.full_name || '', password: '', role: user.role === 'admin' ? 'admin' : 'user', permissions: user.permissions || [] });
    setFormError('');
    setMode('edit');
  };

  const openPassword = (user: UserOut) => {
    setSelected(user);
    setForm((f) => ({ ...f, password: '' }));
    setFormError('');
    setMode('password');
  };

  const togglePermission = (id: string, on: boolean) =>
    setForm((f) => ({ ...f, permissions: on ? [...f.permissions, id] : f.permissions.filter((p) => p !== id) }));

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    // Enter отправляет форму через скрытую submit-кнопку, минуя disabled/loading видимой кнопки
    if (!canSubmit || saving) return;
    setFormError('');
    setSaving(true);
    try {
      if (mode === 'create') {
        await usersApi.createUser({ username: form.username.trim(), full_name: form.full_name.trim(), password: form.password, role: form.role, permissions: form.permissions });
        toast.success('Пользователь создан');
      } else if (mode === 'edit' && selected) {
        await usersApi.updateUser(selected.id, { full_name: form.full_name.trim(), role: form.role, permissions: form.permissions });
        toast.success('Изменения сохранены');
      } else if (mode === 'password' && selected) {
        await usersApi.updatePassword(selected.id, form.password);
        toast.success('Пароль изменён');
      }
      close();
      fetchUsers();
    } catch (err) {
      setFormError(errorText(err, 'Не удалось сохранить'));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (user: UserOut) => {
    const ok = await confirm(`Учётная запись ${user.username} и её диалоги будут удалены.`, `Удалить пользователя ${user.full_name || user.username}?`, { confirmLabel: 'Удалить' });
    if (!ok) return;
    try {
      await usersApi.deleteUser(user.id);
      toast.success('Пользователь удалён');
      fetchUsers();
    } catch (err) {
      toast.error(errorText(err, 'Не удалось удалить пользователя'));
    }
  };

  // Считаем только разделы из списка выше: старые учётки могут хранить и settings_feedback
  const knownPermissions = (u: UserOut) => ALL_PERMISSIONS.filter((x) => u.permissions.includes(x.id));
  const accessText = (u: UserOut) => {
    if (u.role === 'admin') return 'Все разделы';
    const count = knownPermissions(u).length;
    return count === 0 ? 'Нет доступа' : formatCount(count, SECTIONS);
  };
  const accessTitle = (u: UserOut) => (u.role === 'admin' ? undefined : knownPermissions(u).map((x) => x.label).join(', '));

  const canSubmit =
    mode === 'password'
      ? form.password.length > 0
      : mode === 'create'
        ? form.username.trim().length > 0 && form.password.length > 0
        : true;

  return (
    <Page className="users">
      <PageHeader
        title="Пользователи"
        description="Кто может входить в приложение и какие разделы видит"
        actions={
          <Button variant="primary" icon={<Plus />} onClick={openCreate}>
            Добавить
          </Button>
        }
      />

      <Panel padding="none">
        {loadError ? (
          <ErrorState onRetry={fetchUsers} />
        ) : loading && users.length === 0 ? (
          <SkeletonLines className="users__skeleton" widths={['50%', '40%']} />
        ) : users.length === 0 ? (
          <EmptyState compact icon={<Users />} title="Нет пользователей" />
        ) : (
          <Table>
            <thead>
              <tr>
                <th>Имя</th>
                <th>Логин</th>
                <th>Роль</th>
                <th className="users__col-access">Доступ</th>
                <th className="actions">
                  <span className="visually-hidden">Действия</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const self = u.username === me;
                return (
                  <tr key={u.id}>
                    <td>
                      <div className="users__name">
                        <span className="users__avatar" aria-hidden="true">
                          {(u.full_name || u.username).trim()[0]?.toUpperCase()}
                        </span>
                        <span>{u.full_name || '—'}</span>
                        {self && <span className="users__you">это вы</span>}
                      </div>
                    </td>
                    <td className="mono">{u.username}</td>
                    <td className={u.role === 'admin' ? undefined : 'muted'}>{u.role === 'admin' ? 'Администратор' : 'Пользователь'}</td>
                    <td className="muted users__col-access" title={accessTitle(u)}>
                      {accessText(u)}
                    </td>
                    <td className="actions">
                      <div>
                        <IconButton size="sm" label="Изменить" icon={<Pencil />} onClick={() => openEdit(u)} />
                        <IconButton size="sm" label="Сменить пароль" icon={<KeyRound />} onClick={() => openPassword(u)} />
                        <IconButton size="sm" variant="danger" label={self ? 'Нельзя удалить себя' : 'Удалить'} icon={<Trash2 />} onClick={() => handleDelete(u)} disabled={self} />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Panel>

      <Modal
        open={mode === 'create' || mode === 'edit'}
        onClose={close}
        title={mode === 'create' ? 'Новый пользователь' : `Изменить: ${selected?.full_name || selected?.username || ''}`}
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={close}>
              Отмена
            </Button>
            <Button variant="primary" onClick={() => submit()} loading={saving} disabled={!canSubmit}>
              {mode === 'create' ? 'Создать' : 'Сохранить'}
            </Button>
          </>
        }
      >
        <form className="users__form" onSubmit={submit}>
          {formError && <Alert tone="negative">{formError}</Alert>}
          <div className="users__form-row">
            <Field label="Имя">
              {(id) => <Input id={id} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} autoComplete="off" />}
            </Field>
            {mode === 'create' && (
              <Field label="Логин">
                {(id) => (
                  <Input
                    id={id}
                    className="ui-input--mono"
                    value={form.username}
                    onChange={(e) => setForm({ ...form, username: e.target.value })}
                    autoCapitalize="none"
                    spellCheck={false}
                    autoComplete="off"
                    required
                  />
                )}
              </Field>
            )}
          </div>
          {mode === 'create' && (
            <Field label="Пароль">
              {(id) => <Input id={id} type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="new-password" required />}
            </Field>
          )}
          <div className="users__role">
            <span className="ui-field__label">Роль</span>
            {mode === 'edit' && selected?.username === me ? (
              // Бэкенд тоже запрещает: иначе вернуть роль администратора будет некому
              <p className="users__note">Свою роль администратора снять нельзя</p>
            ) : (
              <Segmented<Role>
                label="Роль"
                value={form.role}
                onChange={(role) => setForm({ ...form, role })}
                options={[
                  { value: 'user', label: 'Пользователь' },
                  { value: 'admin', label: 'Администратор' },
                ]}
              />
            )}
          </div>
          {form.role === 'admin' ? (
            <p className="users__note">Администратор видит все разделы и управляет пользователями</p>
          ) : (
            <fieldset className="users__perms">
              <legend className="ui-field__label">Доступ к разделам</legend>
              <div className="users__perm-groups">
                <div className="users__perm-group">
                  <span className="users__perm-title">Работа</span>
                  {MAIN_PERMISSIONS.map((p) => (
                    <Checkbox key={p.id} label={p.label} checked={form.permissions.includes(p.id)} onChange={(on) => togglePermission(p.id, on)} />
                  ))}
                </div>
                <div className="users__perm-group">
                  <span className="users__perm-title">Настройки</span>
                  {SETTINGS_PERMISSIONS.map((p) => (
                    <Checkbox key={p.id} label={p.label} checked={form.permissions.includes(p.id)} onChange={(on) => togglePermission(p.id, on)} />
                  ))}
                </div>
              </div>
            </fieldset>
          )}
          <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
        </form>
      </Modal>

      <Modal
        open={mode === 'password'}
        onClose={close}
        title="Новый пароль"
        description={selected ? `Для ${selected.full_name || selected.username} (${selected.username})` : undefined}
        size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={close}>
              Отмена
            </Button>
            <Button variant="primary" onClick={() => submit()} loading={saving} disabled={!canSubmit}>
              Сменить пароль
            </Button>
          </>
        }
      >
        <form className="users__form" onSubmit={submit}>
          {formError && <Alert tone="negative">{formError}</Alert>}
          <Field label="Пароль">
            {(id) => <Input id={id} type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="new-password" required />}
          </Field>
          <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
        </form>
      </Modal>
    </Page>
  );
}
