import { useState, useEffect } from "react";
import { dataApi } from "../services/api";
import type { UploadResponse, DatasetGroup } from "../services/api";
import DatasetGroupComponent from "../components/DatasetGroup";
import FileUpload from "../components/FileUpload";
import { CheckCircle, Database } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useConfirm } from "../context/ConfirmContext";
import { useDataset } from "../context/DatasetContext";
import { useAuth } from "../contexts/AuthContext";
import "./UploadPage.css";

export default function UploadPage() {
  const [result, setResult] = useState<UploadResponse | null>(null);
  const [loadingDemo, setLoadingDemo] = useState(false);
  const [loadingLuxury, setLoadingLuxury] = useState(false);
  const [loadingUrl, setLoadingUrl] = useState(false);
  const [urlInput, setUrlInput] = useState("");
  const [dataInfo, setDataInfo] = useState<any>(null);
  const [groups, setGroups] = useState<DatasetGroup[]>([]);
  const navigate = useNavigate();
  const confirm = useConfirm();
  const { isAdmin } = useAuth();
  const {
    selectedDatasetId,
    setSelectedDatasetId,
    selectedGroupId,
    setSelectedGroupId,
  } = useDataset();

  const fetchInfo = async () => {
    try {
      const info = await dataApi.getInfo();
      setDataInfo(info);
      const gs = await dataApi.getGroups();
      setGroups(gs);
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    fetchInfo();
  }, []);

  const handleSuccess = (data: UploadResponse) => {
    setResult(data);
    fetchInfo();
    setSelectedDatasetId(data.dataset_id);
    setSelectedGroupId(null);
    setTimeout(() => {
      navigate(`/details?dataset=${data.dataset_id}`);
    }, 1500);
  };

  const handleLoadDemo = async () => {
    setLoadingDemo(true);
    setResult(null);
    try {
      const res = await dataApi.loadDemo();
      handleSuccess(res);
    } catch (e: any) {
      alert(e.response?.data?.detail || "Ошибка загрузки демо данных");
    } finally {
      setLoadingDemo(false);
    }
  };

  const handleLoadDemoLuxury = async () => {
    setLoadingLuxury(true);
    setResult(null);
    try {
      const res = await dataApi.loadDemoLuxury();
      handleSuccess(res);
    } catch (e: any) {
      alert(e.response?.data?.detail || "Ошибка загрузки luxury демо данных");
    } finally {
      setLoadingLuxury(false);
    }
  };

  const handleUrlUpload = async () => {
    if (!urlInput.trim()) return;
    setLoadingUrl(true);
    setResult(null);
    try {
      const res = await dataApi.uploadUrl(urlInput);
      handleSuccess(res);
      setUrlInput("");
    } catch (e: any) {
      alert(e.response?.data?.detail || "Ошибка при загрузке по URL");
    } finally {
      setLoadingUrl(false);
    }
  };

  const handleDelete = async (id: string, name: string) => {
    const isConfirmed = await confirm(`Удалить "${name}"?`, "Удаление");
    if (!isConfirmed) return;
    try {
      await dataApi.deleteDataset(id);
      fetchInfo();
    } catch (e) {
      console.error(e);
    }
  };

  const handleDeleteGroup = async (groupId: string) => {
    const group = groups.find((g) => g.group_id === groupId);
    const tableCount = group?.tables.length || 0;
    const isConfirmed = await confirm(
      `Удалить группу "${group?.source_file}" (${tableCount} таблиц)?`,
      "Удаление группы",
      20,
    );
    if (!isConfirmed) return;
    try {
      await dataApi.deleteGroup(groupId);
      fetchInfo();
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="scrollable-page animate-fade-in">
      <div className="upload-page page-container upload-page__container">
        <header className="page-header">
          <h1>Управление данными</h1>
          <p className="text-muted">
            Загрузите файл с данными о продажах для анализа
          </p>
        </header>

        <div className="upload-page__section-list">
          <div className="upload-page__top-row">
            <div className="card upload-page__card">
              <h3>Демо данные</h3>
              <p className="text-muted upload-page__desc">
                Загрузите сгенерированный демо-набор данных (стандартный или
                люкс-сегмент).
              </p>
              <div
                className="upload-page__demo-buttons"
                style={{ marginTop: "auto", paddingTop: "20px" }}
              >
                <button
                  className="btn btn--secondary upload-page__demo-btn"
                  onClick={handleLoadDemo}
                  disabled={loadingDemo || loadingUrl || loadingLuxury}
                >
                  {loadingDemo ? "Загрузка..." : "Базовое демо"}
                </button>
                <button
                  className="btn btn--secondary upload-page__demo-btn"
                  onClick={handleLoadDemoLuxury}
                  disabled={loadingDemo || loadingUrl || loadingLuxury}
                >
                  {loadingLuxury ? "Загрузка..." : "Luxury демо"}
                </button>
              </div>
            </div>

            <div
              className="card upload-page__card upload-page__db-card"
              style={{
                padding: "24px",
                display: "flex",
                flexDirection: "column",
              }}
            >
              <h3
                className="upload-page__db-title"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "10px",
                  marginBottom: "20px",
                }}
              >
                <Database className="text-accent" />
                Текущее состояние базы данных
              </h3>
              {dataInfo ? (
                <div
                  className="upload-page__db-grid"
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: "20px",
                    flex: 1,
                    alignItems: "center",
                  }}
                >
                  <div>
                    <p className="text-muted">Количество строк</p>
                    <h2
                      className="upload-page__db-value"
                      style={{ fontSize: "2rem" }}
                    >
                      {dataInfo.rows}
                    </h2>
                  </div>
                  <div>
                    <p className="text-muted">Период данных</p>
                    <p
                      className="upload-page__db-period"
                      style={{ fontSize: "1.2rem", marginTop: "10px" }}
                    >
                      {dataInfo.date_range?.min
                        ? `${dataInfo.date_range.min} — ${dataInfo.date_range.max}`
                        : "Нет данных"}
                    </p>
                  </div>
                </div>
              ) : (
                <div
                  style={{
                    flex: 1,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <p className="text-muted">Загрузка...</p>
                </div>
              )}
            </div>
          </div>

          <div className="upload-page__upload-wrapper">
            <FileUpload onSuccess={handleSuccess} />
          </div>

          <div className="card upload-page__card">
            <h3>Загрузка по URL</h3>
            <p className="text-muted upload-page__desc">
              Укажите прямую ссылку на CSV или Excel файл.
            </p>
            <input
              type="url"
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
              placeholder="https://example.com/data.csv"
              className="chat__textarea upload-page__url-input"
            />
            <button
              className="btn btn--primary upload-page__url-btn"
              onClick={handleUrlUpload}
              disabled={!urlInput.trim() || loadingUrl || loadingDemo}
            >
              {loadingUrl ? "Загрузка..." : "Загрузить по URL"}
            </button>
          </div>
        </div>

        {result && (
          <div className="alert-success animate-slide-up upload-page__success-alert">
            <div className="upload-page__success-header">
              <CheckCircle size={20} />
              <h3 className="upload-page__success-title">Успешно загружено!</h3>
            </div>
            <div>
              Добавлено строк: <strong>{result.rows_loaded}</strong>
            </div>
            <div>
              Всего строк в БД: <strong>{result.total_rows}</strong>
            </div>
            {result.warnings && result.warnings.length > 0 && (
              <div className="upload-page__success-warnings">
                <strong>Предупреждения:</strong>
                <ul className="upload-page__warning-list">
                  {result.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        <div className="card upload-page__card">
          <h3>Загруженные базы данных</h3>
          <p
            className="text-muted upload-page__desc"
            style={{ marginBottom: "15px" }}
          >
            Список всех загруженных таблиц.
          </p>
          {groups.length > 0 ? (
            <div
              className="dataset-list__items"
              style={{
                maxHeight: "none",
                display: "flex",
                flexDirection: "column",
                gap: "10px",
              }}
            >
              {groups.map((group) => (
                <DatasetGroupComponent
                  key={group.group_id}
                  group={group}
                  selectedDatasetId={selectedDatasetId}
                  selectedGroupId={selectedGroupId}
                  onSelectDataset={(id) => {
                    // For single datasets, select directly
                    if (group.tables.length === 1) {
                      setSelectedDatasetId(id);
                      setSelectedGroupId(null);
                    } else {
                      // For multi-table groups, always select the group
                      setSelectedGroupId(group.group_id);
                      setSelectedDatasetId(group.tables[0].id);
                    }
                  }}
                  onSelectGroup={(gid, primaryId) => {
                    setSelectedGroupId(gid);
                    setSelectedDatasetId(primaryId);
                  }}
                  onViewDataset={(id) => {
                    // Just view the dataset structure, don't change selection
                    // Navigate to details page for this dataset
                    window.location.href = `/details?dataset=${id}`;
                  }}
                  onDeleteDataset={handleDelete}
                  onDeleteGroup={
                    group.tables.length > 1 ? handleDeleteGroup : undefined
                  }
                  onRename={(id, newName) => {
                    setGroups((prev) =>
                      prev.map((g) =>
                        g.group_id === id
                          ? { ...g, source_file: newName }
                          : g.tables.some((t) => t.id === id)
                            ? {
                                ...g,
                                tables: g.tables.map((t) =>
                                  t.id === id ? { ...t, name: newName } : t,
                                ),
                              }
                            : g,
                      ),
                    );
                  }}
                  isAdmin={isAdmin}
                  compact
                />
              ))}
            </div>
          ) : (
            <div style={{ padding: "40px", textAlign: "center" }}>
              <p className="text-muted">Нет загруженных баз данных</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
