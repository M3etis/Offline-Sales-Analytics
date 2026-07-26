import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  ThumbsUp,
  ThumbsDown,
  BarChart3,
  TrendingUp,
  MessageSquare,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { sessionsApi, dataApi } from "../services/api";
import "./FeedbackStatsPage.css";
import "./SettingsPage.css";

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
}

interface DropdownItem {
  id: string;
  name: string;
  isGroup: boolean;
}

const PAGE_SIZE = 10;

export default function FeedbackStatsPage() {
  const [stats, setStats] = useState<FeedbackStats>({
    positive: 0,
    negative: 0,
    total: 0,
  });
  const [items, setItems] = useState<DropdownItem[]>([]);
  const [selectedDataset, setSelectedDataset] = useState<string>("");
  const [messages, setMessages] = useState<FeedbackMessage[]>([]);
  const [filter, setFilter] = useState<string>("");
  const [page, setPage] = useState(1);
  const navigate = useNavigate();

  useEffect(() => {
    loadData();
  }, []);

  useEffect(() => {
    loadStats();
    loadMessages();
    setPage(1);
  }, [selectedDataset, filter]);

  const loadData = async () => {
    try {
      const groups = await dataApi.getGroups();
      const dropdownItems: DropdownItem[] = [];
      for (const g of groups) {
        if (g.tables.length === 1 && !g.relationships?.length) {
          dropdownItems.push({
            id: g.tables[0].id,
            name: g.source_file,
            isGroup: false,
          });
        } else {
          dropdownItems.push({
            id: g.group_id,
            name: g.source_file,
            isGroup: true,
          });
        }
      }
      setItems(dropdownItems);
    } catch (e) {
      console.error(e);
    }
  };

  const loadStats = async () => {
    try {
      const data = await sessionsApi.getFeedbackStats(
        selectedDataset || undefined,
      );
      setStats(data);
    } catch (e) {
      console.error(e);
    }
  };

  const loadMessages = async () => {
    try {
      const data = await sessionsApi.getFeedbackMessages(
        selectedDataset || undefined,
        filter || undefined,
      );
      setMessages(data);
    } catch (e) {
      console.error(e);
    }
  };

  const handleNavigateToSession = (sessionId: string) => {
    navigate(`/analyst?session_id=${sessionId}`);
  };

  const positivePercent =
    stats.total > 0 ? Math.round((stats.positive / stats.total) * 100) : 0;
  const negativePercent =
    stats.total > 0 ? Math.round((stats.negative / stats.total) * 100) : 0;

  const totalPages = Math.ceil(messages.length / PAGE_SIZE);
  const paginatedMessages = messages.slice(
    (page - 1) * PAGE_SIZE,
    page * PAGE_SIZE,
  );
  const showPagination = messages.length > PAGE_SIZE;

  return (
    <div className="scrollable-page animate-fade-in">
      <div className="page-container">
        <header className="page-header">
          <h1>Статистика обратной связи</h1>
          <p className="text-muted">
            Ассистент учится на ваших оценках. Положительные оценки помогают
            системе запоминать успешные запросы и использовать их как примеры
            для будущих ответов.
            <br /> Чем больше оценок — тем точнее ответы.
          </p>
        </header>

        <div className="settings-section">
          <h2>Фильтр по базе данных</h2>
          <select
            value={selectedDataset}
            onChange={(e) => setSelectedDataset(e.target.value)}
            className="settings-select"
          >
            <option value="">Все базы данных</option>
            {items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.isGroup ? `${item.name} (группа)` : item.name}
              </option>
            ))}
          </select>
        </div>

        <div className="stats-grid">
          <div className="stats-card">
            <div className="stats-card__icon stats-card__icon--total">
              <BarChart3 size={24} />
            </div>
            <div className="stats-card__info">
              <span className="stats-card__value">{stats.total}</span>
              <span className="stats-card__label">Всего оценок</span>
            </div>
          </div>

          <div className="stats-card">
            <div className="stats-card__icon stats-card__icon--positive">
              <ThumbsUp size={24} />
            </div>
            <div className="stats-card__info">
              <span className="stats-card__value">{stats.positive}</span>
              <span className="stats-card__label">
                Положительных ({positivePercent}%)
              </span>
            </div>
          </div>

          <div className="stats-card">
            <div className="stats-card__icon stats-card__icon--negative">
              <ThumbsDown size={24} />
            </div>
            <div className="stats-card__info">
              <span className="stats-card__value">{stats.negative}</span>
              <span className="stats-card__label">
                Отрицательных ({negativePercent}%)
              </span>
            </div>
          </div>

          <div className="stats-card">
            <div className="stats-card__icon stats-card__icon--rate">
              <TrendingUp size={24} />
            </div>
            <div className="stats-card__info">
              <span className="stats-card__value">{positivePercent}%</span>
              <span className="stats-card__label">Процент одобрения</span>
            </div>
          </div>
        </div>

        <div className="settings-section">
          <h2>Процент одобрения</h2>
          <div className="stats-bar">
            <div
              className="stats-bar__positive"
              style={{ width: `${positivePercent}%` }}
            >
              {positivePercent > 10 && `${positivePercent}%`}
            </div>
            <div
              className="stats-bar__negative"
              style={{ width: `${negativePercent}%` }}
            >
              {negativePercent > 10 && `${negativePercent}%`}
            </div>
          </div>
          <div className="stats-bar__legend">
            <span>
              <ThumbsUp size={14} /> Положительных: {stats.positive}
            </span>
            <span>
              <ThumbsDown size={14} /> Отрицательных: {stats.negative}
            </span>
          </div>
        </div>

        <div className="settings-section">
          <div className="rated-header">
            <h2>Оцененные вопросы</h2>
            <div className="rated-filters">
              <button
                className={`btn btn--small ${filter === "" ? "btn--primary" : "btn--secondary"}`}
                onClick={() => setFilter("")}
              >
                Все
              </button>
              <button
                className={`btn btn--small ${filter === "positive" ? "btn--primary" : "btn--secondary"}`}
                onClick={() =>
                  setFilter(filter === "positive" ? "" : "positive")
                }
              >
                <ThumbsUp size={12} /> Положительные
              </button>
              <button
                className={`btn btn--small ${filter === "negative" ? "btn--primary" : "btn--secondary"}`}
                onClick={() =>
                  setFilter(filter === "negative" ? "" : "negative")
                }
              >
                <ThumbsDown size={12} /> Отрицательные
              </button>
            </div>
          </div>

          {messages.length === 0 ? (
            <p className="text-muted" style={{ padding: "20px 0" }}>
              Нет оцененных вопросов
            </p>
          ) : (
            <>
              <div className="rated-list">
                {paginatedMessages.map((msg) => (
                  <div
                    key={msg.id}
                    className="rated-item"
                    onClick={() => handleNavigateToSession(msg.session_id)}
                  >
                    <div className="rated-item__feedback">
                      {msg.feedback === "positive" ? (
                        <ThumbsUp
                          size={16}
                          className="rated-item__icon--positive"
                        />
                      ) : (
                        <ThumbsDown
                          size={16}
                          className="rated-item__icon--negative"
                        />
                      )}
                    </div>
                    <div className="rated-item__content">
                      <div className="rated-item__question">
                        <MessageSquare size={14} className="text-muted" />
                        <span>{msg.question}</span>
                      </div>
                      <div className="rated-item__meta">
                        <span className="rated-item__session">
                          {msg.session_title}
                        </span>
                        <span className="rated-item__date">
                          {new Date(msg.created_at).toLocaleString("ru-RU")}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              {showPagination && (
                <div className="rated-pagination">
                  <button
                    className="btn btn--small btn--secondary"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page === 1}
                  >
                    <ChevronLeft size={14} />
                  </button>
                  <span className="rated-pagination__info">
                    {page} / {totalPages}
                  </span>
                  <button
                    className="btn btn--small btn--secondary"
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={page === totalPages}
                  >
                    <ChevronRight size={14} />
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
