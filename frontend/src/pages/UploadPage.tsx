import { Fragment, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronDown, ChevronRight, Database, Pencil, Table2, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { dataApi } from '../shared/api/data.api';
import type { UploadResponse } from '../shared/api/data.api';
import { apiErrorMessage as errorText } from '../shared/api/errors';
import type { Dataset, DatasetGroup } from '../shared/types';
import FileUpload from '../components/FileUpload';
import { useConfirm } from '../shared/providers/ConfirmContext';
import { useDataset } from '../shared/providers/DatasetContext';
import { useAuth } from '../shared/providers/AuthContext';
import { useCanOpen } from '../components/shell/useCanOpen';
import { Alert, Badge, Button, EmptyState, ErrorState, Field, IconButton, Input, Modal, Page, PageHeader, Panel, Table } from '../shared/ui';
import { formatCount, formatDate, formatNumber, formatPeriod, ROWS, TABLES } from '../shared/lib/format';
import { groupLabel, groupMeta } from '../shared/lib/datasetLabel';
import './UploadPage.css';

interface DataInfo {
  rows: number;
  date_range?: { min?: string | null; max?: string | null } | null;
}

type RenameTarget = { kind: 'group'; id: string; name: string } | { kind: 'table'; id: string; name: string };

export default function UploadPage() {
  const navigate = useNavigate();
  const confirm = useConfirm();
  const { isAdmin } = useAuth();
  const canOpenTables = useCanOpen()('/details');
  const { groups, groupsLoading, groupsError, reloadGroups, selectGroup, selectedDatasetId, setSelectedDatasetId } = useDataset();

  const [result, setResult] = useState<UploadResponse | null>(null);
  const [busy, setBusy] = useState<'demo' | 'luxury' | 'url' | null>(null);
  const [urlInput, setUrlInput] = useState('');
  const [urlError, setUrlError] = useState<string | null>(null);
  const [info, setInfo] = useState<DataInfo | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [renameTarget, setRenameTarget] = useState<RenameTarget | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renaming, setRenaming] = useState(false);

  const fetchInfo = async () => {
    try {
      setInfo(await dataApi.getInfo());
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    fetchInfo();
  }, []);

  const handleSuccess = async (data: UploadResponse) => {
    setResult(data);
    await Promise.all([fetchInfo(), reloadGroups()]);
    setSelectedDatasetId(data.dataset_id);
  };

  const runDemo = async (kind: 'demo' | 'luxury') => {
    setBusy(kind);
    setResult(null);
    try {
      const res = kind === 'demo' ? await dataApi.loadDemo() : await dataApi.loadDemoLuxury();
      await handleSuccess(res);
    } catch (e) {
      toast.error(errorText(e, 'Не удалось загрузить демо-набор'));
    } finally {
      setBusy(null);
    }
  };

  const handleUrlUpload = async () => {
    const url = urlInput.trim();
    // Enter в поле вызывает загрузку напрямую, минуя disabled кнопки — повторяем её условие
    if (!url || busy !== null) return;
    setBusy('url');
    setResult(null);
    setUrlError(null);
    try {
      const res = await dataApi.uploadUrl(url);
      await handleSuccess(res);
      setUrlInput('');
    } catch (e) {
      setUrlError(errorText(e, 'Не удалось загрузить файл по ссылке'));
    } finally {
      setBusy(null);
    }
  };

  const handleDeleteGroup = async (group: DatasetGroup) => {
    const single = group.tables.length === 1;
    const name = groupLabel(group);
    const ok = await confirm(
      single
        ? `Таблица «${name}» и её ${formatCount(group.tables[0].rows, ROWS)} будут удалены. История диалогов останется.`
        : `База «${name}» и все её ${formatCount(group.tables.length, TABLES)} будут удалены. История диалогов останется.`,
      'Удалить базу?',
      { confirmLabel: 'Удалить' },
    );
    if (!ok) return;
    try {
      if (single) await dataApi.deleteDataset(group.tables[0].id);
      else await dataApi.deleteGroup(group.group_id);
      toast.success('База удалена');
      await Promise.all([fetchInfo(), reloadGroups()]);
    } catch (e) {
      toast.error(errorText(e, 'Не удалось удалить базу'));
    }
  };

  const handleDeleteTable = async (table: Dataset) => {
    const ok = await confirm(`Таблица «${table.name}» будет удалена из базы.`, 'Удалить таблицу?', { confirmLabel: 'Удалить' });
    if (!ok) return;
    try {
      await dataApi.deleteDataset(table.id);
      toast.success('Таблица удалена');
      await Promise.all([fetchInfo(), reloadGroups()]);
    } catch (e) {
      toast.error(errorText(e, 'Не удалось удалить таблицу'));
    }
  };

  const openRename = (target: RenameTarget) => {
    setRenameTarget(target);
    setRenameValue(target.name);
  };

  const submitRename = async () => {
    if (!renameTarget) return;
    const name = renameValue.trim();
    if (!name || name === renameTarget.name) {
      setRenameTarget(null);
      return;
    }
    setRenaming(true);
    try {
      if (renameTarget.kind === 'group') await dataApi.renameGroup(renameTarget.id, name);
      else await dataApi.renameDataset(renameTarget.id, name);
      await reloadGroups();
      toast.success('Название изменено');
      setRenameTarget(null);
    } catch (e) {
      toast.error(errorText(e, 'Не удалось переименовать'));
    } finally {
      setRenaming(false);
    }
  };

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const isSelected = (g: DatasetGroup) => g.tables.some((t) => t.id === selectedDatasetId);
  const uploadedAt = (g: DatasetGroup) => g.tables.reduce((latest, t) => (t.uploaded_at > latest ? t.uploaded_at : latest), g.tables[0]?.uploaded_at ?? '');

  const sourcesDescription = info
    ? info.rows > 0
      ? `Всего ${formatCount(info.rows, ROWS)}${info.date_range?.min ? ` · продажи за ${formatPeriod(info.date_range.min, info.date_range.max)}` : ''}`
      : 'Пока ничего не загружено'
    : undefined;

  return (
    <Page className="upload">
      <PageHeader title="Загрузка данных" description="Выгрузки продаж в CSV, Excel или файле базы данных (.db). Всё хранится только на этом компьютере" />

      {result && (
        <Alert
          tone="positive"
          title={`Загружено ${formatCount(result.rows_loaded, ROWS)}`}
          onClose={() => setResult(null)}
          action={
            canOpenTables ? (
              <Button size="sm" onClick={() => navigate(`/details?dataset=${result.dataset_id}`)}>
                Открыть таблицу
              </Button>
            ) : undefined
          }
        >
          Всего в базе {formatCount(result.total_rows, ROWS)}.
          {result.warnings?.length > 0 && (
            <ul className="upload__warnings">
              {result.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
        </Alert>
      )}

      <Panel title="Добавить данные">
        <div className="upload__add">
          <FileUpload onSuccess={handleSuccess} />

          <div className="upload__side">
            <div className="upload__block">
              <Field label="По ссылке" hint="Прямая ссылка на CSV или Excel" error={urlError}>
                {(id, describedBy) => (
                  <div className="upload__url-row">
                    <Input
                      id={id}
                      type="url"
                      inputMode="url"
                      placeholder="https://"
                      value={urlInput}
                      aria-describedby={describedBy}
                      onChange={(e) => {
                        setUrlInput(e.target.value);
                        setUrlError(null);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleUrlUpload();
                      }}
                    />
                    <Button onClick={handleUrlUpload} disabled={!urlInput.trim() || busy !== null} loading={busy === 'url'}>
                      Загрузить
                    </Button>
                  </div>
                )}
              </Field>
            </div>

            <div className="upload__block">
              <p className="upload__block-title">Демо-наборы</p>
              <p className="upload__block-text">Сгенерированные продажи, чтобы попробовать дашборд и ассистента</p>
              <div className="upload__demo">
                <Button size="sm" onClick={() => runDemo('demo')} loading={busy === 'demo'} disabled={busy !== null && busy !== 'demo'}>
                  Розница
                </Button>
                <Button size="sm" onClick={() => runDemo('luxury')} loading={busy === 'luxury'} disabled={busy !== null && busy !== 'luxury'}>
                  Люкс-сегмент
                </Button>
              </div>
            </div>
          </div>
        </div>
      </Panel>

      <Panel title="Источники" description={sourcesDescription} padding="none">
        {groups.length === 0 && groupsError ? (
          <ErrorState compact title="Не удалось загрузить список баз" onRetry={reloadGroups} />
        ) : groups.length === 0 && groupsLoading ? (
          <EmptyState compact icon={<Database />} title="Загружаю список баз…" />
        ) : groups.length === 0 ? (
          <EmptyState compact icon={<Database />} title="Нет загруженных баз" description="Загрузите файл или демо-набор выше." />
        ) : (
          <Table>
            <thead>
              <tr>
                <th>База</th>
                <th className="num">Размер</th>
                <th className="upload__col-date">Загружена</th>
                <th className="actions">
                  <span className="visually-hidden">Действия</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => {
                const multi = g.tables.length > 1;
                const open = expanded.has(g.group_id);
                const name = groupLabel(g);
                return (
                  <Fragment key={g.group_id}>
                    <tr className={isSelected(g) ? 'upload__row is-current' : 'upload__row'}>
                      <td>
                        <div className="upload__name-cell">
                          {multi ? (
                            <IconButton size="sm" label={open ? 'Свернуть таблицы' : 'Показать таблицы'} icon={open ? <ChevronDown /> : <ChevronRight />} onClick={() => toggle(g.group_id)} tooltip={false} aria-expanded={open} />
                          ) : (
                            <span className="upload__name-spacer" aria-hidden="true" />
                          )}
                          <button type="button" className="upload__name" onClick={() => selectGroup(g)} title="Сделать текущей базой">
                            {name}
                          </button>
                          {isSelected(g) && <Badge tone="accent">Текущая</Badge>}
                          {g.relationships?.length > 0 && (
                            // Сами связи (таблица.колонка → таблица.колонка) — в подсказке, как было до редизайна
                            <Badge title={g.relationships.map((r) => `${r.from_table}.${r.from_col} → ${r.to_table}.${r.to_col}`).join('\n')}>
                              {formatCount(g.relationships.length, ['связь', 'связи', 'связей'])}
                            </Badge>
                          )}
                        </div>
                      </td>
                      <td className="num mono">{groupMeta(g)}</td>
                      <td className="mono muted upload__col-date">{formatDate(uploadedAt(g))}</td>
                      <td className="actions">
                        <div>
                          <IconButton size="sm" label="Переименовать" icon={<Pencil />} onClick={() => openRename(multi ? { kind: 'group', id: g.group_id, name } : { kind: 'table', id: g.tables[0].id, name })} />
                          {isAdmin && <IconButton size="sm" variant="danger" label="Удалить базу" icon={<Trash2 />} onClick={() => handleDeleteGroup(g)} />}
                        </div>
                      </td>
                    </tr>
                    {multi &&
                      open &&
                      g.tables.map((t) => (
                        <tr key={t.id} className="upload__child">
                          <td>
                            <div className="upload__name-cell upload__name-cell--child">
                              <Table2 className="upload__child-icon" aria-hidden="true" />
                              {canOpenTables ? (
                                <button
                                  type="button"
                                  className="upload__name upload__name--mono"
                                  onClick={() => navigate(`/details?dataset=${t.id}`)}
                                  title="Открыть таблицу"
                                >
                                  {t.name}
                                </button>
                              ) : (
                                <span className="upload__name upload__name--mono" title={t.name}>
                                  {t.name}
                                </span>
                              )}
                            </div>
                          </td>
                          <td className="num mono muted">{formatNumber(t.rows)}</td>
                          <td className="upload__col-date" />
                          <td className="actions">
                            <div>
                              <IconButton size="sm" label="Переименовать таблицу" icon={<Pencil />} onClick={() => openRename({ kind: 'table', id: t.id, name: t.name })} />
                              {isAdmin && <IconButton size="sm" variant="danger" label="Удалить таблицу" icon={<Trash2 />} onClick={() => handleDeleteTable(t)} />}
                            </div>
                          </td>
                        </tr>
                      ))}
                  </Fragment>
                );
              })}
            </tbody>
          </Table>
        )}
      </Panel>

      <Modal
        open={renameTarget !== null}
        onClose={() => setRenameTarget(null)}
        title={renameTarget?.kind === 'group' ? 'Переименовать базу' : 'Переименовать таблицу'}
        size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={() => setRenameTarget(null)}>
              Отмена
            </Button>
            <Button variant="primary" onClick={submitRename} loading={renaming} disabled={!renameValue.trim()}>
              Сохранить
            </Button>
          </>
        }
      >
        <Field label="Название">
          {(id) => (
            <Input
              id={id}
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyDown={(e) => {
                // Те же условия, что у кнопки «Сохранить»: не во время сохранения и не с пустым именем
                if (e.key === 'Enter' && !renaming && renameValue.trim()) submitRename();
              }}
            />
          )}
        </Field>
      </Modal>
    </Page>
  );
}
