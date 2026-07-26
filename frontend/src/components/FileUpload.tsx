import { useState, useCallback } from 'react';
import { Upload, FileText, AlertTriangle, ChevronDown, ChevronRight, Table, Link, Database, Hash, Calendar, Type, Eye, EyeOff } from 'lucide-react';
import { dataApi } from '../services/api';
import type { DbPreview } from '../services/api';
import './FileUpload.css';

interface FileUploadProps {
  onSuccess: (data: any) => void;
}

export default function FileUpload({ onSuccess }: FileUploadProps) {
  const [dragActive, setDragActive] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [progressStage, setProgressStage] = useState<string>('');
  const [preview, setPreview] = useState<DbPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set());
  const [showSample, setShowSample] = useState<Set<string>>(new Set());

  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFile(e.dataTransfer.files[0]);
    }
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    e.preventDefault();
    if (e.target.files && e.target.files[0]) {
      handleFile(e.target.files[0]);
    }
  };

  const handleFile = (selectedFile: File) => {
    setError(null);
    setPreview(null);
    const validTypes = ['text/csv', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'application/vnd.ms-excel', 'application/x-sqlite3', 'application/octet-stream'];
    if (!validTypes.includes(selectedFile.type) && !selectedFile.name.endsWith('.csv') && !selectedFile.name.endsWith('.xlsx') && !selectedFile.name.endsWith('.xls') && !selectedFile.name.endsWith('.db')) {
      setError('Поддерживаются форматы CSV, XLSX и DB');
      return;
    }
    setFile(selectedFile);
    loadPreview(selectedFile);
  };

  const loadPreview = async (selectedFile: File) => {
    setPreviewLoading(true);
    setError(null);
    try {
      const data = await dataApi.previewFile(selectedFile);
      setPreview(data);
      setExpandedTables(new Set(data.tables.map(t => t.name)));
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Не удалось загрузить превью файла');
      setFile(null);
    } finally {
      setPreviewLoading(false);
    }
  };

  const toggleTable = (name: string) => {
    setExpandedTables(prev => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const toggleSample = (name: string) => {
    setShowSample(prev => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const handleUpload = async () => {
    if (!file) return;
    setLoading(true);
    setError(null);
    setProgress(0);
    setProgressStage('Подготовка к загрузке...');

    let processingInterval: any = null;
    const stages = [
      'Загрузка файла на сервер...',
      'Сохранение данных в базу...',
      'Анализ структуры таблиц...',
      'Кеширование схемы для AI...',
      'Завершение...',
    ];

    try {
      const res = await dataApi.uploadFile(file, (p) => {
        if (p < 100) {
          setProgress(p);
          setProgressStage(`Загрузка файла на сервер... (${p}%)`);
        } else {
          setProgressStage(stages[1]);
          if (!processingInterval) {
            let backendProgress = 0;
            let stageIdx = 1;
            processingInterval = setInterval(() => {
              backendProgress += Math.random() * 3;
              if (backendProgress > 95) backendProgress = 95;
              setProgress(backendProgress);
              const newStageIdx = Math.min(Math.floor(backendProgress / 25) + 1, stages.length - 1);
              if (newStageIdx !== stageIdx) {
                stageIdx = newStageIdx;
                setProgressStage(stages[stageIdx]);
              }
            }, 400);
          }
        }
      });
      if (processingInterval) clearInterval(processingInterval);
      setProgress(100);
      setProgressStage('Загрузка успешно завершена!');
      onSuccess(res);
      setFile(null);
      setPreview(null);
    } catch (err: any) {
      if (processingInterval) clearInterval(processingInterval);
      setError(err.response?.data?.detail || 'Произошла ошибка при загрузке файла');
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = () => {
    setFile(null);
    setPreview(null);
    setError(null);
  };

  const typeIcon = (type: string) => {
    if (type === 'date') return <Calendar size={12} />;
    if (type === 'numeric') return <Hash size={12} />;
    return <Type size={12} />;
  };

  const typeLabel = (type: string) => {
    if (type === 'date') return 'дата';
    if (type === 'numeric') return 'число';
    return 'текст';
  };

  const truncate = (val: any, maxLen = 30) => {
    const s = String(val ?? '');
    return s.length > maxLen ? s.slice(0, maxLen) + '…' : s;
  };

  return (
    <div className="file-upload-wrapper card">
      <div
        className={`drop-zone ${dragActive ? 'active' : ''}`}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
      >
        <input
          type="file"
          id="file-upload"
          className="file-input"
          accept=".csv, .xlsx, .xls, .db"
          onChange={handleChange}
        />
        <label htmlFor="file-upload" className="drop-label">
          <Upload size={48} className={`upload-icon ${dragActive ? 'bounce' : ''}`} />
          <h3>Перетащите файл сюда</h3>
          <p className="text-muted">или нажмите для выбора (CSV, XLSX, DB)</p>
        </label>
      </div>

      {previewLoading && (
        <div className="preview-loading animate-slide-up">
          <div className="spinner-small" />
          <span>Анализ структуры файла...</span>
        </div>
      )}

      {preview && !loading && (
        <div className="preview-panel animate-slide-up">
          {/* Header with summary */}
          <div className="preview-header">
            <div className="preview-file-info">
              <Database size={22} className="text-accent" />
              <div>
                <span className="preview-filename">{preview.filename}</span>
                <span className="preview-stats">
                  {preview.total_tables} {preview.total_tables === 1 ? 'таблица' : preview.total_tables < 5 ? 'таблицы' : 'таблиц'} · {preview.total_rows.toLocaleString('ru-RU')} записей
                </span>
              </div>
            </div>
          </div>

          {/* Relationships */}
          {preview.relationships.length > 0 && (
            <div className="preview-relationships">
              <div className="preview-rel-header">
                <Link size={14} />
                <span>
                  {preview.relationships.length} {preview.relationships.length === 1 ? 'связь' : preview.relationships.length < 5 ? 'связи' : 'связей'} обнаружено
                </span>
              </div>
              <div className="preview-rel-list">
                {preview.relationships.map(r => (
                  <div key={`${r.from_table}.${r.from_col}-${r.to_table}.${r.to_col}`} className="preview-rel-item">
                    <span className="preview-rel-table">{r.from_table}</span>
                    <span className="preview-rel-col">.{r.from_col}</span>
                    <span className="preview-rel-arrow">→</span>
                    <span className="preview-rel-table">{r.to_table}</span>
                    <span className="preview-rel-col">.{r.to_col}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Tables */}
          <div className="preview-tables">
            {preview.tables.map(table => {
              const colTypes = { date: 0, numeric: 0, string: 0 };
              table.columns.forEach(c => { colTypes[c.type as keyof typeof colTypes]++; });

              return (
                <div key={table.name} className="preview-table">
                  <div className="preview-table__header" onClick={() => toggleTable(table.name)}>
                    <span className="preview-table__chevron">
                      {expandedTables.has(table.name) ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </span>
                    <Table size={14} className="text-accent" />
                    <span className="preview-table__name">{table.name}</span>
                    <div className="preview-table__badges">
                      {colTypes.date > 0 && <span className="preview-badge preview-badge--date">{colTypes.date} дат.</span>}
                      {colTypes.numeric > 0 && <span className="preview-badge preview-badge--numeric">{colTypes.numeric} чис.</span>}
                      {colTypes.string > 0 && <span className="preview-badge preview-badge--string">{colTypes.string} текст.</span>}
                    </div>
                    <span className="preview-table__rows">{table.rows.toLocaleString('ru-RU')} стр.</span>
                  </div>

                  {expandedTables.has(table.name) && (
                    <div className="preview-table__body">
                      {/* Columns */}
                      <div className="preview-columns">
                        <div className="preview-columns__header">
                          <span>Колонка</span>
                          <span>Тип</span>
                          <span>Уникальных</span>
                          <span>Пустых</span>
                          <span>Пример</span>
                        </div>
                        {table.columns.map(col => (
                          <div key={col.key} className="preview-column">
                            <span className="preview-column__name">{col.key}</span>
                            <span className={`preview-column__type preview-column__type--${col.type}`}>
                              {typeIcon(col.type)} {typeLabel(col.type)}
                            </span>
                            <span className="preview-column__stat">{col.unique.toLocaleString('ru-RU')}</span>
                            <span className="preview-column__stat preview-column__stat--warn">
                              {col.nulls > 0 ? col.nulls.toLocaleString('ru-RU') : '—'}
                            </span>
                            <span className="preview-column__sample">{truncate(col.sample)}</span>
                          </div>
                        ))}
                      </div>

                      {/* Sample data toggle */}
                      {table.sample.length > 0 && (
                        <div className="preview-sample">
                          <button
                            className="btn btn--tertiary btn--small"
                            onClick={(e) => { e.stopPropagation(); toggleSample(table.name); }}
                          >
                            {showSample.has(table.name) ? <EyeOff size={12} /> : <Eye size={12} />}
                            {showSample.has(table.name) ? 'Скрыть данные' : 'Показать данные'}
                          </button>

                          {showSample.has(table.name) && (
                            <div className="preview-sample__table-wrap">
                              <table className="preview-sample__table">
                                <thead>
                                  <tr>
                                    {table.columns.map(c => (
                                      <th key={c.key}>{c.key}</th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {table.sample.map((row, i) => (
                                    <tr key={i}>
                                      {table.columns.map(c => (
                                        <td key={c.key}>{truncate(row[c.key], 25)}</td>
                                      ))}
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Actions */}
          <div className="preview-actions">
            <span className="preview-actions__summary">
              Будет импортировано: {preview.total_rows.toLocaleString('ru-RU')} записей в {preview.total_tables} {preview.total_tables === 1 ? 'таблицу' : 'таблиц'}
            </span>
            <div className="preview-actions__buttons">
              <button className="btn btn--tertiary" onClick={handleCancel}>Отмена</button>
              <button className="btn btn-primary" onClick={handleUpload}>Импортировать</button>
            </div>
          </div>
        </div>
      )}

      {file && !preview && !previewLoading && (
        <div className="selected-file animate-slide-up">
          <FileText size={24} className="text-primary" />
          <div className="file-details">
            <span className="file-name">{file.name}</span>
            <span className="file-size">{(file.size / 1024 / 1024).toFixed(2)} MB</span>
          </div>
        </div>
      )}

      {loading && (
        <div className="upload-progress animate-slide-up">
          <div className="upload-progress-container">
            <div
              className="upload-progress-bar"
              style={{ width: `${progress}%` }}
            />
          </div>
          <div className="upload-progress-text">{progressStage}</div>
        </div>
      )}

      {error && (
        <div className="alert-error animate-fade-in">
          <AlertTriangle size={20} />
          <span>{error}</span>
        </div>
      )}
    </div>
  );
}
