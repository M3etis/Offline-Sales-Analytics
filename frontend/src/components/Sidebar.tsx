import { NavLink, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  MessageSquare,
  Database,
  History,
  Activity,
  Wrench,
  Trash2,
  Users,
  BarChart3,
  BookOpen,
} from "lucide-react";
import { useEffect, useState } from "react";
import { dataApi } from "../services/api";
import type { Dataset } from "../services/api";
import { useAuth } from "../contexts/AuthContext";
import { useDataset } from "../context/DatasetContext";
import { Tooltip } from "react-tooltip";
import "react-tooltip/dist/react-tooltip.css";

import "./Sidebar.css";

export default function Sidebar({ isCollapsed }: { isCollapsed: boolean }) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const { isAdmin, hasPermission } = useAuth();
  const location = useLocation();
  const isSettingsRoute = location.pathname.startsWith("/settings");
  const { selectedDatasetId } = useDataset();

  useEffect(() => {
    dataApi.getDatasets().then(setDatasets).catch(console.error);
  }, [selectedDatasetId]);

  const currentDataset =
    datasets.find((d) => d.id === selectedDatasetId) || datasets[0];

  const renderMainLinks = () => (
    <>
      {hasPermission("dashboard") && (
        <div
          data-tooltip-id="sidebar-tooltip"
          data-tooltip-hidden={!isCollapsed}
          data-tooltip-content="Дашборд"
        >
          <NavLink
            to="/dashboard"
            className={({ isActive }) =>
              `sidebar__link ${isActive ? "sidebar__link--active" : ""}`
            }
          >
            <LayoutDashboard size={20} />
            <span>Дашборд</span>
          </NavLink>
        </div>
      )}

      {hasPermission("analyst") && (
        <div
          data-tooltip-id="sidebar-tooltip"
          data-tooltip-hidden={!isCollapsed}
          data-tooltip-content="Ассистент"
        >
          <NavLink
            to="/analyst"
            className={({ isActive }) =>
              `sidebar__link ${isActive ? "sidebar__link--active" : ""}`
            }
          >
            <MessageSquare size={20} />
            <span>Ассистент</span>
          </NavLink>
        </div>
      )}

      {hasPermission("sessions") && (
        <div
          data-tooltip-id="sidebar-tooltip"
          data-tooltip-hidden={!isCollapsed}
          data-tooltip-content="История"
        >
          <NavLink
            to="/sessions"
            className={({ isActive }) =>
              `sidebar__link ${isActive ? "sidebar__link--active" : ""}`
            }
          >
            <History size={20} />
            <span>История</span>
          </NavLink>
        </div>
      )}

      {hasPermission("details") && (
        <div
          data-tooltip-id="sidebar-tooltip"
          data-tooltip-hidden={!isCollapsed}
          data-tooltip-content="База данных"
        >
          <NavLink
            to="/details"
            className={({ isActive }) =>
              `sidebar__link ${isActive ? "sidebar__link--active" : ""}`
            }
          >
            <Database size={20} />
            <span>База данных</span>
          </NavLink>
        </div>
      )}
    </>
  );

  const renderSettingsLinks = () => (
    <>
      {/* 1. Статус служб */}
      {hasPermission("settings_status") && (
        <div
          data-tooltip-id="sidebar-tooltip"
          data-tooltip-hidden={!isCollapsed}
          data-tooltip-content="Статус служб"
        >
          <NavLink
            to="/settings/status"
            className={({ isActive }) =>
              `sidebar__link ${isActive ? "sidebar__link--active" : ""}`
            }
          >
            <Activity size={20} />
            <span>Статус служб</span>
          </NavLink>
        </div>
      )}

      {/* 2. Загрузка данных */}
      {hasPermission("settings_data") && (
        <div
          data-tooltip-id="sidebar-tooltip"
          data-tooltip-hidden={!isCollapsed}
          data-tooltip-content="Загрузка данных"
        >
          <NavLink
            to="/settings/data"
            className={({ isActive }) =>
              `sidebar__link ${isActive ? "sidebar__link--active" : ""}`
            }
          >
            <Database size={20} />
            <span>Загрузка данных</span>
          </NavLink>
        </div>
      )}

      {/* 3. Очистка данных */}
      {hasPermission("settings_cleanup") && (
        <div
          data-tooltip-id="sidebar-tooltip"
          data-tooltip-hidden={!isCollapsed}
          data-tooltip-content="Очистка данных"
        >
          <NavLink
            to="/settings/cleanup"
            className={({ isActive }) =>
              `sidebar__link ${isActive ? "sidebar__link--active" : ""}`
            }
          >
            <Trash2 size={20} />
            <span>Очистка данных</span>
          </NavLink>
        </div>
      )}

      {/* 4. Настройки ассистента */}
      {hasPermission("settings_assistant") && (
        <div
          data-tooltip-id="sidebar-tooltip"
          data-tooltip-hidden={!isCollapsed}
          data-tooltip-content="Настройки ассистента"
        >
          <NavLink
            to="/settings/assistant"
            className={({ isActive }) =>
              `sidebar__link ${isActive ? "sidebar__link--active" : ""}`
            }
          >
            <Wrench size={20} />
            <span>Настройки ассистента</span>
          </NavLink>
        </div>
      )}

      {/* 4. Статистика обратной связи */}
      {hasPermission("settings_assistant") && (
        <div
          data-tooltip-id="sidebar-tooltip"
          data-tooltip-hidden={!isCollapsed}
          data-tooltip-content="Статистика оценок"
        >
          <NavLink
            to="/settings/feedback"
            className={({ isActive }) =>
              `sidebar__link ${isActive ? "sidebar__link--active" : ""}`
            }
          >
            <BarChart3 size={20} />
            <span>Оценки ассистента</span>
          </NavLink>
        </div>
      )}

      {/* 5. Словарь */}
      {hasPermission("settings_assistant") && (
        <div
          data-tooltip-id="sidebar-tooltip"
          data-tooltip-hidden={!isCollapsed}
          data-tooltip-content="Словарь"
        >
          <NavLink
            to="/settings/column-dictionary"
            className={({ isActive }) =>
              `sidebar__link ${isActive ? "sidebar__link--active" : ""}`
            }
          >
            <BookOpen size={20} />
            <span>Словарь</span>
          </NavLink>
        </div>
      )}

      {/* 6. Пользователи (только admin) */}
      {isAdmin && (
        <div
          data-tooltip-id="sidebar-tooltip"
          data-tooltip-hidden={!isCollapsed}
          data-tooltip-content="Пользователи"
        >
          <NavLink
            to="/settings/users"
            className={({ isActive }) =>
              `sidebar__link ${isActive ? "sidebar__link--active" : ""}`
            }
          >
            <Users size={20} />
            <span>Пользователи</span>
          </NavLink>
        </div>
      )}
    </>
  );

  return (
    <aside className={`sidebar ${isCollapsed ? "sidebar--collapsed" : ""}`}>
      <nav className="sidebar__nav sidebar__nav--margined">
        {isSettingsRoute ? renderSettingsLinks() : renderMainLinks()}
      </nav>

      <Tooltip
        id="sidebar-tooltip"
        place="right"
        positionStrategy="fixed"
        style={{ zIndex: 9999 }}
      />

      <div className="sidebar__footer">
        <div className="sidebar__status">
          <Database size={20} className="sidebar__status-icon" />
          <div className="sidebar__status-info">
            <span
              className="sidebar__status-label"
              title={currentDataset?.source_file || currentDataset?.name || "Локальная БД"}
            >
              {currentDataset?.source_file || currentDataset?.name || "Локальная БД"}
            </span>
            <span className="sidebar__status-value">
              {currentDataset?.rows || 0} строк
            </span>
          </div>
        </div>
      </div>
    </aside>
  );
}
