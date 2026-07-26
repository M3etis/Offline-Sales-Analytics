from __future__ import annotations
import httpx
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
import shutil
import os
from pathlib import Path

from app.core.dependencies import get_db, get_current_admin
from app.services.ingest import ingest_file, detect_column_type
from app.db.connection import DatabaseManager
from app.schemas.models import UploadResponse, DataInfo, TableData, UrlUploadRequest, Dataset, DatasetGroup, DbPreview, TablePreview, ColumnPreview
from app.services.analytics import get_table_data

router = APIRouter(prefix="/data", tags=["data"])

@router.get("/manifest/{dataset_id}")
async def get_manifest(dataset_id: str, db=Depends(get_db)):
    """Получить манифест преданализа датасета."""
    from app.services.preanalysis import load_manifest, is_manifest_valid
    manifest = load_manifest(db, dataset_id)
    if not manifest:
        return {"status": "not_found"}
    return {
        "status": "ready" if is_manifest_valid(db, dataset_id) else "stale",
        "manifest": manifest
    }


@router.post("/rebuild/{dataset_id}")
async def rebuild_dataset(dataset_id: str, current_user: dict = Depends(get_current_admin), db=Depends(get_db)):
    """Принудительный rebuild манифеста преданализа."""
    row = db.execute("SELECT table_name FROM datasets WHERE id = ?", [dataset_id]).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Dataset not found")
    
    from app.services.preanalysis import preanalyze_dataset
    manifest = preanalyze_dataset(db, dataset_id, row[0])
    return {"status": "rebuilt", "data_hash": manifest["data_hash"]}


@router.post("/preview", response_model=DbPreview)
async def preview_file(file: UploadFile = File(...), current_user: dict = Depends(get_current_admin)):
    if not file.filename.endswith(('.csv', '.xlsx', '.xls', '.db')):
        raise HTTPException(status_code=400, detail="Разрешены файлы CSV, XLSX и DB")

    safe_filename = os.path.basename(file.filename)
    if not safe_filename or safe_filename in ('.', '..'):
        raise HTTPException(status_code=400, detail="Invalid filename")

    temp_dir = Path("data/raw")
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / safe_filename

    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        ext = Path(safe_filename).suffix.lower()
        tables = []
        relationships = []

        if ext == '.db':
            from app.services.ingest import _try_duckdb_tables, _try_sqlite_tables, _detect_relationships
            raw_tables = _try_duckdb_tables(str(temp_path))
            if raw_tables is None:
                raw_tables = _try_sqlite_tables(str(temp_path))
            if raw_tables:
                relationships = _detect_relationships(raw_tables, str(temp_path))
                for tname, df in raw_tables.items():
                    columns = []
                    for col, dtype in df.dtypes.items():
                        meta_type = detect_column_type(str(col), dtype)
                        sample_val = str(df[col].dropna().iloc[0]) if not df[col].dropna().empty else None
                        nulls = int(df[col].isna().sum())
                        unique = int(df[col].nunique())
                        columns.append(ColumnPreview(
                            key=str(col).lower().strip(), type=meta_type,
                            sample=sample_val, nulls=nulls, unique=unique
                        ))
                    sample_rows = df.head(3).fillna("").to_dict(orient="records")
                    # Convert non-serializable types
                    for row in sample_rows:
                        for k, v in row.items():
                            if hasattr(v, 'isoformat'):
                                row[k] = str(v)
                            elif not isinstance(v, (str, int, float, bool)):
                                row[k] = str(v)
                    tables.append(TablePreview(name=tname, rows=len(df), columns=columns, sample=sample_rows))
        else:
            import pandas as pd
            if ext == '.csv':
                try:
                    df = pd.read_csv(str(temp_path), sep=None, engine='python', encoding='utf-8')
                except UnicodeDecodeError:
                    df = pd.read_csv(str(temp_path), sep=None, engine='python', encoding='cp1251')
            else:
                df = pd.read_excel(str(temp_path), engine='openpyxl')

            columns = []
            for col, dtype in df.dtypes.items():
                meta_type = detect_column_type(str(col), dtype)
                sample_val = str(df[col].dropna().iloc[0]) if not df[col].dropna().empty else None
                nulls = int(df[col].isna().sum())
                unique = int(df[col].nunique())
                columns.append(ColumnPreview(
                    key=str(col).lower().strip(), type=meta_type,
                    sample=sample_val, nulls=nulls, unique=unique
                ))
            sample_rows = df.head(3).fillna("").to_dict(orient="records")
            for row in sample_rows:
                for k, v in row.items():
                    if hasattr(v, 'isoformat'):
                        row[k] = str(v)
                    elif not isinstance(v, (str, int, float, bool)):
                        row[k] = str(v)
            tables.append(TablePreview(name=safe_filename, rows=len(df), columns=columns, sample=sample_rows))

        return DbPreview(
            filename=safe_filename,
            tables=tables,
            relationships=relationships,
            total_rows=sum(t.rows for t in tables),
            total_tables=len(tables)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_path.exists():
            os.remove(temp_path)


@router.post("/upload", response_model=UploadResponse)
async def upload_data(file: UploadFile = File(...), current_user: dict = Depends(get_current_admin), db=Depends(get_db)):
    if not file.filename.endswith(('.csv', '.xlsx', '.xls', '.db')):
        raise HTTPException(status_code=400, detail="Разрешены файлы CSV, XLSX и DB")

    # Sanitize filename to prevent path traversal
    safe_filename = os.path.basename(file.filename)
    if not safe_filename or safe_filename in ('.', '..'):
        raise HTTPException(status_code=400, detail="Invalid filename")

    temp_dir = Path("data/raw")
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / safe_filename
    
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Here db is the duckdb connection
        result = ingest_file(str(temp_path), db)
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_path.exists():
            os.remove(temp_path)

@router.post("/url", response_model=UploadResponse)
async def upload_url_data(payload: UrlUploadRequest, current_user: dict = Depends(get_current_admin), db=Depends(get_db)):
    from urllib.parse import urlparse

    url = payload.url
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL format")

    # SSRF protection: validate URL
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            raise HTTPException(status_code=400, detail="Only HTTP/HTTPS protocols allowed")

        # Block internal/private IPs
        hostname = parsed.hostname
        if hostname in ('localhost', '127.0.0.1', '0.0.0.0', '::1'):
            raise HTTPException(status_code=400, detail="Cannot access local URLs")

        # Resolve DNS and block private IP ranges (including IPv4-mapped IPv6)
        import socket
        import ipaddress
        try:
            resolved = socket.getaddrinfo(hostname, None)
            for family, _, _, _, sockaddr in resolved:
                ip = ipaddress.ip_address(sockaddr[0])
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    raise HTTPException(status_code=400, detail="Cannot access private or internal IP addresses")
        except HTTPException:
            raise
        except (socket.gaierror, ValueError):
            raise HTTPException(status_code=400, detail="Could not resolve hostname")

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid URL")

    temp_dir = Path("data/raw")
    temp_dir.mkdir(parents=True, exist_ok=True)
    filename = url.split('/')[-1]
    if not filename or not filename.endswith(('.csv', '.xlsx', '.xls', '.db')):
        filename = "downloaded_data.csv"

    # Sanitize filename
    safe_filename = os.path.basename(filename)
    temp_path = temp_dir / safe_filename

    # Size limit (50MB from settings)
    from app.core.config import settings
    MAX_SIZE = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    try:
        async with httpx.AsyncClient() as client:
            async with client.stream('GET', url, follow_redirects=True, timeout=30.0) as response:
                response.raise_for_status()

                # Check content length if provided
                content_length = response.headers.get('content-length')
                if content_length and int(content_length) > MAX_SIZE:
                    raise HTTPException(status_code=400, detail=f"File too large (max {settings.MAX_UPLOAD_SIZE_MB}MB)")

                # Stream download with size check
                downloaded_size = 0
                with open(temp_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        downloaded_size += len(chunk)
                        if downloaded_size > MAX_SIZE:
                            raise HTTPException(status_code=400, detail=f"File too large (max {settings.MAX_UPLOAD_SIZE_MB}MB)")
                        f.write(chunk)

        result = ingest_file(str(temp_path), db)
        return {"status": "success", **result}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=400, detail=f"Failed to download file: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_path.exists():
            os.remove(temp_path)

@router.post("/demo", response_model=UploadResponse)
async def upload_demo_data(current_user: dict = Depends(get_current_admin), db=Depends(get_db)):
    """Generate and load demo data."""
    try:
        from scripts.generate_demo_data import generate_sales_data
        
        # Generate to a temporary file
        temp_file = Path("data/demo/demo_sales.csv")
        generate_sales_data(2000)
        
        if not temp_file.exists():
            raise HTTPException(status_code=500, detail="Demo file was not created")
            
        result = ingest_file(str(temp_file), db)
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/demo-luxury", response_model=UploadResponse)
async def upload_demo_luxury_data(current_user: dict = Depends(get_current_admin), db=Depends(get_db)):
    """Generate and load luxury demo data."""
    try:
        from scripts.generate_luxury_data import generate_luxury_data
        
        # Generate to a temporary file
        temp_file = Path("data/demo/demo_luxury.csv")
        generate_luxury_data(2000)
        
        if not temp_file.exists():
            raise HTTPException(status_code=500, detail="Demo file was not created")
            
        result = ingest_file(str(temp_file), db)
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/datasets", response_model=list[Dataset])
async def get_datasets(db=Depends(get_db)):
    try:
        datasets = db.execute("SELECT id, name, rows, uploaded_at, source_group, source_file, relationships, table_name FROM datasets ORDER BY uploaded_at DESC").fetchall()
        result = []
        for d in datasets:
            item = {
                "id": d[0], "name": d[1], "rows": d[2], "uploaded_at": str(d[3]),
                "source_group": d[4], "source_file": d[5], "table_name": d[7],
            }
            if d[6]:
                import json
                try:
                    item["relationships"] = json.loads(d[6])
                except Exception:
                    item["relationships"] = None
            result.append(item)
        return result
    except Exception:
        return []


@router.get("/groups", response_model=list[DatasetGroup])
async def get_dataset_groups(db=Depends(get_db)):
    try:
        datasets = db.execute(
            "SELECT id, name, rows, uploaded_at, source_group, source_file, relationships, table_name "
            "FROM datasets ORDER BY uploaded_at DESC"
        ).fetchall()

        groups: dict[str, dict] = {}
        individual: list = []

        for d in datasets:
            ds = Dataset(
                id=d[0], name=d[1], rows=d[2], uploaded_at=str(d[3]),
                source_group=d[4], source_file=d[5], relationships=None
            )
            if d[6]:
                import json
                try:
                    ds.relationships = json.loads(d[6])
                except Exception:
                    pass

            if d[4]:
                gid = d[4]
                if gid not in groups:
                    groups[gid] = {
                        "group_id": gid,
                        "source_file": d[5] or "unknown",
                        "tables": [],
                        "relationships": ds.relationships or []
                    }
                groups[gid]["tables"].append(ds)
                if ds.relationships and not groups[gid]["relationships"]:
                    groups[gid]["relationships"] = ds.relationships
            else:
                individual.append(ds)

        result = [DatasetGroup(**g) for g in groups.values()]
        for ds in individual:
            result.append(DatasetGroup(
                group_id=ds.id, source_file=ds.name,
                tables=[ds], relationships=[]
            ))
        return result
    except Exception:
        return []

@router.delete("/datasets/{dataset_id}")
async def delete_dataset(dataset_id: str, current_user: dict = Depends(get_current_admin), db=Depends(get_db)):
    try:
        import re
        sanitized_id = dataset_id.replace('-', '_')
        if not re.match(r'^dataset_[a-f0-9_]+$', f'dataset_{sanitized_id}'):
            raise HTTPException(status_code=400, detail="Invalid dataset ID format")
        table_name = f"dataset_{sanitized_id}"
        db.execute("BEGIN")
        db.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
        try:
            db.execute("DELETE FROM dataset_manifest WHERE dataset_id = ?", (dataset_id,))
        except Exception:
            pass  # Table might not exist yet
        db.execute(f"DROP TABLE IF EXISTS {table_name}")
        db.execute("COMMIT")
        return {"status": "success", "message": f"Dataset {dataset_id} deleted"}
    except Exception as e:
        db.execute("ROLLBACK")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/groups/{group_id}")
async def delete_dataset_group(group_id: str, current_user: dict = Depends(get_current_admin), db=Depends(get_db)):
    try:
        import re
        rows = db.execute("SELECT id, table_name FROM datasets WHERE source_group = ?", (group_id,)).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="Group not found")
        db.execute("BEGIN")
        for ds_id, table_name in rows:
            sanitized = table_name.replace('-', '_')
            if re.match(r'^(sales|dataset_[a-f0-9_]+)$', sanitized):
                db.execute(f"DROP TABLE IF EXISTS {sanitized}")
            try:
                db.execute("DELETE FROM dataset_manifest WHERE dataset_id = ?", (ds_id,))
            except Exception:
                pass  # Table might not exist yet
        db.execute("DELETE FROM datasets WHERE source_group = ?", (group_id,))
        db.execute("COMMIT")
        return {"status": "success", "message": f"Deleted {len(rows)} datasets in group"}
    except HTTPException:
        raise
    except Exception as e:
        db.execute("ROLLBACK")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/datasets")
async def delete_all_datasets(current_user: dict = Depends(get_current_admin), db=Depends(get_db)):
    try:
        import hashlib

        db.execute("BEGIN")

        sessions = db.execute("SELECT id, dataset_id FROM chat_sessions").fetchall()
        session_ids = [row[0] for row in sessions]

        hashes_to_delete = []
        if session_ids:
            placeholders = ', '.join(['?'] * len(session_ids))
            user_messages = db.execute(
                f"SELECT content, session_id FROM chat_messages WHERE session_id IN ({placeholders}) AND role = 'user'",
                session_ids
            ).fetchall()

            ds_map = {row[0]: row[1] for row in sessions}
            for content, sid in user_messages:
                dk = str(ds_map.get(sid, "global")) if ds_map.get(sid) else "global"
                q = content.strip().lower()
                h_false = hashlib.md5((q + str(False) + dk).encode()).hexdigest()
                h_true = hashlib.md5((q + str(True) + dk).encode()).hexdigest()
                hashes_to_delete.extend([h_false, h_true])

            db.execute(f"DELETE FROM chat_messages WHERE session_id IN ({placeholders})", session_ids)
            db.execute("DELETE FROM chat_sessions")

        if hashes_to_delete:
            ph = ', '.join(['?'] * len(hashes_to_delete))
            db.execute(f"DELETE FROM llm_cache WHERE query_hash IN ({ph})", hashes_to_delete)

        tables = db.execute("SELECT id FROM datasets").fetchall()
        for row in tables:
            table_name = f"dataset_{row[0].replace('-', '_')}"
            db.execute(f"DROP TABLE IF EXISTS {table_name}")
        try:
            db.execute("DELETE FROM dataset_manifest")
        except Exception:
            pass  # Table might not exist yet
        db.execute("DELETE FROM datasets")

        db.execute("COMMIT")
        return {"status": "success", "message": f"Deleted {len(tables)} datasets and {len(session_ids)} sessions"}
    except Exception as e:
        db.execute("ROLLBACK")
        raise HTTPException(status_code=500, detail=str(e))


from pydantic import BaseModel

class RenameRequest(BaseModel):
    name: str


@router.put("/datasets/{dataset_id}/rename")
async def rename_dataset(dataset_id: str, request: RenameRequest, current_user: dict = Depends(get_current_admin), db=Depends(get_db)):
    """Rename a dataset."""
    try:
        import re
        sanitized_id = dataset_id.replace('-', '_')
        if not re.match(r'^dataset_[a-f0-9_]+$', f'dataset_{sanitized_id}'):
            raise HTTPException(status_code=400, detail="Invalid dataset ID format")

        new_name = request.name.strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="Name cannot be empty")
        if len(new_name) > 200:
            raise HTTPException(status_code=400, detail="Name too long (max 200 characters)")

        result = db.execute("UPDATE datasets SET name = ? WHERE id = ?", [new_name, dataset_id])
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Dataset not found")

        return {"status": "success", "name": new_name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/groups/{group_id}/rename")
async def rename_group(group_id: str, request: RenameRequest, current_user: dict = Depends(get_current_admin), db=Depends(get_db)):
    """Rename a dataset group (updates source_file for all tables in the group)."""
    try:
        new_name = request.name.strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="Name cannot be empty")
        if len(new_name) > 200:
            raise HTTPException(status_code=400, detail="Name too long (max 200 characters)")

        result = db.execute("UPDATE datasets SET source_file = ? WHERE source_group = ?", [new_name, group_id])
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Group not found")

        return {"status": "success", "name": new_name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/info", response_model=DataInfo)
async def get_data_info():
    from app.core.dependencies import db_manager
    info = db_manager.get_table_info()
    return info

@router.get("/table", response_model=TableData)
async def get_table(
    page: int = 1,
    page_size: int = 50,
    sort_by: str = 'date',
    sort_order: str = 'desc',
    category: str = None,
    region: str = None,
    manager: str = None,
    start_date: str = None,
    end_date: str = None,
    dataset_id: str = None,
    search: str = None,
    db=Depends(get_db)
):
    try:
        return get_table_data(
            db, start_date=start_date, end_date=end_date,
            category=category, region=region, manager=manager,
            sort_by=sort_by, sort_order=sort_order, page=page, page_size=page_size,
            dataset_id=dataset_id, search=search
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/full-cleanup")
async def full_cleanup(current_user: dict = Depends(get_current_admin), db=Depends(get_db)):
    try:
        db.execute("BEGIN")
        # Delete dataset tables
        tables = db.execute("SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'dataset_%' AND table_name != 'datasets'").fetchall()
        for t in tables:
            db.execute(f"DROP TABLE IF EXISTS {t[0]}")
        # Truncate tables (with existence checks)
        try:
            db.execute("DELETE FROM dataset_manifest")
        except Exception:
            pass  # Table might not exist yet
        db.execute("DELETE FROM datasets")
        db.execute("DELETE FROM sales")
        db.execute("DELETE FROM chat_sessions")
        db.execute("DELETE FROM chat_messages")
        db.execute("DELETE FROM llm_cache")
        db.execute("COMMIT")
        return {"status": "success"}
    except Exception as e:
        db.execute("ROLLBACK")
        raise HTTPException(status_code=500, detail=str(e))
