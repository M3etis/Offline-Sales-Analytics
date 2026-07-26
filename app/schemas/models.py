from __future__ import annotations
from pydantic import BaseModel
from typing import Optional, Any
from datetime import date


class UrlUploadRequest(BaseModel):
    url: str

class UploadResponse(BaseModel):
    status: str
    dataset_id: str
    rows_loaded: int
    total_rows: int
    columns: list[str]
    warnings: list[str]

class Relationship(BaseModel):
    from_table: str
    from_col: str
    to_table: str
    to_col: str


class Dataset(BaseModel):
    id: str
    name: str
    rows: int
    uploaded_at: str
    source_group: Optional[str] = None
    source_file: Optional[str] = None
    table_name: Optional[str] = None
    relationships: Optional[list[Relationship]] = None


class DatasetGroup(BaseModel):
    group_id: str
    source_file: str
    tables: list[Dataset]
    relationships: list[Relationship]


class ColumnPreview(BaseModel):
    key: str
    type: str  # date, numeric, string
    sample: Optional[str] = None
    nulls: int = 0
    unique: int = 0


class TablePreview(BaseModel):
    name: str
    rows: int
    columns: list[ColumnPreview]
    sample: list[dict] = []  # first 3 rows


class DbPreview(BaseModel):
    filename: str
    tables: list[TablePreview]
    relationships: list[Relationship]
    total_rows: int
    total_tables: int


class DataInfo(BaseModel):
    rows: int
    columns: list[dict[str, str]]
    date_range: Optional[dict[str, Optional[str]]]


class KpiResponse(BaseModel):
    total_revenue: float
    total_profit: float
    order_count: int
    avg_check: float
    avg_discount: float
    total_quantity: int = 0
    revenue_change: Optional[float] = None
    profit_change: Optional[float] = None
    orders_change: Optional[float] = None


class CategorySales(BaseModel):
    category: str
    revenue: float
    profit: float
    orders: int
    quantity: int = 0
    share: Optional[float] = None


class RegionSales(BaseModel):
    region: str
    revenue: float
    profit: float
    orders: int
    quantity: int = 0


class ManagerSales(BaseModel):
    manager: str
    revenue: float
    profit: float
    orders: int
    quantity: int = 0


class ProductInfo(BaseModel):
    product: str
    category: str
    revenue: float
    profit: float
    quantity: int
    orders: int


class TimelinePoint(BaseModel):
    period: str
    revenue: float
    profit: float
    orders: int
    quantity: int = 0


class PeriodComparison(BaseModel):
    current: dict[str, float]
    previous: dict[str, float]
    changes: dict[str, Optional[float]]


class Anomaly(BaseModel):
    type: str
    entity: str
    metric: str
    value: float
    average: float
    deviation_pct: float
    severity: str
    description: str


class WeakSpot(BaseModel):
    type: str
    entity: str
    description: str
    severity: str
    metric: str
    value: Any


class TableData(BaseModel):
    data: list[dict]
    columns: list[dict[str, str]] = []
    total: int
    page: int
    page_size: int
    total_pages: int


class AskRequest(BaseModel):
    question: str
    extended: bool = False
    session_id: Optional[str] = None
    dataset_id: Optional[str] = None


class AskResponse(BaseModel):
    answer: str
    sql: Optional[str] = None
    intent: Optional[str] = None
    data: Optional[list[dict]] = None
    chart_type: Optional[str] = None
    error: Optional[str] = None
    processing_time: Optional[float] = None
    is_cached: Optional[bool] = False
    cached_at: Optional[str] = None


class QueryLog(BaseModel):
    timestamp: str
    question: str
    intent: Optional[str]
    sql: Optional[str]
    answer: str
    processing_time: float
    status: str
    error: Optional[str] = None


class VoiceTranscribeResponse(BaseModel):
    text: str
    language: str = 'ru'
    confidence: float = 0.0


class VoiceSynthesizeRequest(BaseModel):
    text: str
