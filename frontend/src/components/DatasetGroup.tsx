import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Trash2,
  Table,
  Link,
  Pencil,
  Check,
} from "lucide-react";
import { Tooltip } from "react-tooltip";
import "react-tooltip/dist/react-tooltip.css";
import type { DatasetGroup as DatasetGroupType } from "../services/api";
import { dataApi } from "../services/api";

interface DatasetGroupProps {
  group: DatasetGroupType;
  selectedDatasetId: string | null;
  selectedGroupId: string | null;
  onSelectDataset: (id: string) => void;
  onSelectGroup: (groupId: string, primaryId: string) => void;
  onDeleteDataset: (id: string, name: string) => void;
  onDeleteGroup?: (groupId: string) => void;
  compact?: boolean;
  onRename?: (id: string, newName: string) => void;
  onViewDataset?: (id: string) => void;
  isAdmin?: boolean;
}

export default function DatasetGroup({
  group,
  selectedDatasetId,
  selectedGroupId,
  onSelectDataset,
  onSelectGroup,
  onDeleteDataset,
  onDeleteGroup,
  compact = false,
  onRename,
  onViewDataset,
  isAdmin = false,
}: DatasetGroupProps) {
  const [expanded, setExpanded] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editName, setEditName] = useState(group.source_file || "");

  const handleRename = async () => {
    const newName = editName.trim();
    if (!newName || newName === group.source_file) {
      setIsEditing(false);
      return;
    }
    try {
      await dataApi.renameGroup(group.group_id, newName);
      if (onRename) onRename(group.group_id, newName);
      setIsEditing(false);
    } catch (e) {
      console.error("Rename failed:", e);
    }
  };

  const totalRows = group.tables.reduce((sum, t) => sum + t.rows, 0);
  const isSingle = group.tables.length === 1;
  const isGroupMode = selectedGroupId === group.group_id || 
    (!isSingle && group.tables.some(t => t.id === selectedDatasetId));

  const [isEditingSingle, setIsEditingSingle] = useState(false);
  const [editNameSingle, setEditNameSingle] = useState("");

  const handleRenameSingle = async (ds: any) => {
    const newName = editNameSingle.trim();
    if (!newName || newName === ds.name) {
      setIsEditingSingle(false);
      return;
    }
    try {
      await dataApi.renameDataset(ds.id, newName);
      if (onRename) onRename(ds.id, newName);
      setIsEditingSingle(false);
    } catch (e) {
      console.error("Rename failed:", e);
    }
  };

  if (isSingle) {
    const ds = group.tables[0];
    return (
      <div
        onClick={() => !compact && onSelectDataset(ds.id)}
        className={`dataset-item ${selectedDatasetId === ds.id && !selectedGroupId && !compact ? "dataset-item--active" : ""} ${compact ? "dataset-item--compact" : ""}`}
      >
        <div className="dataset-item__header">
          {isEditingSingle ? (
            <input
              type="text"
              value={editNameSingle}
              onChange={(e) => setEditNameSingle(e.target.value)}
              onBlur={() => handleRenameSingle(ds)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleRenameSingle(ds);
                if (e.key === "Escape") {
                  setEditNameSingle("");
                  setIsEditingSingle(false);
                }
              }}
              className="dataset-group__edit-input"
              autoFocus
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <div className="dataset-item__name" title={ds.name}>
              {ds.name}
            </div>
          )}
          <div className="dataset-item__actions">
            {isEditingSingle ? (
              <button
                className="btn btn--tertiary btn--icon dataset-group__rename-btn dataset-group__rename-btn--active"
                onClick={(e) => {
                  e.stopPropagation();
                  handleRenameSingle(ds);
                }}
                data-tooltip-id="ds-tooltip" data-tooltip-content="Сохранить" data-tooltip-place="top"
              >
                <Check size={14} />
              </button>
            ) : (
              <button
                className="btn btn--tertiary btn--icon dataset-group__rename-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  setEditNameSingle(ds.name);
                  setIsEditingSingle(true);
                }}
                data-tooltip-id="ds-tooltip" data-tooltip-content="Переименовать" data-tooltip-place="top"
              >
                <Pencil size={14} />
              </button>
            )}
            {isAdmin && (
              <button
                className="btn btn--tertiary btn--alert btn--icon dataset-item__delete-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteDataset(ds.id, ds.name);
                }}
                data-tooltip-id="ds-tooltip" data-tooltip-content="Удалить базу" data-tooltip-place="top"
              >
                <Trash2 size={16} />
              </button>
            )}
          </div>
        </div>
        <div className="dataset-item__meta">
          <div className="dataset-item__meta-item">
            {ds.rows.toLocaleString("ru-RU")} строк
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className={`dataset-group ${isGroupMode ? "dataset-group--active" : ""} ${compact ? "dataset-group--compact" : ""}`}
    >
      {/* Group header */}
      <div className="dataset-group__header">
        {!compact && (
          <div
            className="dataset-group__toggle"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          </div>
        )}
        <div
          className={`dataset-group__info ${compact ? "dataset-group__info--compact" : ""}`}
          onClick={() => {
            if (!compact) {
              onSelectGroup(group.group_id, group.tables[0].id);
              setExpanded(!expanded);
            }
          }}
        >
          <div className="dataset-group__name-row">
            {isEditing ? (
              <div
                className="dataset-group__edit"
                onClick={(e) => e.stopPropagation()}
              >
                <input
                  type="text"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  onBlur={handleRename}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleRename();
                    if (e.key === "Escape") {
                      setEditName(group.source_file || "");
                      setIsEditing(false);
                    }
                  }}
                  className="dataset-group__edit-input"
                  autoFocus
                />
              </div>
            ) : (
              <span className="dataset-group__name" title={group.source_file}>
                {group.source_file}
              </span>
            )}
          </div>
          <div className="dataset-group__meta">
            <div className="dataset-group__details-wrapper">
              <span>{group.tables.length} таблиц</span>
              <span>{totalRows.toLocaleString("ru-RU")} строк</span>
            </div>
            {group.relationships.length > 0 && (
              <span
                className="dataset-group__relations"
                title={group.relationships
                  .map(
                    (r) =>
                      `${r.from_table}.${r.from_col} → ${r.to_table}.${r.to_col}`,
                  )
                  .join("\n")}
              >
                <Link size={11} />
                {group.relationships.length} связей
              </span>
            )}
          </div>
        </div>
        {isEditing ? (
          <button
            className="btn btn--tertiary btn--icon dataset-group__rename-btn dataset-group__rename-btn--active"
            onClick={(e) => {
              e.stopPropagation();
              handleRename();
            }}
            data-tooltip-id="ds-tooltip" data-tooltip-content="Сохранить" data-tooltip-place="top"
          >
            <Check size={14} />
          </button>
        ) : (
          <button
            className="btn btn--tertiary btn--icon dataset-group__rename-btn"
            onClick={(e) => {
              e.stopPropagation();
              setIsEditing(true);
            }}
            data-tooltip-id="ds-tooltip" data-tooltip-content="Переименовать" data-tooltip-place="top"
          >
            <Pencil size={14} />
          </button>
        )}
        {isAdmin && onDeleteGroup && (
          <button
            className="btn btn--tertiary btn--alert btn--icon dataset-group__delete-btn"
            onClick={(e) => {
              e.stopPropagation();
              onDeleteGroup(group.group_id);
            }}
            data-tooltip-id="ds-tooltip" data-tooltip-content="Удалить группу" data-tooltip-place="top"
          >
            <Trash2 size={16} />
          </button>
        )}
      </div>

      {/* Child tables - hidden in compact mode */}
      {!compact && (
        <div
          className={`dataset-group__items ${expanded ? "dataset-group__items--open" : ""}`}
        >
          {group.tables.map((ds) => (
            <div
              key={ds.id}
              onClick={() =>
                onViewDataset ? onViewDataset(ds.id) : onSelectDataset(ds.id)
              }
              className={`dataset-item dataset-item--child ${selectedDatasetId === ds.id && !isGroupMode ? "dataset-item--active" : ""}`}
            >
              <div className="dataset-item__child-indent" />
              <Table size={14} className="text-muted" />
              <div className="dataset-item__child-info">
                <div className="dataset-item__name" title={ds.name}>
                  {ds.name}
                </div>
                <div className="dataset-item__meta">
                  <span>{ds.rows.toLocaleString("ru-RU")} строк</span>
                </div>
              </div>
              {isAdmin && (
                <button
                  className="btn btn--tertiary btn--alert btn--icon dataset-item__delete-btn"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteDataset(ds.id, ds.name);
                  }}
                  data-tooltip-id="ds-tooltip" data-tooltip-content="Удалить таблицу" data-tooltip-place="top"
                >
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      <Tooltip id="ds-tooltip" delayShow={300} />
    </div>
  );
}
