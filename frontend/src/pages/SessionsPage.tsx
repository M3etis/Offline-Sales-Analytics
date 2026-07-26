import React, { useEffect, useState, useRef } from "react";
import { ChevronLeft, ChevronRight, Trash2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { sessionsApi } from "../services/api";
import type { ChatSession } from "../services/api";
import { useDataset } from "../context/DatasetContext";
import { useConfirm } from "../context/ConfirmContext";
import { Tooltip } from "react-tooltip";
import "react-tooltip/dist/react-tooltip.css";
import DatePicker, { registerLocale } from "react-datepicker";
import { ru } from "date-fns/locale/ru";
import "react-datepicker/dist/react-datepicker.css";
import "./SessionsPage.css";
import "../components/DataTable.css";

registerLocale("ru", ru);

const TruncatedTooltipText = ({ text }: { text: string }) => {
  const textRef = useRef<HTMLSpanElement>(null);
  const [isTruncated, setIsTruncated] = useState(false);

  useEffect(() => {
    const checkTruncation = () => {
      if (textRef.current) {
        setIsTruncated(
          textRef.current.scrollWidth > textRef.current.clientWidth,
        );
      }
    };
    checkTruncation();
    window.addEventListener("resize", checkTruncation);
    return () => window.removeEventListener("resize", checkTruncation);
  }, [text]);

  return (
    <div
      data-tooltip-id={isTruncated ? "sessions-tooltip" : undefined}
      data-tooltip-content={isTruncated ? text : undefined}
      style={{ display: "block", overflow: "hidden" }}
    >
      <span
        ref={textRef}
        className="session-title"
        style={{
          fontWeight: 500,
          display: "block",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          maxWidth: "40vw",
        }}
      >
        {text}
      </span>
    </div>
  );
};

const SessionsPage: React.FC = () => {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();
  const confirm = useConfirm();
  const { selectedDatasetId } = useDataset();

  const [fromDate, setFromDate] = useState<Date | null>(null);
  const [toDate, setToDate] = useState<Date | null>(null);
  const [isFromOpen, setIsFromOpen] = useState(false);
  const [isToOpen, setIsToOpen] = useState(false);

  const [page, setPage] = useState(1);
  const [pageInput, setPageInput] = useState("1");
  const [pageSize, setPageSize] = useState(20);
  const [totalPages, setTotalPages] = useState(1);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    setPageInput(page.toString());
  }, [page]);

  useEffect(() => {
    loadSessions();
  }, [fromDate, toDate, selectedDatasetId, page, pageSize]);

  const loadSessions = async () => {
    try {
      setLoading(true);
      const fromStr = fromDate ? fromDate.toISOString().split("T")[0] : "";
      const toStr = toDate ? toDate.toISOString().split("T")[0] : "";
      const data = await sessionsApi.getSessions(
        fromStr,
        toStr,
        selectedDatasetId || null,
        page,
        pageSize,
      );
      setSessions(data.sessions);
      setTotal(data.total);
      setTotalPages(data.total_pages);
    } catch (error) {
      console.error("Failed to load sessions", error);
    } finally {
      setLoading(false);
    }
  };

  const handleClearAllMy = async () => {
    const isConfirmed = await confirm(
      "Вы уверены, что хотите удалить ВСЕ свои сессии?\n\nЭто действие необратимо. Все ваши чаты и история будут удалены.",
      "Удаление всех сессий",
      20,
    );
    if (!isConfirmed) return;
    try {
      await sessionsApi.clearMySessions();
      setSessions([]);
      setTotal(0);
      setTotalPages(1);
      setPage(1);
    } catch (error) {
      console.error("Failed to clear sessions", error);
    }
  };

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    const isConfirmed = await confirm(
      "Вы уверены, что хотите удалить эту сессию?",
      "Удаление сессии",
      20,
    );
    if (isConfirmed) {
      try {
        await sessionsApi.deleteSession(id);
        loadSessions();
      } catch (error) {
        console.error("Failed to delete session", error);
      }
    }
  };

  const handleOpenSession = (id: string) => {
    navigate(`/analyst?session_id=${id}`);
  };

  const handleNewSession = () => {
    navigate("/analyst");
  };

  return (
    <div className="scrollable-page animate-fade-in">
      <div className="sessions-page page-container">
        <header className="page-header">
          <h1>Менеджер сессий</h1>
          <p>
            Здесь хранится история ваших диалогов с ИИ-аналитиком. Вы можете
            вернуться к любому обсуждению и продолжить работу с сохраненными
            данными.
          </p>
        </header>

        <div className="data-table-container card">
          <div
            className="table-controls"
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "1rem",
              alignItems: "flex-end",
              justifyContent: "space-between",
            }}
          >
            <div
              className="sessions-filters"
              style={{
                display: "flex",
                gap: "1.5rem",
                alignItems: "flex-end",
                margin: 0,
                padding: 0,
                border: "none",
                background: "transparent",
              }}
            >
              <div className="sessions-filter-group">
                <label>Дата с:</label>
                <div className="date-input-wrapper">
                  <DatePicker
                    selected={fromDate}
                    onChange={(date: Date | null) => {
                      setFromDate(date);
                      setIsFromOpen(false);
                    }}
                    onInputClick={() => setIsFromOpen(!isFromOpen)}
                    onClickOutside={() => setIsFromOpen(false)}
                    open={isFromOpen}
                    isClearable
                    dateFormat="dd.MM.yyyy"
                    placeholderText="дд.мм.гггг"
                    locale="ru"
                    className="react-datepicker-input"
                  />
                  <svg
                    className="calendar-icon"
                    onMouseDown={(e) => e.stopPropagation()}
                    onClick={(e) => {
                      e.stopPropagation();
                      setIsFromOpen((prev) => !prev);
                    }}
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <rect
                      x="3"
                      y="4"
                      width="18"
                      height="18"
                      rx="2"
                      ry="2"
                    ></rect>
                    <line x1="16" y1="2" x2="16" y2="6"></line>
                    <line x1="8" y1="2" x2="8" y2="6"></line>
                    <line x1="3" y1="10" x2="21" y2="10"></line>
                  </svg>
                </div>
              </div>
              <div className="sessions-filter-group">
                <label>Дата по:</label>
                <div className="date-input-wrapper">
                  <DatePicker
                    selected={toDate}
                    onChange={(date: Date | null) => {
                      setToDate(date);
                      setIsToOpen(false);
                    }}
                    onInputClick={() => setIsToOpen(!isToOpen)}
                    onClickOutside={() => setIsToOpen(false)}
                    open={isToOpen}
                    isClearable
                    dateFormat="dd.MM.yyyy"
                    placeholderText="дд.мм.гггг"
                    locale="ru"
                    className="react-datepicker-input"
                  />
                  <svg
                    className="calendar-icon"
                    onMouseDown={(e) => e.stopPropagation()}
                    onClick={(e) => {
                      e.stopPropagation();
                      setIsToOpen((prev) => !prev);
                    }}
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <rect
                      x="3"
                      y="4"
                      width="18"
                      height="18"
                      rx="2"
                      ry="2"
                    ></rect>
                    <line x1="16" y1="2" x2="16" y2="6"></line>
                    <line x1="8" y1="2" x2="8" y2="6"></line>
                    <line x1="3" y1="10" x2="21" y2="10"></line>
                  </svg>
                </div>
              </div>
            </div>
            {total > 0 && (
              <button
                className="btn btn--danger-outline"
                onClick={handleClearAllMy}
              >
                <Trash2 size={14} />
                Удалить все
              </button>
            )}
          </div>

          <div
            className="sessions-content"
            style={{
              position: "relative",
              minHeight: loading ? "200px" : "auto",
            }}
          >
            {loading ? (
              <div className="table-loading">
                <div className="spinner"></div>
              </div>
            ) : total === 0 ? (
              <div className="sessions-empty" style={{ padding: "3rem 1rem" }}>
                <p>
                  {fromDate || toDate
                    ? "За выбранный диапазон сессий не найдено."
                    : "У вас пока нет сохраненных сессий."}
                </p>
                <button className="btn btn--primary" onClick={handleNewSession}>
                  Начать общение с аналитиком
                </button>
              </div>
            ) : (
              <>
                <div className="table-wrapper">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>
                          <div className="th-content">Название</div>
                        </th>
                        <th>
                          <div className="th-content">Дата запроса</div>
                        </th>
                        <th style={{ width: "60px", textAlign: "center" }}></th>
                      </tr>
                    </thead>
                    <tbody>
                      {sessions.map((session) => {
                        const hasError = session.status === 'error' || session.status === 'no_response';
                        const rowStyle = hasError
                          ? { cursor: "pointer", color: "#d32f2f" }
                          : { cursor: "pointer" };
                        return (
                        <tr
                          key={session.id}
                          className="animate-fade-in"
                          onClick={() => handleOpenSession(session.id)}
                          style={rowStyle}
                        >
                          <td>
                            <TruncatedTooltipText text={session.title} />
                          </td>
                          <td style={{ color: hasError ? "#d32f2f" : "var(--text-secondary)" }}>
                            {new Date(session.updated_at).toLocaleString(
                              "ru-RU",
                              {
                                day: "numeric",
                                month: "long",
                                hour: "2-digit",
                                minute: "2-digit",
                              },
                            )}
                          </td>
                          <td style={{ textAlign: "center" }}>
                            <button
                              className="btn btn--icon btn--alert session-delete-btn"
                              onClick={(e) => handleDelete(e, session.id)}
                              data-tooltip-id="sessions-tooltip"
                              data-tooltip-content="Удалить сессию"
                              data-tooltip-place="left"
                              style={{ padding: "4px" }}
                            >
                              <svg
                                width="16"
                                height="16"
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                              >
                                <path d="M3 6h18"></path>
                                <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path>
                                <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path>
                              </svg>
                            </button>
                          </td>
                        </tr>
                        );
                      })}
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
                      <ChevronLeft
                        size={18}
                        style={{ pointerEvents: "none" }}
                      />
                    </button>
                    <span className="page-info">
                      Страница
                      <input
                        type="number"
                        className="page-input"
                        value={pageInput}
                        min={1}
                        max={totalPages}
                        onChange={(e) => setPageInput(e.target.value)}
                        onBlur={() => {
                          const p = parseInt(pageInput);
                          if (!isNaN(p) && p >= 1 && p <= totalPages) {
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
                      из {totalPages}
                    </span>
                    <button
                      type="button"
                      className="btn btn--tertiary btn--icon"
                      disabled={page >= totalPages}
                      onClick={() => setPage((p) => p + 1)}
                    >
                      <ChevronRight
                        size={18}
                        style={{ pointerEvents: "none" }}
                      />
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
        <Tooltip id="sessions-tooltip" place="top" delayShow={200} />
      </div>
    </div>
  );
};

export default SessionsPage;
