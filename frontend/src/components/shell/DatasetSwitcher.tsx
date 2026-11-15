import { useNavigate } from 'react-router-dom';
import { ChevronsUpDown, Database, RotateCw, Upload } from 'lucide-react';
import { Menu } from '../../shared/ui';
import type { MenuEntry } from '../../shared/ui';
import { useDataset } from '../../shared/providers/DatasetContext';
import { useCanOpen } from './useCanOpen';
import { groupLabel, groupMeta } from '../../shared/lib/datasetLabel';

/** Переключатель базы данных: действует на дашборд, ассистента и таблицу данных */
export function DatasetSwitcher({ compact = false, onNavigate }: { compact?: boolean; onNavigate?: () => void }) {
  const { groups, groupsLoading, groupsError, reloadGroups, currentGroup, selectGroup } = useDataset();
  const canOpen = useCanOpen();
  const navigate = useNavigate();

  // Ошибка загрузки списка — не то же самое, что «баз нет»: не предлагаем загрузить файл заново
  const title = currentGroup ? groupLabel(currentGroup) : groupsLoading ? 'Загрузка…' : groupsError ? 'Базы недоступны' : 'Нет данных';
  const meta = currentGroup ? groupMeta(currentGroup) : groupsLoading ? '' : groupsError ? 'Ошибка загрузки' : 'Загрузите файл';

  const items: MenuEntry[] = [];
  if (groups.length > 0) {
    items.push({ type: 'label', label: 'Базы данных' });
    groups.forEach((g) => {
      items.push({
        key: g.group_id,
        label: groupLabel(g),
        meta: groupMeta(g),
        icon: <Database />,
        selected: g === currentGroup,
        onSelect: () => selectGroup(g),
      });
    });
  } else if (groupsError) {
    items.push({ type: 'label', label: 'Не удалось загрузить список баз' });
    items.push({ key: 'retry', label: 'Повторить', icon: <RotateCw />, onSelect: () => void reloadGroups() });
  } else if (!groupsLoading) {
    items.push({ type: 'label', label: 'Пока нет загруженных баз' });
  }
  if (canOpen('/settings/data')) {
    if (items.length > 0) items.push({ type: 'separator' });
    items.push({
      key: 'upload',
      label: 'Загрузить данные',
      icon: <Upload />,
      onSelect: () => {
        navigate('/settings/data');
        onNavigate?.();
      },
    });
  }

  return (
    <Menu
      align={compact ? 'start' : 'stretch'}
      width={compact ? 280 : undefined}
      items={items}
      className="dataset-switcher"
      trigger={(props) =>
        compact ? (
          <button
            {...props}
            type="button"
            className="dataset-switcher__trigger dataset-switcher__trigger--compact"
            aria-label={`База данных: ${title}`}
            data-tooltip-id="ui-tooltip"
            data-tooltip-content={title}
            data-tooltip-place="right"
          >
            <Database />
          </button>
        ) : (
          <button {...props} type="button" className="dataset-switcher__trigger" aria-label={`База данных: ${title}. Сменить`}>
            <span className="dataset-switcher__text">
              <span className="dataset-switcher__name">{title}</span>
              {meta && <span className="dataset-switcher__meta">{meta}</span>}
            </span>
            <ChevronsUpDown className="dataset-switcher__chevron" />
          </button>
        )
      }
    />
  );
}
