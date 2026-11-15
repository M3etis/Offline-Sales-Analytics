import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { dataApi } from '../api/data.api';
import type { Dataset, DatasetGroup } from '../types';
import { useAuth } from './AuthContext';
import { findGroupOf } from '../lib/datasetLabel';

interface DatasetContextType {
  selectedDatasetId: string | null;
  setSelectedDatasetId: (id: string | null) => void;
  /** Все загруженные базы, сгруппированные по исходному файлу */
  groups: DatasetGroup[];
  groupsLoading: boolean;
  groupsError: boolean;
  reloadGroups: () => Promise<void>;
  /** Выбрать одиночную таблицу или группу целиком */
  selectGroup: (group: DatasetGroup) => void;
  /** Многотабличная база с выбранной таблицей; null — выбрана одиночная таблица */
  activeGroup: DatasetGroup | null;
  /** База любого размера, в которую входит выбранная таблица: её название показывают шапки и переключатель */
  currentGroup: DatasetGroup | null;
  activeDataset: Dataset | null;
}

const DatasetContext = createContext<DatasetContextType | undefined>(undefined);

function persist(key: string, value: string | null) {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    /* private mode */
  }
}

export const DatasetProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const { isAuthenticated } = useAuth();
  const [selectedDatasetId, setSelectedDatasetIdState] = useState<string | null>(() => localStorage.getItem('selectedDatasetId'));
  const [groups, setGroups] = useState<DatasetGroup[]>([]);
  const [groupsLoading, setGroupsLoading] = useState(false);
  const [groupsError, setGroupsError] = useState(false);
  const [loadedOnce, setLoadedOnce] = useState(false);
  // Последний выбор, известный синхронно: эффекты детей (например, /details?dataset=X) выполняются
  // раньше эффекта автовыбора, и в его замыкании selectedDatasetId ещё старый
  const selectedRef = useRef(selectedDatasetId);

  const setSelectedDatasetId = useCallback((id: string | null) => {
    selectedRef.current = id;
    setSelectedDatasetIdState(id);
    persist('selectedDatasetId', id);
  }, []);

  // Группа определяется по выбранной таблице (activeGroup), поэтому достаточно выбрать её первую таблицу
  const selectGroup = useCallback(
    (group: DatasetGroup) => {
      setSelectedDatasetId(group.tables[0]?.id ?? null);
    },
    [setSelectedDatasetId],
  );

  const reloadGroups = useCallback(async () => {
    setGroupsLoading(true);
    setGroupsError(false);
    try {
      const data = await dataApi.getGroups();
      setGroups(data);
      setLoadedOnce(true);
    } catch (e) {
      console.error(e);
      setGroupsError(true);
    } finally {
      setGroupsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isAuthenticated) {
      reloadGroups();
    } else {
      setGroups([]);
      setLoadedOnce(false);
    }
  }, [isAuthenticated, reloadGroups]);

  // Базы могли загрузить или удалить в другом окне (или другой пользователь) — при возврате в окно
  // тихо перечитываем список. Ошибка фонового обновления не показывается: старый список остаётся
  useEffect(() => {
    if (!isAuthenticated) return;
    let lastRefresh = Date.now();
    const refresh = () => {
      if (document.visibilityState !== 'visible' || Date.now() - lastRefresh < 5000) return;
      lastRefresh = Date.now();
      dataApi
        .getGroups({ background: true })
        .then((data) => {
          setGroups(data);
          setLoadedOnce(true);
          // Список получен — ошибка прошлой загрузки больше не актуальна
          setGroupsError(false);
        })
        .catch(() => {});
    };
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [isAuthenticated]);

  // Если выбранная база исчезла (удалена) или ничего не выбрано — выбрать первую
  useEffect(() => {
    if (!loadedOnce || groupsLoading || groupsError) return;
    const current = selectedRef.current;
    if (groups.length === 0) {
      // все базы удалены — сбросить выбор
      if (current) {
        setSelectedDatasetId(null);
      }
      return;
    }
    const exists = groups.some((g) => g.tables.some((t) => t.id === current));
    if (!exists) selectGroup(groups[0]);
  }, [groups, loadedOnce, groupsLoading, groupsError, selectedDatasetId, selectGroup, setSelectedDatasetId]);

  // До первого ответа список ещё грузится, а не пуст: эффект с reloadGroups запускается
  // только после первой отрисовки, и без этого интерфейс успевает показать «Нет данных»
  const groupsPending = groupsLoading || (isAuthenticated && !loadedOnce && !groupsError);

  const currentGroup = useMemo(() => findGroupOf(groups, selectedDatasetId), [groups, selectedDatasetId]);
  const activeGroup = currentGroup && currentGroup.tables.length > 1 ? currentGroup : null;

  const activeDataset = useMemo(() => {
    for (const g of groups) {
      const t = g.tables.find((x) => x.id === selectedDatasetId);
      if (t) return t;
    }
    return null;
  }, [groups, selectedDatasetId]);

  const value = useMemo(
    () => ({
      selectedDatasetId,
      setSelectedDatasetId,
      groups,
      groupsLoading: groupsPending,
      groupsError,
      reloadGroups,
      selectGroup,
      activeGroup,
      currentGroup,
      activeDataset,
    }),
    [selectedDatasetId, setSelectedDatasetId, groups, groupsPending, groupsError, reloadGroups, selectGroup, activeGroup, currentGroup, activeDataset],
  );

  return <DatasetContext.Provider value={value}>{children}</DatasetContext.Provider>;
};

export const useDataset = () => {
  const context = useContext(DatasetContext);
  if (context === undefined) {
    throw new Error('useDataset must be used within a DatasetProvider');
  }
  return context;
};
