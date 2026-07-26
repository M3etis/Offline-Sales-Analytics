import { useState, useEffect } from "react";
import { Trash2, AlertTriangle, CheckCircle, Users, Database } from "lucide-react";
import { settingsApi, dataApi } from "../services/api";
import type { UserOut } from "../services/api";
import { useConfirm } from "../context/ConfirmContext";
import "./CleanupPage.css";

export default function CleanupPage() {
  const [loadingAction, setLoadingAction] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<{
    type: "error" | "success";
    text: string;
  } | null>(null);
  const confirm = useConfirm();
  const [users, setUsers] = useState<UserOut[]>([]);
  const [selectedUserId, setSelectedUserId] = useState<string>("");

  useEffect(() => {
    loadUsers();
  }, []);

  const loadUsers = async () => {
    try {
      const data = await settingsApi.getUsers();
      setUsers(data);
    } catch (e) {
      console.error("Failed to load users", e);
    }
  };

  const handleClearSessions = async () => {
    const userLabel = selectedUserId || "ВСЕХ";
    const isConfirmed = await confirm(`Вы уверены, что хотите удалить все сессии ${userLabel === "ВСЕХ" ? "ВСЕХ пользователей" : `пользователя ${userLabel}`}? \n\nВ результате:\n• Все прошлые чаты и история диалогов будут удалены.\n• Кэш нейросети будет очищен.\n• Сами загруженные данные (БД) и настройки не пострадают.\n\nЭто действие необратимо.`, "Очистка сессий");
    if (!isConfirmed) {
      return;
    }
    setLoadingAction("sessions");
    setStatusMessage(null);
    try {
      await settingsApi.clearSessions(selectedUserId || undefined);
      await settingsApi.clearCache(selectedUserId || undefined);
      setStatusMessage({
        type: "success",
        text: `Сессии ${userLabel === "ВСЕХ" ? "всех пользователей" : `пользователя ${userLabel}`} и кэш нейросети успешно удалены.`,
      });
    } catch (e: any) {
      setStatusMessage({
        type: "error",
        text: "Ошибка при удалении сессий.",
      });
    } finally {
      setLoadingAction(null);
    }
  };

  const handleDeleteAllDatasets = async () => {
    const isConfirmed = await confirm("Вы уверены, что хотите удалить ВСЕ базы данных?\n\nВ результате:\n• Все загруженные файлы и таблицы будут безвозвратно удалены.\n• Настройки и история диалогов не пострадают.\n\nЭто действие необратимо.", "Удаление баз данных");
    if (!isConfirmed) return;
    setLoadingAction("datasets");
    setStatusMessage(null);
    try {
      await dataApi.deleteAllDatasets();
      setStatusMessage({
        type: "success",
        text: "Все базы данных успешно удалены.",
      });
    } catch (e: any) {
      setStatusMessage({ type: "error", text: "Ошибка при удалении баз данных." });
    } finally {
      setLoadingAction(null);
    }
  };

  const handleClearAll = async () => {
    const isConfirmed = await confirm("ВНИМАНИЕ! Вы собираетесь выполнить полный сброс системы.\n\nВ результате:\n• Будут безвозвратно удалены ВСЕ загруженные файлы баз данных.\n• Полностью очистится вся история диалогов.\n• Будет удален весь кэш нейросети.\n\nСистема вернется в свое первоначальное состояние. Вы уверены?", "Сброс всей системы");
    if (!isConfirmed) {
      return;
    }
    setLoadingAction("all");
    setStatusMessage(null);
    try {
      // dataApi is imported from api
      const { dataApi } = await import("../services/api");
      await dataApi.fullCleanup();
      setStatusMessage({
        type: "success",
        text: "Система полностью очищена. Все данные, кэш и история удалены.",
      });

      // Optionally reload the page to clear contexts
      setTimeout(() => {
        window.location.href = "/dashboard";
      }, 2000);
    } catch (e: any) {
      setStatusMessage({
        type: "error",
        text: "Ошибка при полном сбросе системы.",
      });
    } finally {
      setLoadingAction(null);
    }
  };

  return (
    <div className="scrollable-page animate-fade-in">
      <div className="page-container">
        <header className="page-header">
          <h1>Очистка данных</h1>
          <p>Управление историей диалогов и кэшем системы</p>
        </header>

        {statusMessage && (
          <div className={`cleanup-status cleanup-status--${statusMessage.type}`}>
            {statusMessage.type === "success" ? (
              <CheckCircle size={20} />
            ) : (
              <AlertTriangle size={20} />
            )}
            <span>{statusMessage.text}</span>
          </div>
        )}

        <div className="cleanup-actions">
          <div className="cleanup-card">
            <div>
              <div className="cleanup-card__header">
                <Users size={20} color="#8b5cf6" />
                <h3>Выбор пользователя</h3>
              </div>
              <p className="cleanup-card__desc">
                Выберите пользователя для очистки данных. Если не выбран — данные удалятся для всех.
              </p>
              <select
                value={selectedUserId}
                onChange={(e) => setSelectedUserId(e.target.value)}
                className="cleanup-user-select"
              >
                <option value="">Все пользователи</option>
                {users.map((user) => (
                  <option key={user.id} value={user.username}>
                    {user.full_name} ({user.username})
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="cleanup-card">
            <div>
              <div className="cleanup-card__header">
                <Trash2 size={20} color="#ef4444" />
                <h3>Сессии</h3>
              </div>
              <p className="cleanup-card__desc">
                Удаление всех сохраненных сессий, истории диалогов и кэша нейросети. При этом
                пользовательские файлы и данные продаж не удаляются.
              </p>
            </div>
            <button
              className="btn btn--primary"
              onClick={handleClearSessions}
              disabled={loadingAction !== null}
            >
              {loadingAction === "sessions"
                ? "Удаление..."
                : "Очистить сессии"}
            </button>
          </div>

          <div className="cleanup-card">
            <div>
              <div className="cleanup-card__header">
                <Database size={20} color="#3b82f6" />
                <h3>Базы данных</h3>
              </div>
              <p className="cleanup-card__desc">
                Удаление всех загруженных файлов и таблиц данных. Настройки и
                история диалогов не пострадают.
              </p>
            </div>
            <button
              className="btn btn--primary"
              onClick={handleDeleteAllDatasets}
              disabled={loadingAction !== null}
            >
              {loadingAction === "datasets"
                ? "Удаление..."
                : "Удалить базы данных"}
            </button>
          </div>

          <div className="cleanup-card">
            <div>
              <div className="cleanup-card__header cleanup-card__header--danger">
                <AlertTriangle size={20} color="#ef4444" />
                <h3>Сброс всей системы</h3>
              </div>
              <p className="cleanup-card__desc">
                Полное удаление всех загруженных баз данных, историй диалогов и
                кэша. Система вернется в исходное состояние.
              </p>
            </div>
            <button
              className="btn btn--alert"
              onClick={handleClearAll}
              disabled={loadingAction !== null}
            >
              {loadingAction === "all" ? "Очистка..." : "Очистить ВСЁ"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
