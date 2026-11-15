import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { MessageSquareText, ThumbsDown, ThumbsUp } from 'lucide-react';
import { sessionsApi } from '../shared/api/sessions.api';
import { useDataset } from '../shared/providers/DatasetContext';
import { useCanOpen } from '../components/shell/useCanOpen';
import { EmptyState, ErrorState, Page, PageHeader, Pagination, Panel, Segmented, Select, SkeletonLines, Stat, Table, Toolbar, ToolbarSpacer } from '../shared/ui';
import { formatDateTime, formatNumber, formatPercent } from '../shared/lib/format';
import { groupFilterId, groupLabel } from '../shared/lib/datasetLabel';
import './FeedbackStatsPage.css';

interface FeedbackStats {
  positive: number;
  negative: number;
  total: number;
}

interface FeedbackMessage {
  id: string;
  answer: string;
  feedback: string;
  created_at: string;
  session_id: string;
  session_title: string;
  dataset_id: string;
  question: string;
  /** Диалог текущего пользователя: чужой ассистент не откроет */
  is_own?: boolean;
}

type Filter = 'all' | 'positive' | 'negative';

const PAGE_SIZE = 10;

export default function FeedbackStatsPage() {
  const navigate = useNavigate();
  const canOpenDialogs = useCanOpen()('/analyst');
  const { groups } = useDataset();
  const [stats, setStats] = useState<FeedbackStats | null>(null);
  const [messages, setMessages] = useState<FeedbackMessage[]>([]);
  const [selectedDataset, setSelectedDataset] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const datasetOptions = useMemo(
    () =>
      // Название и значение фильтра — по общим правилам datasetLabel.ts, как в переключателе и «Истории»
      groups.map((g) => ({
        id: groupFilterId(g) ?? g.group_id,
        name: g.tables.length > 1 ? `${groupLabel(g)} · группа` : groupLabel(g),
      })),
    [groups],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      // Сначала сводка: по ней знаем, сколько оценок запросить. Без limit бэкенд отдаёт только 50,
      // и при большем числе оценок старые были бы недоступны на любой странице списка
      const s: FeedbackStats = await sessionsApi.getFeedbackStats(selectedDataset || undefined);
      // Не меньше прежних 50: если сводка не посчиталась (бэкенд тогда отдаёт нули), список всё равно виден
      const count = Math.max(filter === 'all' ? s.total : s[filter], 50);
      const m = await sessionsApi.getFeedbackMessages(selectedDataset || undefined, filter === 'all' ? undefined : filter, count);
      setStats(s);
      setMessages(m);
    } catch (e) {
      console.error(e);
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [selectedDataset, filter]);

  useEffect(() => {
    setPage(1);
    load();
  }, [load]);

  const total = stats?.total ?? 0;
  const positiveShare = total > 0 ? ((stats?.positive ?? 0) / total) * 100 : 0;
  const negativeShare = total > 0 ? ((stats?.negative ?? 0) / total) * 100 : 0;
  const pageCount = Math.max(1, Math.ceil(messages.length / PAGE_SIZE));
  const pageItems = messages.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  return (
    <Page className="feedback">
      <PageHeader
        title="Оценки ответов"
        description="Ответы с оценкой «полезно» ассистент запоминает и использует как примеры для похожих вопросов"
        actions={
          <Select value={selectedDataset} onChange={(e) => setSelectedDataset(e.target.value)} aria-label="База данных">
            <option value="">Все базы</option>
            {datasetOptions.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </Select>
        }
      />

      {error ? (
        <Panel>
          <ErrorState onRetry={load} />
        </Panel>
      ) : (
        <>
          <section className="feedback__summary" aria-label="Сводка оценок">
            <div className="feedback__stats">
              <Stat label="Всего оценок" loading={loading && !stats} value={formatNumber(total)} />
              <Stat label="Полезно" loading={loading && !stats} value={formatNumber(stats?.positive ?? 0)} />
              <Stat label="Бесполезно" loading={loading && !stats} value={formatNumber(stats?.negative ?? 0)} />
              <Stat label="Доля полезных" loading={loading && !stats} value={total > 0 ? formatPercent(positiveShare, 0) : '—'} emphasis={total > 0} />
            </div>
            <div className="feedback__bar-wrap">
              <div className="feedback__bar" role="img" aria-label={`Полезно ${formatPercent(positiveShare, 0)}, бесполезно ${formatPercent(negativeShare, 0)}`}>
                {total > 0 ? (
                  <>
                    <span className="feedback__bar-positive" style={{ width: `${positiveShare}%` }} />
                    <span className="feedback__bar-negative" style={{ width: `${negativeShare}%` }} />
                  </>
                ) : null}
              </div>
              <div className="feedback__legend">
                <span>
                  <i className="feedback__swatch feedback__swatch--positive" /> Полезно {formatPercent(positiveShare, 0)}
                </span>
                <span>
                  <i className="feedback__swatch feedback__swatch--negative" /> Бесполезно {formatPercent(negativeShare, 0)}
                </span>
              </div>
            </div>
          </section>

          <Panel padding="none">
            <Toolbar className="feedback__toolbar">
              <h2 className="feedback__list-title">Оценённые вопросы</h2>
              <ToolbarSpacer />
              <Segmented<Filter>
                label="Фильтр оценок"
                size="sm"
                value={filter}
                onChange={setFilter}
                options={[
                  { value: 'all', label: 'Все' },
                  { value: 'positive', label: 'Полезные', icon: <ThumbsUp /> },
                  { value: 'negative', label: 'Бесполезные', icon: <ThumbsDown /> },
                ]}
              />
            </Toolbar>

            {loading && messages.length === 0 ? (
              <SkeletonLines className="feedback__skeleton" widths={Array.from({ length: 5 }, (_, i) => `${75 - i * 7}%`)} />
            ) : messages.length === 0 ? (
              <EmptyState
                compact
                icon={<MessageSquareText />}
                title="Пока нет оценок"
                description="Отмечайте ответы ассистента кнопками «полезно» и «бесполезно» — они появятся здесь."
              />
            ) : (
              <>
                <Table interactive={canOpenDialogs}>
                  <thead>
                    <tr>
                      <th className="feedback__col-mark">
                        <span className="visually-hidden">Оценка</span>
                      </th>
                      <th>Вопрос</th>
                      <th className="feedback__col-session">Диалог</th>
                      <th className="feedback__col-date">Дата</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pageItems.map((m) => (
                      <tr key={m.id} onClick={canOpenDialogs && m.is_own !== false ? () => navigate(`/analyst?session_id=${m.session_id}`) : undefined}>
                        <td className="feedback__col-mark">
                          {m.feedback === 'positive' ? (
                            <ThumbsUp className="feedback__mark feedback__mark--positive" aria-label="Полезно" />
                          ) : (
                            <ThumbsDown className="feedback__mark feedback__mark--negative" aria-label="Бесполезно" />
                          )}
                        </td>
                        <td>
                          {canOpenDialogs && m.is_own !== false ? (
                            <button
                              type="button"
                              className="feedback__question"
                              onClick={(e) => {
                                e.stopPropagation();
                                navigate(`/analyst?session_id=${m.session_id}`);
                              }}
                            >
                              {m.question}
                            </button>
                          ) : (
                            <span className="feedback__question" title={m.is_own === false ? 'Диалог другого пользователя' : undefined}>
                              {m.question}
                            </span>
                          )}
                        </td>
                        <td className="muted feedback__col-session" title={m.session_title}>
                          <span className="feedback__session">{m.session_title}</span>
                        </td>
                        <td className="mono muted feedback__col-date">{formatDateTime(m.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
                {messages.length > PAGE_SIZE && <Pagination page={page} pageCount={pageCount} onPageChange={setPage} total={messages.length} pageSize={PAGE_SIZE} />}
              </>
            )}
          </Panel>
        </>
      )}
    </Page>
  );
}
