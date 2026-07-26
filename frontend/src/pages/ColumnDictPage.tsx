import { useState, useEffect, useCallback } from "react";
import { Plus, Trash2, Edit2, RotateCcw, Search, X, ChevronLeft, ChevronRight } from "lucide-react";
import { settingsApi } from "../services/api";
import type { ColumnDictItem } from "../services/api";
import toast from "react-hot-toast";
import "./ColumnDictPage.css";

const CATEGORY_LABELS: Record<string, string> = {
  dates: "Даты",
  finance: "Финансы",
  quantity: "Количества",
  identifiers: "Идентификаторы",
  text: "Текст",
  classification: "Классификация",
  sales: "Продажи",
  marketing: "Маркетинг",
  logistics: "Логистика",
  customers: "Клиенты",
  other: "Прочее",
};

const PAGE_SIZE = 50;

export default function ColumnDictPage() {
  const [items, setItems] = useState<ColumnDictItem[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [editingItem, setEditingItem] = useState<ColumnDictItem | null>(null);
  const [newItem, setNewItem] = useState<ColumnDictItem>({
    en_name: "",
    ru_name: "",
    category: "other",
  });
  const [showAddForm, setShowAddForm] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);

  const fetchItems = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await settingsApi.getColumnDictionary(
        selectedCategory || undefined,
        searchQuery || undefined
      );
      setItems(data.items);
      setCurrentPage(1);
    } catch (e) {
      console.error("Failed to fetch column dictionary:", e);
      toast.error("Ошибка загрузки словаря");
    } finally {
      setIsLoading(false);
    }
  }, [selectedCategory, searchQuery]);

  const fetchCategories = useCallback(async () => {
    try {
      const data = await settingsApi.getColumnDictCategories();
      setCategories(data.categories);
    } catch (e) {
      console.error("Failed to fetch categories:", e);
    }
  }, []);

  useEffect(() => {
    fetchCategories();
  }, [fetchCategories]);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  const totalPages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const paginatedItems = items.slice(
    (currentPage - 1) * PAGE_SIZE,
    currentPage * PAGE_SIZE
  );

  const handleAdd = async () => {
    if (!newItem.en_name.trim() || !newItem.ru_name.trim()) {
      toast.error("Заполните оба поля");
      return;
    }
    setIsSaving(true);
    try {
      await settingsApi.addColumnDictItem(newItem);
      toast.success("Запись добавлена");
      setNewItem({ en_name: "", ru_name: "", category: "other" });
      setShowAddForm(false);
      fetchItems();
      fetchCategories();
    } catch (e) {
      toast.error("Ошибка при добавлении");
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancelAdd = () => {
    setNewItem({ en_name: "", ru_name: "", category: "other" });
    setShowAddForm(false);
  };

  const handleUpdate = async () => {
    if (!editingItem || !editingItem.id) return;
    if (!editingItem.en_name.trim() || !editingItem.ru_name.trim()) {
      toast.error("Заполните оба поля");
      return;
    }
    setIsSaving(true);
    try {
      await settingsApi.updateColumnDictItem(editingItem.id, editingItem);
      toast.success("Запись обновлена");
      setEditingItem(null);
      fetchItems();
      fetchCategories();
    } catch (e) {
      toast.error("Ошибка при обновлении");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Удалить эту запись?")) return;
    try {
      await settingsApi.deleteColumnDictItem(id);
      toast.success("Запись удалена");
      fetchItems();
    } catch (e) {
      toast.error("Ошибка при удалении");
    }
  };

  const handleRestoreDefaults = async () => {
    if (
      !confirm(
        "Восстановить словарь по умолчанию? Все текущие записи будут заменены."
      )
    )
      return;
    setIsSaving(true);
    try {
      const result = await settingsApi.restoreColumnDictDefaults();
      toast.success(`Восстановлено ${result.count} записей`);
      fetchItems();
      fetchCategories();
    } catch (e) {
      toast.error("Ошибка при восстановлении");
    } finally {
      setIsSaving(false);
    }
  };

  const handleSearch = (value: string) => {
    setSearchQuery(value);
  };

  return (
    <div className="scrollable-page">
      <div className="settings-page page-container">
        <header className="page-header">
          <h1>Словарь</h1>
          <p>
            Сопоставления английских и русских названий колонок для баз данных
            маркетинга, продаж и финансов
          </p>
        </header>

        <div className="dict-toolbar">
          <div className="dict-filters">
            <div className="dict-search">
              <Search size={16} />
              <input
                type="text"
                placeholder="Поиск..."
                value={searchQuery}
                onChange={(e) => handleSearch(e.target.value)}
              />
              {searchQuery && (
                <button
                  className="dict-search-clear"
                  onClick={() => setSearchQuery("")}
                >
                  <X size={14} />
                </button>
              )}
            </div>
            <select
              value={selectedCategory}
              onChange={(e) => setSelectedCategory(e.target.value)}
              className="settings-select"
            >
              <option value="">Все категории</option>
              {categories.map((cat) => (
                <option key={cat} value={cat}>
                  {CATEGORY_LABELS[cat] || cat}
                </option>
              ))}
            </select>
          </div>
          <div className="dict-actions">
            <button
              className="btn btn--secondary"
              onClick={() => setShowAddForm(!showAddForm)}
            >
              <Plus size={16} />
              Добавить
            </button>
            <button
              className="btn btn--secondary"
              onClick={handleRestoreDefaults}
              disabled={isSaving}
            >
              <RotateCcw size={16} />
              Восстановить умолчания
            </button>
          </div>
        </div>

        {showAddForm && (
          <div className="dict-add-form">
            <h3>Новая запись</h3>
            <div className="dict-form-row">
              <input
                type="text"
                placeholder="English name"
                value={newItem.en_name}
                onChange={(e) =>
                  setNewItem({ ...newItem, en_name: e.target.value })
                }
              />
              <input
                type="text"
                placeholder="Русское название"
                value={newItem.ru_name}
                onChange={(e) =>
                  setNewItem({ ...newItem, ru_name: e.target.value })
                }
              />
              <select
                value={newItem.category}
                onChange={(e) =>
                  setNewItem({ ...newItem, category: e.target.value })
                }
              >
                {Object.entries(CATEGORY_LABELS).map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
              <div className="dict-form-buttons">
                <button
                  className="btn btn--primary"
                  onClick={handleAdd}
                  disabled={isSaving}
                >
                  Добавить
                </button>
                <button
                  className="btn btn--tertiary"
                  onClick={handleCancelAdd}
                >
                  Отмена
                </button>
              </div>
            </div>
          </div>
        )}

        <div className="dict-table-wrapper">
          {isLoading ? (
            <div className="settings-loading">
              <div className="spinner"></div>
            </div>
          ) : (
            <table className="dict-table">
              <thead>
                <tr>
                  <th>English</th>
                  <th>Русский</th>
                  <th>Категория</th>
                  <th>Действия</th>
                </tr>
              </thead>
              <tbody>
                {paginatedItems.map((item) => (
                  <tr key={item.id}>
                    {editingItem?.id === item.id ? (
                      <>
                        <td>
                          <input
                            type="text"
                            value={editingItem?.en_name ?? ''}
                            onChange={(e) =>
                              setEditingItem(editingItem
                                ? { ...(editingItem as NonNullable<typeof editingItem>), en_name: e.target.value }
                                : null
                              )
                            }
                            className="dict-edit-input"
                          />
                        </td>
                        <td>
                          <input
                            type="text"
                            value={editingItem?.ru_name ?? ''}
                            onChange={(e) =>
                              setEditingItem(editingItem
                                ? { ...(editingItem as NonNullable<typeof editingItem>), ru_name: e.target.value }
                                : null
                              )
                            }
                            className="dict-edit-input"
                          />
                        </td>
                        <td>
                          <select
                            value={editingItem?.category ?? ''}
                            onChange={(e) =>
                              setEditingItem(editingItem
                                ? { ...(editingItem as NonNullable<typeof editingItem>), category: e.target.value }
                                : null
                              )
                            }
                            className="dict-edit-select"
                          >
                            {Object.entries(CATEGORY_LABELS).map(
                              ([id, label]) => (
                                <option key={id} value={id}>
                                  {label}
                                </option>
                              )
                            )}
                          </select>
                        </td>
                        <td>
                          <div className="dict-row-actions">
                            <button
                              className="btn btn--sm btn--primary"
                              onClick={handleUpdate}
                              disabled={isSaving}
                            >
                              Сохранить
                            </button>
                            <button
                              className="btn btn--sm btn--tertiary"
                              onClick={() => setEditingItem(null)}
                            >
                              Отмена
                            </button>
                          </div>
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="dict-en">{item.en_name}</td>
                        <td className="dict-ru">{item.ru_name}</td>
                        <td>
                          <span className="dict-category-badge">
                            {CATEGORY_LABELS[item.category] || item.category}
                          </span>
                        </td>
                        <td>
                          <div className="dict-row-actions">
                            <button
                              className="btn btn--icon btn--tertiary"
                              onClick={() => setEditingItem({ ...item })}
                              title="Редактировать"
                            >
                              <Edit2 size={14} />
                            </button>
                            <button
                              className="btn btn--icon btn--alert"
                              onClick={() => item.id && handleDelete(item.id)}
                              title="Удалить"
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </td>
                      </>
                    )}
                  </tr>
                ))}
                {paginatedItems.length === 0 && (
                  <tr>
                    <td colSpan={4} className="dict-empty">
                      Нет записей
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>

        <div className="dict-footer">
          <span className="dict-count">
            Всего: {items.length} записей
            {totalPages > 1 && ` · Стр. ${currentPage} из ${totalPages}`}
          </span>
          {totalPages > 1 && (
            <div className="dict-pagination">
              <button
                className="btn btn--icon btn--tertiary"
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                disabled={currentPage === 1}
                title="Предыдущая"
              >
                <ChevronLeft size={16} />
              </button>
              <span className="dict-page-info">
                {currentPage} / {totalPages}
              </span>
              <button
                className="btn btn--icon btn--tertiary"
                onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                disabled={currentPage === totalPages}
                title="Следующая"
              >
                <ChevronRight size={16} />
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
