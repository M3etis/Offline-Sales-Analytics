import { useState, useRef, useEffect } from "react";
import {
  LogIn,
  LogOut,
  Sun,
  Moon,
  Menu,
  Bot,
  Activity,
  Database,
  Wrench,
  Trash2,
  Users,
  BarChart3,
  ChevronDown,
  BookOpen,
} from "lucide-react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import "./Header.css";

interface HeaderProps {
  isSidebarCollapsed?: boolean;
  setIsSidebarCollapsed?: (v: boolean) => void;
}

const SETTINGS_SECTIONS = [
  {
    permission: "settings_status",
    label: "Статус служб",
    path: "/settings/status",
    icon: Activity,
  },
  {
    permission: "settings_data",
    label: "Загрузка данных",
    path: "/settings/data",
    icon: Database,
  },
  {
    permission: "settings_cleanup",
    label: "Очистка данных",
    path: "/settings/cleanup",
    icon: Trash2,
  },
  {
    permission: "settings_assistant",
    label: "Настройки ассистента",
    path: "/settings/assistant",
    icon: Wrench,
  },
  {
    permission: "settings_assistant",
    label: "Оценки ассистента",
    path: "/settings/feedback",
    icon: BarChart3,
  },
  {
    permission: "settings_assistant",
    label: "Словарь",
    path: "/settings/column-dictionary",
    icon: BookOpen,
  },
  {
    permission: "admin",
    label: "Пользователи",
    path: "/settings/users",
    icon: Users,
  },
];

export default function Header({
  isSidebarCollapsed,
  setIsSidebarCollapsed,
}: HeaderProps) {
  const {
    isAdmin,
    isAuthenticated,
    logout,
    hasPermission,
    username,
    fullName,
    role,
  } = useAuth();
  const [theme, setTheme] = useState(localStorage.getItem("theme") || "dark");
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const toggleTheme = () => {
    const newTheme = theme === "dark" ? "light" : "dark";
    setTheme(newTheme);
    document.documentElement.setAttribute("data-theme", newTheme);
    localStorage.setItem("theme", newTheme);
  };

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handleLogout = () => {
    setMenuOpen(false);
    logout();
    navigate("/login");
  };

  const visibleSections = SETTINGS_SECTIONS.filter((s) =>
    s.permission === "admin" ? isAdmin : hasPermission(s.permission),
  );

  const displayName = fullName || username || "?";
  const avatarLetter = displayName[0].toUpperCase();

  return (
    <header className="header">
      <div className="header__left">
        {setIsSidebarCollapsed && (
          <button
            className="header__toggle-btn"
            onClick={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
            title="Скрыть/показать меню"
          >
            <Menu size={20} />
          </button>
        )}
        <NavLink to="/dashboard" className="header__brand">
          <div className="header__logo-icon">
            <Bot size={16} color="white" />
          </div>
          <h2 className="header__title">AI Аналитика</h2>
        </NavLink>
      </div>

      <div className="header__right">
        <button
          className="header__btn"
          onClick={toggleTheme}
          title="Переключить тему"
        >
          {theme === "dark" ? <Sun size={20} /> : <Moon size={20} />}
          <span>{theme === "dark" ? "Светлая" : "Темная"} тема</span>
        </button>

        {/* User dropdown */}
        <div className="header__user-menu" ref={menuRef}>
          {isAuthenticated ? (
            <>
              <button
                className="header__user-trigger"
                onClick={() => setMenuOpen((o) => !o)}
                aria-expanded={menuOpen}
              >
                <div className="header__avatar">{avatarLetter}</div>
                <span className="header__username">{displayName}</span>
                <ChevronDown
                  size={14}
                  className={`header__chevron ${menuOpen ? "open" : ""}`}
                />
              </button>

              {menuOpen && (
                <div className="header__dropdown">
                  {/* User info header */}
                  <div className="header__dropdown-header">
                    <div className="header__avatar header__avatar--lg">
                      {avatarLetter}
                    </div>
                    <div className="header__dropdown-user-info">
                      <span className="header__dropdown-name">
                        {displayName}
                      </span>
                      <span className="header__dropdown-role">
                        {role === "admin" ? "Администратор" : "Пользователь"}
                      </span>
                    </div>
                  </div>

                  {visibleSections.length > 0 && (
                    <>
                      <div className="header__dropdown-divider" />
                      <p className="header__dropdown-section-label">
                        Настройки
                      </p>
                      {visibleSections.map((s) => {
                        const Icon = s.icon;
                        return (
                          <NavLink
                            key={s.path}
                            to={s.path}
                            className="header__dropdown-item"
                            onClick={() => setMenuOpen(false)}
                          >
                            <Icon size={15} />
                            {s.label}
                          </NavLink>
                        );
                      })}
                    </>
                  )}

                  <div className="header__dropdown-divider" />
                  <button
                    className="header__dropdown-item header__dropdown-item--danger"
                    onClick={handleLogout}
                  >
                    <LogOut size={15} />
                    Выйти
                  </button>
                </div>
              )}
            </>
          ) : (
            <NavLink to="/login" className="header__btn">
              <LogIn size={20} />
              <span>Войти</span>
            </NavLink>
          )}
        </div>
      </div>
    </header>
  );
}
