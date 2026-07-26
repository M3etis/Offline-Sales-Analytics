import React, { useState, useEffect } from "react";
import { useAuth } from "../contexts/AuthContext";
import { usersApi } from "../services/api";
import type { UserOut } from "../services/api";
import { useConfirm } from "../context/ConfirmContext";
import { Plus, Trash2, Edit2, Key } from "lucide-react";
import "./UsersPage.css";

const MAIN_PERMISSIONS = [
  { id: "dashboard", label: "Дашборд" },
  { id: "analyst", label: "Ассистент" },
  { id: "sessions", label: "История" },
  { id: "details", label: "База данных" },
];

const SETTINGS_PERMISSIONS = [
  { id: "settings_status", label: "Статус служб" },
  { id: "settings_data", label: "Загрузка данных" },
  { id: "settings_assistant", label: "Настройки ассистента" },
  { id: "settings_feedback", label: "Оценки ассистента" },
  { id: "settings_cleanup", label: "Очистка данных" },
];

const PERMISSION_OPTIONS = [...MAIN_PERMISSIONS, ...SETTINGS_PERMISSIONS];

export default function UsersPage() {
  const { username } = useAuth();
  const confirm = useConfirm();
  const [users, setUsers] = useState<UserOut[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  // Modal states
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [isPasswordModalOpen, setIsPasswordModalOpen] = useState(false);

  // Form states
  const [selectedUser, setSelectedUser] = useState<UserOut | null>(null);
  const [formData, setFormData] = useState({
    username: "",
    full_name: "",
    password: "",
    role: "user",
    permissions: ["dashboard", "analyst", "sessions", "details"],
  });

  useEffect(() => {
    fetchUsers();
  }, []);

  const fetchUsers = async () => {
    setIsLoading(true);
    try {
      const data = await usersApi.getUsers();
      setUsers(data);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Ошибка загрузки пользователей");
    } finally {
      setIsLoading(false);
    }
  };

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await usersApi.createUser({
        username: formData.username,
        full_name: formData.full_name,
        password: formData.password,
        role: formData.role,
        permissions: formData.permissions,
      });
      setIsCreateModalOpen(false);
      fetchUsers();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Ошибка создания пользователя");
    }
  };

  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedUser) return;
    setError("");
    try {
      await usersApi.updateUser(selectedUser.id, {
        full_name: formData.full_name,
        role: formData.role,
        permissions: formData.permissions,
      });
      setIsEditModalOpen(false);
      fetchUsers();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Ошибка обновления пользователя");
    }
  };

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedUser) return;
    setError("");
    try {
      await usersApi.updatePassword(selectedUser.id, formData.password);
      setIsPasswordModalOpen(false);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Ошибка смены пароля");
    }
  };

  const handleDelete = async (user: UserOut) => {
    if (user.username === username) {
      alert("Вы не можете удалить сами себя!");
      return;
    }
    const isConfirmed = await confirm(`Вы уверены, что хотите удалить пользователя ${user.username}?`);
    if (isConfirmed) {
      try {
        await usersApi.deleteUser(user.id);
        fetchUsers();
      } catch (err: any) {
        alert(err.response?.data?.detail || "Ошибка удаления пользователя");
      }
    }
  };

  const openCreateModal = () => {
    setFormData({
      username: "",
      full_name: "",
      password: "",
      role: "user",
      permissions: ["dashboard", "analyst", "sessions", "details"],
    });
    setError("");
    setIsCreateModalOpen(true);
  };

  const openEditModal = (user: UserOut) => {
    setSelectedUser(user);
    setFormData({
      ...formData,
      full_name: user.full_name || "",
      role: user.role,
      permissions: user.permissions || [],
    });
    setError("");
    setIsEditModalOpen(true);
  };

  const openPasswordModal = (user: UserOut) => {
    setSelectedUser(user);
    setFormData({ ...formData, password: "" });
    setError("");
    setIsPasswordModalOpen(true);
  };

  const togglePermission = (permId: string) => {
    setFormData((prev) => {
      const perms = prev.permissions.includes(permId)
        ? prev.permissions.filter((p) => p !== permId)
        : [...prev.permissions, permId];
      return { ...prev, permissions: perms };
    });
  };

  return (
    <div className="scrollable-page animate-fade-in">
      <div className="users-page page-container">
        <header
          className="page-header"
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div>
            <h1>Пользователи</h1>
            <p>Управление доступом и ролями пользователей системы</p>
          </div>
          <button className="btn btn--primary users-add-btn" onClick={openCreateModal}>
            <Plus size={18} />
            Добавить пользователя
          </button>
        </header>

        <div className="users-table-container">
          {isLoading ? (
            <div style={{ padding: "20px", textAlign: "center" }}>
              Загрузка...
            </div>
          ) : (
            <table className="users-table">
              <thead>
                <tr>
                  <th>Логин</th>
                  <th>Имя</th>
                  <th>Роль</th>
                  <th>Доступ к разделам</th>
                  <th>Действия</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.id}>
                    <td>{user.username}</td>
                    <td>{user.full_name}</td>
                    <td>
                      <span className={`users-role-badge ${user.role}`}>
                        {user.role}
                      </span>
                    </td>
                    <td>
                      {user.role === "admin" ? (
                        <span className="users-perm-badge">Все разделы</span>
                      ) : (
                        <div className="users-perms-list">
                          {user.permissions.map((p) => {
                            const option = PERMISSION_OPTIONS.find(
                              (o) => o.id === p,
                            );
                            return (
                              <span key={p} className="users-perm-badge">
                                {option ? option.label : p}
                              </span>
                            );
                          })}
                          {user.permissions.length === 0 && (
                            <span style={{ color: "var(--text-muted)" }}>
                              Нет доступов
                            </span>
                          )}
                        </div>
                      )}
                    </td>
                    <td>
                      <div className="users-actions">
                        <button
                          className="btn btn--tertiary btn--icon users-action-btn"
                          title="Изменить доступы"
                          onClick={() => openEditModal(user)}
                        >
                          <Edit2 size={16} />
                        </button>
                        <button
                          className="btn btn--tertiary btn--icon users-action-btn"
                          title="Сменить пароль"
                          onClick={() => openPasswordModal(user)}
                        >
                          <Key size={16} />
                        </button>
                        <button
                          className="btn btn--tertiary btn--alert btn--icon users-action-btn delete"
                          title="Удалить"
                          onClick={() => handleDelete(user)}
                        >
                          <Trash2 size={16} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* CREATE MODAL */}
        {isCreateModalOpen && (
          <div className="modal-overlay">
            <div className="modal-content">
              <h2>Создать пользователя</h2>
              {error && <div className="error-message">{error}</div>}
              <form onSubmit={handleCreateSubmit}>
                <div className="form-group">
                  <label>Имя пользователя (Логин)</label>
                  <input
                    type="text"
                    value={formData.username}
                    onChange={(e) =>
                      setFormData({ ...formData, username: e.target.value })
                    }
                    required
                  />
                </div>
                <div className="form-group">
                  <label>Отображаемое Имя</label>
                  <input
                    type="text"
                    value={formData.full_name}
                    onChange={(e) =>
                      setFormData({ ...formData, full_name: e.target.value })
                    }
                  />
                </div>
                <div className="form-group">
                  <label>Пароль</label>
                  <input
                    type="password"
                    value={formData.password}
                    onChange={(e) =>
                      setFormData({ ...formData, password: e.target.value })
                    }
                    required
                  />
                </div>
                <div className="form-group">
                  <label>Роль</label>
                  <select
                    value={formData.role}
                    onChange={(e) =>
                      setFormData({ ...formData, role: e.target.value })
                    }
                  >
                    <option value="user">Пользователь</option>
                    <option value="admin">Администратор</option>
                  </select>
                </div>

                {formData.role === "user" && (
                  <div className="permissions-group">
                    <label>Права доступа</label>
                    <div className="checkbox-list">
                      {MAIN_PERMISSIONS.map((opt) => (
                        <label key={opt.id} className="checkbox-label">
                          <input
                            type="checkbox"
                            checked={formData.permissions.includes(opt.id)}
                            onChange={() => togglePermission(opt.id)}
                          />
                          {opt.label}
                        </label>
                      ))}
                    </div>
                    <hr className="permissions-divider" />
                    <div className="checkbox-list">
                      {SETTINGS_PERMISSIONS.map((opt) => (
                        <label key={opt.id} className="checkbox-label">
                          <input
                            type="checkbox"
                            checked={formData.permissions.includes(opt.id)}
                            onChange={() => togglePermission(opt.id)}
                          />
                          {opt.label}
                        </label>
                      ))}
                    </div>
                  </div>
                )}

                <div className="modal-actions">
                  <button
                    type="button"
                    className="btn-cancel"
                    onClick={() => setIsCreateModalOpen(false)}
                  >
                    Отмена
                  </button>
                  <button type="submit" className="btn-save">
                    Создать
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* EDIT MODAL */}
        {isEditModalOpen && selectedUser && (
          <div className="modal-overlay">
            <div className="modal-content">
              <h2>Настройки доступа: {selectedUser.username}</h2>
              {error && <div className="error-message">{error}</div>}
              <form onSubmit={handleEditSubmit}>
                <div className="form-group">
                  <label>Отображаемое Имя</label>
                  <input
                    type="text"
                    value={formData.full_name}
                    onChange={(e) =>
                      setFormData({ ...formData, full_name: e.target.value })
                    }
                  />
                </div>
                <div className="form-group">
                  <label>Роль</label>
                  <select
                    value={formData.role}
                    onChange={(e) =>
                      setFormData({ ...formData, role: e.target.value })
                    }
                  >
                    <option value="user">Пользователь</option>
                    <option value="admin">Администратор</option>
                  </select>
                </div>

                {formData.role === "user" && (
                  <div className="permissions-group">
                    <label>Права доступа</label>
                    <div className="checkbox-list">
                      {MAIN_PERMISSIONS.map((opt) => (
                        <label key={opt.id} className="checkbox-label">
                          <input
                            type="checkbox"
                            checked={formData.permissions.includes(opt.id)}
                            onChange={() => togglePermission(opt.id)}
                          />
                          {opt.label}
                        </label>
                      ))}
                    </div>
                    <hr className="permissions-divider" />
                    <div className="checkbox-list">
                      {SETTINGS_PERMISSIONS.map((opt) => (
                        <label key={opt.id} className="checkbox-label">
                          <input
                            type="checkbox"
                            checked={formData.permissions.includes(opt.id)}
                            onChange={() => togglePermission(opt.id)}
                          />
                          {opt.label}
                        </label>
                      ))}
                    </div>
                  </div>
                )}

                <div className="modal-actions">
                  <button
                    type="button"
                    className="btn-cancel"
                    onClick={() => setIsEditModalOpen(false)}
                  >
                    Отмена
                  </button>
                  <button type="submit" className="btn-save">
                    Сохранить
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* PASSWORD MODAL */}
        {isPasswordModalOpen && selectedUser && (
          <div className="modal-overlay">
            <div className="modal-content">
              <h2>Сменить пароль: {selectedUser.username}</h2>
              {error && <div className="error-message">{error}</div>}
              <form onSubmit={handlePasswordSubmit}>
                <div className="form-group">
                  <label>Новый пароль</label>
                  <input
                    type="password"
                    value={formData.password}
                    onChange={(e) =>
                      setFormData({ ...formData, password: e.target.value })
                    }
                    required
                  />
                </div>
                <div className="modal-actions">
                  <button
                    type="button"
                    className="btn-cancel"
                    onClick={() => setIsPasswordModalOpen(false)}
                  >
                    Отмена
                  </button>
                  <button type="submit" className="btn-save">
                    Сохранить
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
