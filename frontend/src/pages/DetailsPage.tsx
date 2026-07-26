import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useDataset } from '../context/DatasetContext';
import { useConfirm } from '../context/ConfirmContext';
import DataTable from '../components/DataTable';
import DatasetGroupComponent from '../components/DatasetGroup';
import { dataApi } from '../services/api';
import type { DatasetGroup } from '../services/api';
import { Database, ArrowUpDown, Table } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';

export default function DetailsPage() {
  const [searchParams] = useSearchParams();
  const [groups, setGroups] = useState<DatasetGroup[]>([]);
  const { selectedDatasetId, setSelectedDatasetId, selectedGroupId, setSelectedGroupId } = useDataset();
  const confirm = useConfirm();
  const { isAdmin } = useAuth();
  const [sortAsc, setSortAsc] = useState(false);

  useEffect(() => {
    fetchGroups();
  }, []);

  const fetchGroups = async () => {
    try {
      const data = await dataApi.getGroups();
      setGroups(data);

      const allDatasets = data.flatMap(g => g.tables);
      const queryId = searchParams.get('dataset');
      if (queryId && allDatasets.find(d => d.id === queryId)) {
        setSelectedDatasetId(queryId);
        // If it belongs to a group, select the group
        const group = data.find(g => g.tables.some(t => t.id === queryId));
        if (group && group.tables.length > 1) {
          setSelectedGroupId(group.group_id);
        }
      } else if (selectedDatasetId && allDatasets.find(d => d.id === selectedDatasetId)) {
        // Keep current
      } else if (allDatasets.length > 0) {
        // Find first group and select it by default
        const firstGroup = data.find(g => g.tables.length > 1);
        if (firstGroup) {
          setSelectedGroupId(firstGroup.group_id);
          setSelectedDatasetId(firstGroup.tables[0].id);
        } else {
          setSelectedDatasetId(allDatasets[0].id);
        }
      } else {
        setSelectedDatasetId('');
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleDeleteDataset = async (id: string, name: string) => {
    const isConfirmed = await confirm(
      `Вы уверены, что хотите удалить "${name}"?`,
      "Удаление"
    );
    if (!isConfirmed) return;

    try {
      await dataApi.deleteDataset(id);
      await fetchGroups();
      if (selectedDatasetId === id) {
        const remaining = groups.flatMap(g => g.tables).filter(d => d.id !== id);
        setSelectedDatasetId(remaining.length > 0 ? remaining[0].id : '');
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleDeleteGroup = async (groupId: string) => {
    const group = groups.find(g => g.group_id === groupId);
    const tableCount = group?.tables.length || 0;
    const isConfirmed = await confirm(
      `Удалить группу "${group?.source_file}" (${tableCount} таблиц)?`,
      "Удаление группы"
    );
    if (!isConfirmed) return;

    try {
      await dataApi.deleteGroup(groupId);
      await fetchGroups();
      if (group && group.tables.find(t => t.id === selectedDatasetId)) {
        const remaining = groups.flatMap(g => g.tables).filter(t => t.source_group !== groupId);
        setSelectedDatasetId(remaining.length > 0 ? remaining[0].id : '');
      }
    } catch (e) {
      console.error(e);
    }
  };

  const sortedGroups = [...groups].sort((a, b) => {
    const aIsSingle = a.tables.length === 1 && !a.relationships.length;
    const bIsSingle = b.tables.length === 1 && !b.relationships.length;
    if (aIsSingle !== bIsSingle) return aIsSingle ? 1 : -1;
    const aTime = new Date(a.tables[0]?.uploaded_at || 0).getTime();
    const bTime = new Date(b.tables[0]?.uploaded_at || 0).getTime();
    return sortAsc ? aTime - bTime : bTime - aTime;
  });

  return (
    <div className="scrollable-page">
      <div className="page-container">
        <header className="page-header animate-fade-in">
          <h1>Сырые данные</h1>
          <p className="text-muted">Просмотр и фильтрация всех загруженных данных</p>
        </header>

        <div className="details-page details-layout">
          <div className="details-layout__content">
            <div className="details-content__table-wrapper">
          {selectedGroupId && selectedGroupId !== '' ? (
            <div className="card" style={{ padding: '40px', textAlign: 'center' }}>
              <Table size={48} style={{ color: 'var(--accent)', marginBottom: '16px' }} />
              <h3 style={{ marginBottom: '8px' }}>Выбрана группа баз данных</h3>
              <p className="text-muted">
                Просмотр таблицы невозможен для группы связанных таблиц.
                <br />
                Выберите конкретную таблицу в панели справа для просмотра её содержимого.
              </p>
            </div>
          ) : selectedDatasetId ? (
            <DataTable datasetId={selectedDatasetId} />
          ) : (
            <div className="card details-content__empty">
              Нет выбранной базы данных. Загрузите данные в разделе "Данные".
            </div>
          )}
        </div>
      </div>

      {/* Правый сайдбар */}
      <div className="details-layout__sidebar animate-slide-left">
        <div className="card dataset-list">
          <div className="dataset-list__header">
            <h3 className="dataset-list__title">
              <Database size={18} className="text-accent" />
              Базы данных
            </h3>
            <button
              className="btn btn--tertiary btn--icon dataset-list__sort-btn"
              onClick={() => setSortAsc(!sortAsc)}
              title="Сортировка по дате"
            >
              <ArrowUpDown size={16} />
            </button>
          </div>

          <div className="dataset-list__items">
            {sortedGroups.length === 0 ? (
              <p className="text-muted dataset-list__empty">Нет загруженных баз</p>
            ) : (
              sortedGroups.map(group => (
                <DatasetGroupComponent
                  key={group.group_id}
                  group={group}
                  selectedDatasetId={selectedDatasetId}
                  selectedGroupId={selectedGroupId}
                  onSelectDataset={(id) => {
                    // For single datasets, select directly
                    if (group.tables.length === 1) {
                      setSelectedDatasetId(id);
                      setSelectedGroupId(null);
                    } else {
                      // For multi-table groups, always select the group
                      setSelectedGroupId(group.group_id);
                      setSelectedDatasetId(group.tables[0].id);
                    }
                  }}
                  onSelectGroup={(gid, primaryId) => { setSelectedGroupId(gid); setSelectedDatasetId(primaryId); }}
                  onViewDataset={(id) => {
                    // View individual dataset within a group
                    setSelectedDatasetId(id);
                    setSelectedGroupId(null);
                  }}
                  onDeleteDataset={handleDeleteDataset}
                  onDeleteGroup={group.tables.length > 1 ? handleDeleteGroup : undefined}
                  isAdmin={isAdmin}
                  onRename={(id, newName) => {
                    setGroups(prev => prev.map(g =>
                      g.group_id === id ? { ...g, source_file: newName } :
                      g.tables.some(t => t.id === id) ? { ...g, tables: g.tables.map(t => t.id === id ? { ...t, name: newName } : t) } :
                      g
                    ));
                  }}
                />
              ))
            )}
          </div>
        </div>
      </div>

        </div>
      </div>
    </div>
  );
}
