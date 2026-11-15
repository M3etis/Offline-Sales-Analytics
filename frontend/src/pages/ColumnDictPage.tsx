import { useCallback, useEffect, useState } from 'react';
import { BookOpen, MoreHorizontal, Pencil, Plus, RotateCcw, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { settingsApi } from '../shared/api/settings.api';
import type { ColumnDictItem } from '../shared/api/settings.api';
import { useConfirm } from '../shared/providers/ConfirmContext';
import { Badge, Button, EmptyState, ErrorState, Field, IconButton, Input, Menu, Modal, Page, PageHeader, Pagination, Panel, SearchInput, Select, SkeletonLines, Table, Toolbar, ToolbarSpacer } from '../shared/ui';
import { formatCount } from '../shared/lib/format';
import { useDebouncedValue } from '../shared/hooks/useDebouncedValue';
import { reloadColumnLabels } from '../shared/lib/columnLabels';
import './ColumnDictPage.css';

const CATEGORY_LABELS: Record<string, string> = {
  dates: 'Даты',
  finance: 'Финансы',
  quantity: 'Количества',
  identifiers: 'Идентификаторы',
  text: 'Текст',
  classification: 'Классификация',
  sales: 'Продажи',
  marketing: 'Маркетинг',
  logistics: 'Логистика',
  customers: 'Клиенты',
  other: 'Прочее',
};

const PAGE_SIZE = 50;
const EMPTY: ColumnDictItem = { en_name: '', ru_name: '', category: 'other' };
const ENTRIES: [string, string, string] = ['запись', 'записи', 'записей'];

const label = (c: string) => CATEGORY_LABELS[c] || c;

export default function ColumnDictPage() {
  const confirm = useConfirm();
  const [items, setItems] = useState<ColumnDictItem[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [category, setCategory] = useState('');
  const [search, setSearch] = useState('');
  const debounced = useDebouncedValue(search.trim(), 300);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [page, setPage] = useState(1);
  const [form, setForm] = useState<ColumnDictItem | null>(null);
  const [saving, setSaving] = useState(false);
  const fetchItems = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const data = await settingsApi.getColumnDictionary(category || undefined, debounced || undefined);
      setItems(data.items);
      setPage(1);
    } catch (e) {
      console.error('Failed to fetch column dictionary:', e);
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [category, debounced]);

  const fetchCategories = useCallback(async () => {
    try {
      const data = await settingsApi.getColumnDictCategories();
      setCategories(data.categories);
    } catch (e) {
      console.error('Failed to fetch categories:', e);
    }
  }, []);

  useEffect(() => {
    fetchCategories();
  }, [fetchCategories]);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  const pageCount = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const pageItems = items.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const categoryOptions = Array.from(new Set([...Object.keys(CATEGORY_LABELS), ...categories]));

  const submit = async () => {
    if (!form) return;
    if (!form.en_name.trim() || !form.ru_name.trim()) {
      toast.error('Заполните оба названия');
      return;
    }
    setSaving(true);
    try {
      if (form.id) {
        await settingsApi.updateColumnDictItem(form.id, form);
        toast.success('Запись обновлена');
      } else {
        await settingsApi.addColumnDictItem(form);
        toast.success('Запись добавлена');
      }
      setForm(null);
      fetchItems();
      fetchCategories();
      reloadColumnLabels();
    } catch (e) {
      console.error(e);
      toast.error('Не удалось сохранить запись');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (item: ColumnDictItem) => {
    if (!item.id) return;
    const ok = await confirm(`Перевод «${item.en_name} → ${item.ru_name}» будет удалён из словаря.`, 'Удалить запись?', { confirmLabel: 'Удалить' });
    if (!ok) return;
    try {
      await settingsApi.deleteColumnDictItem(item.id);
      toast.success('Запись удалена');
      fetchItems();
      reloadColumnLabels();
    } catch (e) {
      console.error(e);
      toast.error('Не удалось удалить запись');
    }
  };

  const handleRestore = async () => {
    const ok = await confirm('Все текущие записи, включая добавленные вручную, будут заменены стандартным словарём.', 'Восстановить стандартный словарь?', {
      confirmLabel: 'Восстановить',
    });
    if (!ok) return;
    try {
      const result = await settingsApi.restoreColumnDictDefaults();
      toast.success(`Восстановлено: ${formatCount(result.count, ENTRIES)}`);
      fetchItems();
      reloadColumnLabels();
      fetchCategories();
    } catch (e) {
      console.error(e);
      toast.error('Не удалось восстановить словарь');
    }
  };

  return (
    <Page className="dict">
      <PageHeader
        title="Словарь колонок"
        description="Как ассистент понимает английские названия колонок, когда вы спрашиваете по-русски"
        actions={
          <Button variant="primary" icon={<Plus />} onClick={() => setForm({ ...EMPTY })}>
            Добавить
          </Button>
        }
      />

      <Panel padding="none" className="dict__panel">
        <Toolbar className="dict__toolbar">
          <SearchInput value={search} onChange={setSearch} placeholder="Поиск по названию" className="dict__search" />
          <Select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Категория">
            <option value="">Все категории</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {label(c)}
              </option>
            ))}
          </Select>
          <ToolbarSpacer />
          {!loading && <span className="dict__count num">{formatCount(items.length, ENTRIES)}</span>}
          <Menu
            align="end"
            width={260}
            items={[{ key: 'restore', label: 'Восстановить стандартный словарь', icon: <RotateCcw />, onSelect: handleRestore }]}
            trigger={(props) => <IconButton {...props} label="Ещё действия" icon={<MoreHorizontal />} />}
          />
        </Toolbar>

        {error ? (
          <ErrorState onRetry={fetchItems} />
        ) : loading && items.length === 0 ? (
          <SkeletonLines className="dict__skeleton" widths={Array.from({ length: 8 }, (_, i) => `${80 - (i % 3) * 12}%`)} />
        ) : items.length === 0 ? (
          <EmptyState
            compact
            icon={<BookOpen />}
            title={debounced || category ? 'Ничего не найдено' : 'Словарь пуст'}
            description={debounced || category ? 'Измените запрос или категорию.' : 'Добавьте перевод или восстановите стандартный словарь.'}
          />
        ) : (
          <>
            <Table stickyHeader wrapClassName="dict__table-wrap" loading={loading}>
              <thead>
                <tr>
                  <th>Колонка</th>
                  <th>По-русски</th>
                  <th className="dict__col-category">Категория</th>
                  <th className="actions">
                    <span className="visually-hidden">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {pageItems.map((item) => (
                  <tr key={item.id ?? item.en_name}>
                    <td className="mono">{item.en_name}</td>
                    <td>{item.ru_name}</td>
                    <td className="dict__col-category">
                      <Badge>{label(item.category)}</Badge>
                    </td>
                    <td className="actions">
                      <div>
                        <IconButton size="sm" label="Изменить" icon={<Pencil />} onClick={() => setForm({ ...item })} />
                        <IconButton size="sm" variant="danger" label="Удалить" icon={<Trash2 />} onClick={() => handleDelete(item)} />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
            {items.length > PAGE_SIZE && <Pagination page={page} pageCount={pageCount} onPageChange={setPage} total={items.length} pageSize={PAGE_SIZE} />}
          </>
        )}
      </Panel>

      <Modal
        open={form !== null}
        onClose={() => setForm(null)}
        title={form?.id ? 'Изменить запись' : 'Новая запись'}
        description="Ассистент будет понимать, что колонка с этим названием означает русский термин"
        size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={() => setForm(null)}>
              Отмена
            </Button>
            <Button variant="primary" onClick={submit} loading={saving}>
              {form?.id ? 'Сохранить' : 'Добавить'}
            </Button>
          </>
        }
      >
        {form && (
          <>
            <Field label="Название колонки" hint="Как в таблице, например customer_segment">
              {(id, describedBy) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  className="ui-input--mono"
                  value={form.en_name}
                  autoCapitalize="none"
                  spellCheck={false}
                  onChange={(e) => setForm({ ...form, en_name: e.target.value })}
                />
              )}
            </Field>
            <Field label="По-русски" hint="Смысл колонки: «выручка», «дата продажи», «город» — по нему ассистент определяет, что в ней лежит">
              {(id, describedBy) => (
                <Input id={id} aria-describedby={describedBy} value={form.ru_name} onChange={(e) => setForm({ ...form, ru_name: e.target.value })} />
              )}
            </Field>
            <Field label="Категория">
              {(id) => (
                <Select id={id} value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                  {categoryOptions.map((c) => (
                    <option key={c} value={c}>
                      {label(c)}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </>
        )}
      </Modal>
    </Page>
  );
}
