import { useCallback, useRef, useState } from 'react';
import type { ChangeEvent, DragEvent } from 'react';
import { ChevronDown, ChevronRight, Eye, EyeOff, FileSpreadsheet, Link2, Upload } from 'lucide-react';
import { dataApi } from '../shared/api/data.api';
import type { DbPreview, UploadResponse } from '../shared/api/data.api';
import { apiErrorMessage as errorText } from '../shared/api/errors';
import { Alert, Badge, Button, Spinner, Table } from '../shared/ui';
import { formatCount, formatNumber, ROWS, TABLES } from '../shared/lib/format';
import './FileUpload.css';

interface FileUploadProps {
  onSuccess: (data: UploadResponse) => void;
}

const ACCEPTED = ['.csv', '.xlsx', '.xls', '.db'];
const TYPE_LABEL: Record<string, string> = { date: 'дата', numeric: 'число', string: 'текст' };

/**
 * Значение колонки в строке превью. Ключ колонки бэкенд приводит к виду «revenue»
 * (str(col).lower().strip()), а строки превью приходят с исходными заголовками («Revenue »).
 */
function sampleValue(row: Record<string, unknown>, key: string): unknown {
  if (key in row) return row[key];
  const original = Object.keys(row).find((k) => k.toLowerCase().trim() === key);
  return original === undefined ? undefined : row[original];
}

export default function FileUpload({ onSuccess }: FileUploadProps) {
  const [dragActive, setDragActive] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<DbPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showSample, setShowSample] = useState<Set<string>>(new Set());
  const inputRef = useRef<HTMLInputElement>(null);
  // Номер последнего запроса превью: ответ на более ранний запрос уже не относится к выбранному файлу
  const previewRequestRef = useRef(0);

  // Без сброса значения повторный выбор того же файла не вызывает change
  const clearInput = () => {
    if (inputRef.current) inputRef.current.value = '';
  };

  const reset = () => {
    previewRequestRef.current += 1;
    setFile(null);
    setPreview(null);
    setError(null);
    setProgress(0);
    clearInput();
  };

  const loadPreview = async (selected: File) => {
    const request = ++previewRequestRef.current;
    setPreviewLoading(true);
    setError(null);
    try {
      const data = await dataApi.previewFile(selected);
      if (request !== previewRequestRef.current) return;
      setPreview(data);
      setExpanded(new Set(data.tables.length <= 2 ? data.tables.map((t) => t.name) : []));
    } catch (err) {
      if (request !== previewRequestRef.current) return;
      setError(errorText(err, 'Не удалось прочитать файл'));
      setFile(null);
      clearInput();
    } finally {
      if (request === previewRequestRef.current) setPreviewLoading(false);
    }
  };

  const handleFile = (selected: File) => {
    setError(null);
    setPreview(null);
    const name = selected.name.toLowerCase();
    if (!ACCEPTED.some((ext) => name.endsWith(ext))) {
      setError('Поддерживаются файлы CSV, XLSX, XLS и DB');
      clearInput();
      return;
    }
    setFile(selected);
    loadPreview(selected);
  };

  const handleDrag = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') setDragActive(true);
    else if (e.type === 'dragleave') setDragActive(false);
  }, []);

  const handleDrop = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    // Пока читается превью, выбор через диалог заблокирован (input disabled) — так же игнорируем и перетаскивание
    if (previewLoading) return;
    const f = e.dataTransfer.files?.[0];
    if (f) handleFile(f);
  };

  const handleChange = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) handleFile(f);
  };

  const toggle = (setter: typeof setExpanded, name: string) =>
    setter((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  const handleUpload = async () => {
    if (!file) return;
    setUploading(true);
    setError(null);
    setProgress(0);
    try {
      const res = await dataApi.uploadFile(file, (p) => setProgress(p));
      onSuccess(res);
      reset();
    } catch (err) {
      setError(errorText(err, 'Не удалось загрузить файл'));
    } finally {
      setUploading(false);
    }
  };

  const processing = uploading && progress >= 100;

  return (
    <div className="file-upload">
      {!preview && !uploading && (
        <div
          className={`file-upload__drop ${dragActive ? 'is-active' : ''} ${previewLoading ? 'is-busy' : ''}`}
          onDragEnter={handleDrag}
          onDragLeave={handleDrag}
          onDragOver={handleDrag}
          onDrop={handleDrop}
        >
          <input ref={inputRef} type="file" id="file-upload" className="file-upload__input" accept={ACCEPTED.join(',')} onChange={handleChange} disabled={previewLoading} />
          <label htmlFor="file-upload" className="file-upload__label">
            {previewLoading ? (
              <>
                <Spinner size={16} />
                <span className="file-upload__title">Читаю структуру файла</span>
                <span className="file-upload__hint">{file?.name}</span>
              </>
            ) : (
              <>
                <span className="file-upload__icon">
                  <Upload />
                </span>
                <span className="file-upload__title">Перетащите файл или выберите на компьютере</span>
                <span className="file-upload__hint">CSV, XLSX, XLS или DB</span>
              </>
            )}
          </label>
        </div>
      )}

      {preview && !uploading && (
        <div className="file-upload__preview">
          <div className="file-upload__preview-head">
            <FileSpreadsheet className="file-upload__file-icon" />
            <div className="file-upload__file">
              <span className="file-upload__file-name">{preview.filename}</span>
              <span className="file-upload__file-meta num">
                {formatCount(preview.total_tables, TABLES)} · {formatCount(preview.total_rows, ROWS)}
              </span>
            </div>
          </div>

          {preview.relationships.length > 0 && (
            <div className="file-upload__relations">
              <span className="file-upload__section-label">
                <Link2 /> Связи между таблицами
              </span>
              <ul>
                {preview.relationships.map((r) => (
                  <li key={`${r.from_table}.${r.from_col}-${r.to_table}.${r.to_col}`} className="mono">
                    {r.from_table}.{r.from_col} <span aria-hidden="true">→</span>
                    <span className="visually-hidden">ссылается на</span> {r.to_table}.{r.to_col}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="file-upload__tables">
            {preview.tables.map((table) => {
              const open = expanded.has(table.name);
              const sample = showSample.has(table.name);
              return (
                <section key={table.name} className="file-upload__table">
                  <button type="button" className="file-upload__table-head" aria-expanded={open} onClick={() => toggle(setExpanded, table.name)}>
                    {open ? <ChevronDown /> : <ChevronRight />}
                    <span className="file-upload__table-name">{table.name}</span>
                    <span className="file-upload__table-meta num">
                      {formatCount(table.columns.length, ['колонка', 'колонки', 'колонок'])} · {formatCount(table.rows, ROWS)}
                    </span>
                  </button>
                  {open && (
                    <div className="file-upload__table-body">
                      <Table density="compact">
                        <thead>
                          <tr>
                            <th>Колонка</th>
                            <th>Тип</th>
                            <th className="num">Уникальных</th>
                            <th className="num">Пустых</th>
                            <th>Пример</th>
                          </tr>
                        </thead>
                        <tbody>
                          {table.columns.map((col) => (
                            <tr key={col.key}>
                              <td className="mono">{col.key}</td>
                              <td>
                                <Badge tone={col.type === 'date' ? 'accent' : 'neutral'}>{TYPE_LABEL[col.type] ?? col.type}</Badge>
                              </td>
                              <td className="num">{formatNumber(col.unique)}</td>
                              <td className={`num ${col.nulls > 0 ? 'file-upload__nulls' : 'muted'}`}>{col.nulls > 0 ? formatNumber(col.nulls) : '—'}</td>
                              <td className="muted file-upload__sample-cell" title={String(col.sample ?? '')}>
                                {String(col.sample ?? '—')}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </Table>
                      {table.sample.length > 0 && (
                        <div className="file-upload__sample">
                          <Button size="sm" variant="ghost" icon={sample ? <EyeOff /> : <Eye />} onClick={() => toggle(setShowSample, table.name)}>
                            {sample ? 'Скрыть первые строки' : 'Показать первые строки'}
                          </Button>
                          {sample && (
                            <Table density="compact" wrapClassName="file-upload__sample-table">
                              <thead>
                                <tr>
                                  {table.columns.map((c) => (
                                    <th key={c.key} className="mono">
                                      {c.key}
                                    </th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {table.sample.map((row, i) => (
                                  <tr key={i}>
                                    {table.columns.map((c) => {
                                      const value = sampleValue(row, c.key);
                                      return (
                                        <td key={c.key} className={typeof value === 'number' ? 'num' : undefined}>
                                          {String(value ?? '—')}
                                        </td>
                                      );
                                    })}
                                  </tr>
                                ))}
                              </tbody>
                            </Table>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </section>
              );
            })}
          </div>

          <div className="file-upload__actions">
            <span className="file-upload__summary num">
              Импорт: {formatCount(preview.total_rows, ROWS)} в {formatCount(preview.total_tables, ['таблицу', 'таблицы', 'таблиц'])}
            </span>
            <div className="file-upload__buttons">
              <Button variant="ghost" onClick={reset}>
                Отмена
              </Button>
              <Button variant="primary" onClick={handleUpload}>
                Импортировать
              </Button>
            </div>
          </div>
        </div>
      )}

      {uploading && (
        <div className="file-upload__progress" role="status" aria-live="polite">
          <div className="file-upload__progress-head">
            <span>{processing ? 'Сохраняю таблицы и разбираю структуру' : 'Передаю файл'}</span>
            {!processing && <span className="num mono">{progress}%</span>}
          </div>
          <div className={`file-upload__bar ${processing ? 'is-indeterminate' : ''}`}>
            <span style={processing ? undefined : { transform: `scaleX(${progress / 100})` }} />
          </div>
          <span className="file-upload__hint">{file?.name}</span>
        </div>
      )}

      {error && (
        <Alert tone="negative" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
    </div>
  );
}
