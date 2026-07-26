import { useState, useEffect, useRef, useCallback } from "react";
import {
  ChevronDown,
  ChevronUp,
  ChevronLeft,
  ChevronRight,
  Search,
  X,
} from "lucide-react";
import { dataApi } from "../services/api";
import { formatDateRu } from "../utils/formatDate";
import "./DataTable.css";

interface DataTableProps {
  datasetId?: string;
}

export default function DataTable({ datasetId }: DataTableProps) {
  const [data, setData] = useState<any[]>([]);
  const [columns, setColumns] = useState<{ key: string; label: string }[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageInput, setPageInput] = useState("1");
  const [pageSize, setPageSize] = useState(20);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [sortBy, setSortBy] = useState("date");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");

  const handleSearch = useCallback((value: string) => {
    setSearchQuery(value);
    setPage(1);

    if (debounceRef.current) clearTimeout(debounceRef.current);

    debounceRef.current = setTimeout(() => {
      if (value.length === 0 || value.length >= 2) {
        setDebouncedSearch(value);
      }
    }, 300);
  }, []);

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  useEffect(() => {
    fetchData();
  }, [page, pageSize, sortBy, sortOrder, datasetId, debouncedSearch]);

  useEffect(() => {
    setPageInput(page.toString());
  }, [page]);

  const fetchData = async () => {
    setLoading(true);
    try {
      const res = await dataApi.getTable({
        page,
        page_size: pageSize,
        sort_by: sortBy,
        sort_order: sortOrder,
        dataset_id: datasetId || undefined,
        search: debouncedSearch || undefined,
      });
      setData(res.data);
      if (res.columns && res.columns.length > 0) {
        setColumns(res.columns.map((c) => ({ key: c, label: c })));
      } else if (res.data && res.data.length > 0) {
        setColumns(Object.keys(res.data[0]).map((k) => ({ key: k, label: k })));
      }
      setTotal(res.total);
      setTotalPages(res.total_pages);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  // Server-side search: data is already filtered
  const filteredData = data;

  const handleSort = (column: string) => {
    if (sortBy === column) {
      setSortOrder(sortOrder === "asc" ? "desc" : "asc");
    } else {
      setSortBy(column);
      setSortOrder("desc");
    }
    setPage(1);
  };

  return (
    <div className="data-table-container card">
      <div
        className="table-controls"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <div
          className="table-search"
          style={{ display: "flex", alignItems: "center", gap: "8px" }}
        >
          <div
            style={{
              position: "relative",
              display: "flex",
              alignItems: "center",
            }}
          >
            <Search
              size={16}
              style={{
                color: "var(--text-muted)",
                position: "absolute",
                left: "10px",
                pointerEvents: "none",
              }}
            />
            <input
              type="text"
              className="search-input"
              placeholder="Поиск..."
              value={searchQuery}
              onChange={(e) => handleSearch(e.target.value)}
              style={{
                padding: "6px 12px 6px 32px",
                borderRadius: "6px",
                border: "1px solid var(--border-light)",
                background: "var(--bg-card)",
                color: "var(--text-primary)",
                fontSize: "0.9rem",
                outline: "none",
                minWidth: "250px",
              }}
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => handleSearch("")}
                style={{
                  position: "absolute",
                  right: "6px",
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  padding: "2px",
                  color: "var(--text-muted)",
                  borderRadius: "4px",
                }}
              >
                <X size={14} />
              </button>
            )}
          </div>
        </div>
        <div className="table-stats text-muted">
          {debouncedSearch
            ? `Найдено: ${total}`
            : `Всего записей: ${total}`}
        </div>
      </div>

      <div className="table-wrapper">
        {loading && (
          <div className="table-loading">
            <div className="spinner"></div>
          </div>
        )}
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((col) => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  className="sortable"
                >
                  <div className="th-content">
                    {col.label}
                    {sortBy === col.key &&
                      (sortOrder === "asc" ? (
                        <ChevronUp size={14} />
                      ) : (
                        <ChevronDown size={14} />
                      ))}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filteredData.map((row, i) => (
              <tr key={i} className="animate-fade-in">
                {columns.map((col) => {
                  const val = row[col.key];
                  let formattedVal = val;
                  if (val === null || val === undefined) {
                    formattedVal = "-";
                  } else if (typeof val === "number") {
                    formattedVal = val.toLocaleString("ru-RU", {
                      maximumFractionDigits: 2,
                    });
                  } else if (typeof val === "string") {
                    // Try to format as date (DD.MM.YYYY)
                    const dateFormatted = formatDateRu(val);
                    if (dateFormatted) {
                      formattedVal = dateFormatted;
                    }
                  }

                  return <td key={col.key}>{formattedVal}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="pagination">
        <div className="pagination-left">
          Записей на странице:
          <select
            className="page-size-select"
            value={pageSize}
            onChange={(e) => {
              setPageSize(Number(e.target.value));
              setPage(1);
            }}
          >
            <option value={10}>10</option>
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
        <div className="pagination-controls">
          <button
            type="button"
            className="btn btn--tertiary btn--icon"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            <ChevronLeft size={18} style={{ pointerEvents: "none" }} />
          </button>
          <span className="page-info">
            Страница
            <input
              type="number"
              className="page-input"
              value={pageInput}
              min={1}
              max={totalPages || 1}
              onChange={(e) => setPageInput(e.target.value)}
              onBlur={() => {
                const p = parseInt(pageInput);
                if (!isNaN(p) && p >= 1 && p <= (totalPages || 1)) {
                  setPage(p);
                } else {
                  setPageInput(page.toString());
                }
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.currentTarget.blur();
                }
              }}
            />
            из {totalPages || 1}
          </span>
          <button
            type="button"
            className="btn btn--tertiary btn--icon"
            disabled={page >= (totalPages || 1)}
            onClick={() => setPage((p) => p + 1)}
          >
            <ChevronRight size={18} style={{ pointerEvents: "none" }} />
          </button>
        </div>
      </div>
    </div>
  );
}
