from fastapi import APIRouter, Depends, Query
from typing import List, Optional

from app.core.dependencies import get_db
from app.services import analytics
from app.schemas.models import KpiResponse, CategorySales, RegionSales, ManagerSales, ProductInfo, TimelinePoint, Anomaly

router = APIRouter(prefix="/analytics", tags=["analytics"])

@router.get("/kpis", response_model=KpiResponse)
async def get_kpis(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    dataset_id: Optional[str] = None,
    db=Depends(get_db)
):
    return analytics.get_kpis(db, start_date, end_date, dataset_id)

@router.get("/categories", response_model=List[CategorySales])
async def get_categories(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    dataset_id: Optional[str] = None,
    db=Depends(get_db)
):
    return analytics.sales_by_category(db, start_date, end_date, dataset_id)

@router.get("/regions", response_model=List[RegionSales])
async def get_regions(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    dataset_id: Optional[str] = None,
    db=Depends(get_db)
):
    return analytics.sales_by_region(db, start_date, end_date, dataset_id)

@router.get("/managers", response_model=List[ManagerSales])
async def get_managers(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    dataset_id: Optional[str] = None,
    db=Depends(get_db)
):
    return analytics.sales_by_manager(db, start_date, end_date, dataset_id)

@router.get("/timeline", response_model=List[TimelinePoint])
async def get_timeline(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    dataset_id: Optional[str] = None,
    granularity: str = Query('month', pattern='^(day|week|month)$'),
    db=Depends(get_db)
):
    return analytics.sales_timeline(db, start_date, end_date, dataset_id, granularity)

@router.get("/top-products", response_model=List[ProductInfo])
async def get_top_products(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 5,
    sort_by: str = Query('revenue', pattern='^(revenue|profit)$'),
    dataset_id: Optional[str] = None,
    category: Optional[str] = None,
    db=Depends(get_db)
):
    return analytics.top_products(
        conn=db, 
        start_date=start_date, 
        end_date=end_date, 
        dataset_id=dataset_id, 
        category=category,
        limit=limit, 
        sort_by=sort_by
    )

@router.get("/anomalies", response_model=List[Anomaly])
async def get_anomalies(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    dataset_id: Optional[str] = None,
    db=Depends(get_db)
):
    return analytics.find_anomalies(db, start_date, end_date, dataset_id)
