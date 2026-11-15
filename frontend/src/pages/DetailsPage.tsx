import { useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Database } from 'lucide-react';
import DataTable from '../components/DataTable';
import { useDataset } from '../shared/providers/DatasetContext';
import { useCanOpen } from '../components/shell/useCanOpen';
import { Button, EmptyState, ErrorState, Page, PageHeader, Panel, Segmented, Skeleton, SkeletonLines } from '../shared/ui';
import { formatCount, formatDate, ROWS, TABLES } from '../shared/lib/format';
import { groupLabel } from '../shared/lib/datasetLabel';
import './DetailsPage.css';

export default function DetailsPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const canOpen = useCanOpen();
  const { groups, groupsLoading, groupsError, reloadGroups, selectedDatasetId, setSelectedDatasetId, activeGroup, currentGroup, activeDataset } = useDataset();

  // Поддержка ссылок вида /details?dataset=<id>
  useEffect(() => {
    const queryId = searchParams.get('dataset');
    if (!queryId || groups.length === 0) return;
    if (groups.some((g) => g.tables.some((t) => t.id === queryId))) setSelectedDatasetId(queryId);
    setSearchParams({}, { replace: true });
  }, [searchParams, groups, setSelectedDatasetId, setSearchParams]);

  if (groupsError) {
    return (
      <Page width="wide">
        <PageHeader title="Данные" />
        <Panel>
          <ErrorState onRetry={reloadGroups} />
        </Panel>
      </Page>
    );
  }

  if (!groupsLoading && groups.length === 0) {
    return (
      <Page width="wide">
        <PageHeader title="Данные" />
        <Panel>
          <EmptyState
            icon={<Database />}
            title="Нет загруженных баз"
            description="Загрузите выгрузку продаж — здесь можно будет просматривать и искать строки."
            action={
              canOpen('/settings/data') ? (
                <Button variant="primary" onClick={() => navigate('/settings/data')}>
                  Загрузить данные
                </Button>
              ) : undefined
            }
          />
        </Panel>
      </Page>
    );
  }

  const title = currentGroup ? groupLabel(currentGroup) : null;
  const meta = activeGroup
    ? `${formatCount(activeGroup.tables.length, TABLES)} · загружена ${formatDate(activeGroup.tables[0]?.uploaded_at)}`
    : activeDataset
      ? `${formatCount(activeDataset.rows, ROWS)} · загружена ${formatDate(activeDataset.uploaded_at)}`
      : null;

  return (
    <Page width="wide" className="details">
      <PageHeader title="Данные" description={title ? [title, meta].filter(Boolean).join(' · ') : <Skeleton width={240} height={14} />} />

      {activeGroup && (
        <div className="details__tables">
          <span className="details__tables-label">Таблица</span>
          <Segmented
            label="Таблица базы"
            size="sm"
            value={selectedDatasetId ?? activeGroup.tables[0].id}
            onChange={(id) => setSelectedDatasetId(id)}
            options={activeGroup.tables.map((t) => ({ value: t.id, label: t.name }))}
          />
        </div>
      )}

      <Panel padding="none" className="details__panel">
        {selectedDatasetId ? (
          <DataTable datasetId={selectedDatasetId} />
        ) : (
          <SkeletonLines className="details__loading" widths={['40%', '70%', '55%']} />
        )}
      </Panel>
    </Page>
  );
}
