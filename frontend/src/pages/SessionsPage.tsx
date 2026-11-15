import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { History, MoreHorizontal, Plus, Trash2, X } from 'lucide-react';
import { sessionsApi } from '../shared/api/sessions.api';
import type { ChatSession } from '../shared/types';
import { useDataset } from '../shared/providers/DatasetContext';
import { useConfirm } from '../shared/providers/ConfirmContext';
import { useCanOpen } from '../components/shell/useCanOpen';
import { Badge, Button, EmptyState, ErrorState, IconButton, Menu, Page, PageHeader, Pagination, Panel, Skeleton, Table, Toolbar, ToolbarSpacer } from '../shared/ui';
import { formatDateTime } from '../shared/lib/format';
import { groupFilterId, groupLabel } from '../shared/lib/datasetLabel';
import { forgetCurrentSession } from '../shared/lib/analystSession';
import toast from 'react-hot-toast';
import './SessionsPage.css';

const STATUS_LABEL: Record<string, { text: string; tone: 'neutral' | 'negative' }> = {
  no_response: { text: 'Без ответа', tone: 'neutral' },
  error: { text: 'Ошибка', tone: 'negative' },
};

export default function SessionsPage() {
  const navigate = useNavigate();
  // История доступна и без права на ассистента — тогда диалоги только просматриваются списком
  const canOpenDialogs = useCanOpen()('/analyst');
  const confirm = useConfirm();
  const { selectedDatasetId, currentGroup } = useDataset();

  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [totalPages, setTotalPages] = useState(1);
  const [total, setTotal] = useState(0);

  // Номер последнего запроса: ответ на устаревший запрос не перезаписывает свежий
  const requestRef = useRef(0);

  // Шапка говорит «по базе …», поэтому фильтруем по всей базе, а не по выбранной в ней таблице
  const datasetFilter = currentGroup ? groupFilterId(currentGroup) : selectedDatasetId;

  // Смена базы сбрасывает страницу в том же рендере, чтобы запрос сразу ушёл с page=1.
  // Даты и размер страницы сбрасывают её в своих обработчиках
  const [prevDatasetFilter, setPrevDatasetFilter] = useState(datasetFilter);
  if (prevDatasetFilter !== datasetFilter) {
    setPrevDatasetFilter(datasetFilter);
    setPage(1);
  }

  const loadSessions = useCallback(async () => {
    const request = ++requestRef.current;
    setLoading(true);
    setError(false);
    try {
      // Даты из <input type="date"> уже локальные «YYYY-MM-DD» — без сдвига часового пояса
      const data = await sessionsApi.getSessions(fromDate, toDate, datasetFilter || null, page, pageSize);
      if (request !== requestRef.current) return;
      setSessions(data.sessions);
      setTotal(data.total);
      setTotalPages(data.total_pages);
      // Бэкенд ограничивает номер страницы их числом: после удаления последнего диалога на странице
      // он вернул предыдущую — показываем её номер, иначе пагинация пишет «2 / 1»
      if (data.page !== page) setPage(data.page);
    } catch (e) {
      if (request !== requestRef.current) return;
      console.error('Failed to load sessions', e);
      setError(true);
    } finally {
      if (request === requestRef.current) setLoading(false);
    }
  }, [fromDate, toDate, datasetFilter, page, pageSize]);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  const changeFromDate = (value: string) => {
    setFromDate(value);
    setPage(1);
  };
  const changeToDate = (value: string) => {
    setToDate(value);
    setPage(1);
  };
  const changePageSize = (size: number) => {
    setPageSize(size);
    setPage(1);
  };

  const handleClearAll = async () => {
    // total — число диалогов в текущем фильтре (база, даты), а удаляются все диалоги пользователя,
    // поэтому счётчик на кнопке не показываем
    const ok = await confirm('Все ваши диалоги по всем базам и за все даты будут удалены вместе с историей. Загруженные базы и настройки не изменятся.', 'Удалить все диалоги?', {
      confirmLabel: 'Удалить все',
    });
    if (!ok) return;
    try {
      await sessionsApi.clearMySessions();
      toast.success('Диалоги удалены');
      // На первой странице перезагружаем сами, иначе смена страницы перезагрузит список
      if (page === 1) loadSessions();
      else setPage(1);
    } catch (e) {
      console.error(e);
      toast.error('Не удалось удалить диалоги');
    }
  };

  const handleDelete = async (session: ChatSession) => {
    const ok = await confirm(`«${session.title}» будет удалён без возможности восстановления.`, 'Удалить диалог?', { confirmLabel: 'Удалить' });
    if (!ok) return;
    try {
      await sessionsApi.deleteSession(session.id);
      toast.success('Диалог удалён');
      loadSessions();
    } catch (e) {
      console.error(e);
      toast.error('Не удалось удалить диалог');
    }
  };

  const datasetName = currentGroup ? groupLabel(currentGroup) : null;
  const hasFilter = Boolean(fromDate || toDate);

  return (
    <Page className="sessions">
      <PageHeader
        title="История"
        description={datasetName ? `Диалоги с ассистентом по базе ${datasetName}` : 'Диалоги с ассистентом'}
        actions={
          canOpenDialogs && (
            <Button
              variant="primary"
              icon={<Plus />}
              onClick={() => {
                // иначе /analyst продолжит последний открытый диалог
                forgetCurrentSession();
                navigate('/analyst');
              }}
            >
              Новый диалог
            </Button>
          )
        }
      />

      <Panel padding="none">
        <Toolbar className="sessions__toolbar">
          <label className="sessions__date">
            <span>С</span>
            <input type="date" value={fromDate} max={toDate || undefined} onChange={(e) => changeFromDate(e.target.value)} aria-label="Дата начала" />
          </label>
          <label className="sessions__date">
            <span>по</span>
            <input type="date" value={toDate} min={fromDate || undefined} onChange={(e) => changeToDate(e.target.value)} aria-label="Дата окончания" />
          </label>
          {hasFilter && (
            <Button
              size="sm"
              variant="ghost"
              icon={<X />}
              onClick={() => {
                setFromDate('');
                setToDate('');
                setPage(1);
              }}
            >
              Сбросить
            </Button>
          )}
          <ToolbarSpacer />
          <Menu
            align="end"
            // Удаляются диалоги по всем базам и датам, поэтому пустой текущий фильтр не повод блокировать пункт
            items={[{ key: 'clear', label: 'Удалить все мои диалоги', icon: <Trash2 />, tone: 'danger', onSelect: handleClearAll }]}
            trigger={(props) => <IconButton {...props} label="Ещё действия" icon={<MoreHorizontal />} />}
          />
        </Toolbar>

        {error ? (
          <ErrorState onRetry={loadSessions} />
        ) : loading && sessions.length === 0 ? (
          <div className="sessions__skeleton">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="sessions__skeleton-row">
                <Skeleton width={`${60 - i * 5}%`} height={14} />
                <Skeleton width={120} height={14} />
              </div>
            ))}
          </div>
        ) : sessions.length === 0 ? (
          <EmptyState
            icon={<History />}
            title={hasFilter ? 'За выбранные даты диалогов нет' : 'Пока нет диалогов'}
            description={hasFilter ? 'Измените период или сбросьте фильтр.' : 'Задайте ассистенту первый вопрос — диалог сохранится здесь.'}
            action={
              hasFilter || !canOpenDialogs ? undefined : (
                <Button variant="primary" onClick={() => navigate('/analyst')}>
                  Спросить ассистента
                </Button>
              )
            }
          />
        ) : (
          <>
            <Table interactive={canOpenDialogs} loading={loading} className="sessions__table">
              <thead>
                <tr>
                  <th>Диалог</th>
                  <th className="sessions__col-date">Обновлён</th>
                  <th className="actions">
                    <span className="visually-hidden">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => {
                  const status = s.status ? STATUS_LABEL[s.status] : undefined;
                  return (
                    <tr key={s.id} onClick={canOpenDialogs ? () => navigate(`/analyst?session_id=${s.id}`) : undefined}>
                      <td>
                        <div className="sessions__title-cell">
                          {canOpenDialogs ? (
                            <Link to={`/analyst?session_id=${s.id}`} className="sessions__title" title={s.title} onClick={(e) => e.stopPropagation()}>
                              {s.title}
                            </Link>
                          ) : (
                            <span className="sessions__title" title={s.title}>
                              {s.title}
                            </span>
                          )}
                          {status && <Badge tone={status.tone}>{status.text}</Badge>}
                        </div>
                      </td>
                      <td className="num muted sessions__col-date">{formatDateTime(s.updated_at || s.created_at)}</td>
                      <td className="actions" onClick={(e) => e.stopPropagation()}>
                        <IconButton size="sm" variant="danger" label="Удалить диалог" icon={<Trash2 />} onClick={() => handleDelete(s)} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
            <Pagination page={page} pageCount={totalPages} onPageChange={setPage} total={total} pageSize={pageSize} pageSizeOptions={[10, 20, 50, 100]} onPageSizeChange={changePageSize} />
          </>
        )}
      </Panel>
    </Page>
  );
}
