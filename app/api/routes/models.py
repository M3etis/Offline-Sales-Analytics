"""Локальные модели: подбор под компьютер, скачивание и переключение активной модели."""
from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.dependencies import get_current_admin
from app.services.hardware import detect_hardware
from app.services.llm import ollama_client
from app.services.model_catalog import evaluate, normalize_name
from app.services.model_manager import (
    activate_model,
    catalog_name,
    installed_models,
    is_chat_model,
    pull_manager,
)

router = APIRouter(prefix="/ai/models", tags=["models"])


class PullRequest(BaseModel):
    name: str
    activate: bool = True


class ActivateRequest(BaseModel):
    name: str


@router.get("")
async def list_models():
    """Конфигурация компьютера, каталог с оценкой «потянет ли» и рекомендуемая модель."""
    hardware = await asyncio.to_thread(detect_hardware)
    ollama: dict = {"status": "ok"}
    try:
        installed = await installed_models()
    except httpx.ConnectError:
        installed, ollama = [], {"status": "offline", "error": "Ollama не запущена"}
    except httpx.HTTPError as e:
        installed, ollama = [], {"status": "error", "error": str(e)}

    active = ollama_client.model
    result = evaluate(hardware, [m.get("name", "") for m in installed], active)

    catalog_names = {normalize_name(m["name"]) for m in result["models"]}
    other = [
        {
            "name": m.get("name"),
            "size_gb": round((m.get("size") or 0) / 1e9, 1),
            "params": (m.get("details") or {}).get("parameter_size"),
            "active": normalize_name(m.get("name", "")) == normalize_name(active),
        }
        for m in installed
        if normalize_name(m.get("name", "")) not in catalog_names and is_chat_model(m)
    ]

    return {
        "hardware": hardware,
        "budgets": result["budgets"],
        "recommended": result["recommended"],
        "active_model": active,
        "ollama": ollama,
        "models": result["models"],
        "other_installed": other,
        "pull": pull_manager.snapshot(),
    }


@router.get("/pull")
async def pull_status():
    return pull_manager.snapshot()


@router.post("/pull", dependencies=[Depends(get_current_admin)])
async def start_pull(payload: PullRequest):
    name = catalog_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="Можно скачать только модель из списка рекомендуемых")
    try:
        return pull_manager.start(name, payload.activate)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/pull", dependencies=[Depends(get_current_admin)])
async def cancel_pull():
    return await pull_manager.cancel()


@router.post("/active", dependencies=[Depends(get_current_admin)])
async def set_active_model(payload: ActivateRequest):
    try:
        installed = await installed_models()
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Ollama не запущена")
    match = next(
        (m["name"] for m in installed if normalize_name(m.get("name", "")) == normalize_name(payload.name) and is_chat_model(m)),
        None,
    )
    if not match:
        raise HTTPException(status_code=404, detail="Модель не скачана")
    await activate_model(match)
    return {"active_model": match}
