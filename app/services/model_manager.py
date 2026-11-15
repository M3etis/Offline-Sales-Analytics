"""Скачивание моделей в Ollama и переключение активной модели ассистента.

Активная модель хранится в system_settings (ключ ollama_model) и переживает перезапуск.
Если её нет — используется OLLAMA_MODEL из окружения или .env.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque

import httpx

from app.core.dependencies import db_manager
from app.services.llm import ollama_client
from app.services.model_catalog import ACTIVE_MODEL_KEY, find_model, normalize_name

logger = logging.getLogger(__name__)

_SPEED_WINDOW_S = 5.0
#: Как часто при старте ждём Ollama, чтобы проверить сохранённую модель
_RESTORE_RETRY_S = 5.0
#: Ссылки на фоновые задачи, чтобы сборщик мусора не прервал их на середине
_background: set[asyncio.Task] = set()


# ── Активная модель ───────────────────────────────────────────────────────────


def _settings_cursor():
    """Отдельное соединение с той же базой (курсор DuckDB): его можно использовать параллельно."""
    return db_manager.get_connection().cursor()


def _read_setting() -> str | None:
    with _settings_cursor() as cur:
        row = cur.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key = ?", [ACTIVE_MODEL_KEY]
        ).fetchone()
    return row[0] if row and row[0] else None


def _write_setting(name: str) -> None:
    with _settings_cursor() as cur:
        cur.execute(
            "INSERT INTO system_settings (setting_key, setting_value) VALUES (?, ?) "
            "ON CONFLICT (setting_key) DO UPDATE SET setting_value = excluded.setting_value",
            [ACTIVE_MODEL_KEY, name],
        )


async def installed_models() -> list[dict]:
    """Модели, скачанные в Ollama. Бросает httpx.HTTPError, если Ollama недоступна."""
    client = ollama_client._get_client()
    response = await client.get(f"{ollama_client.base_url}/api/tags", timeout=5.0)
    response.raise_for_status()
    return response.json().get("models", [])


def is_chat_model(entry: dict) -> bool:
    """Отсекаем модели эмбеддингов (bge-m3 и т. п.): отвечать на вопросы они не умеют."""
    caps = entry.get("capabilities")
    if isinstance(caps, list) and caps:
        return "completion" in caps
    family = (entry.get("details") or {}).get("family", "")
    return "bert" not in family


def _apply_saved_model(saved: str, installed: set[str]) -> None:
    if normalize_name(saved) not in installed:
        # Например, в собранное приложение попала база разработчика с другой моделью
        logger.warning(f"Saved model '{saved}' is not installed, keeping '{ollama_client.model}'")
        return
    logger.info(f"Active model restored from settings: {saved}")
    ollama_client.model = saved


async def _installed_names() -> set[str]:
    return {normalize_name(m.get("name", "")) for m in await installed_models()}


async def _restore_when_ollama_ready(saved: str, startup_model: str) -> None:
    """Ollama не ответила при старте: ждём её и применяем выбор, только убедившись, что модель скачана.

    Пока ждём, работает модель по умолчанию (OLLAMA_MODEL или встроенная в сборку).
    Если админ за это время сам выбрал модель, сохранённый выбор уже не применяем.
    """
    while True:
        await asyncio.sleep(_RESTORE_RETRY_S)
        if ollama_client.model != startup_model:
            return
        try:
            installed = await _installed_names()
        except httpx.HTTPError:
            continue
        if ollama_client.model == startup_model:
            _apply_saved_model(saved, installed)
        return


async def restore_active_model() -> None:
    """При старте: применить модель, выбранную в интерфейсе, если она всё ещё скачана."""
    try:
        saved = await asyncio.to_thread(_read_setting)
    except Exception as e:
        logger.warning(f"Could not read active model setting: {e}")
        return
    if not saved or saved == ollama_client.model:
        return
    try:
        installed = await _installed_names()
    except httpx.HTTPError:
        # Ollama ещё не поднялась. Применять непроверенный выбор нельзя: модели может не быть
        # (в desktop-сборку попадает БД разработчика), и ассистент перестал бы отвечать
        _spawn(_restore_when_ollama_ready(saved, ollama_client.model))
        return
    _apply_saved_model(saved, installed)


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _unload(name: str) -> None:
    try:
        await ollama_client._get_client().post(
            f"{ollama_client.base_url}/api/generate",
            json={"model": name, "keep_alive": 0},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        logger.info(f"Unload of '{name}' skipped: {e}")


async def activate_model(name: str) -> str:
    """Сделать модель активной: запомнить выбор, выгрузить прежнюю из памяти, прогреть новую."""
    previous = ollama_client.model
    await asyncio.to_thread(_write_setting, name)
    ollama_client.model = name
    logger.info(f"Active model switched: {previous} -> {name}")

    async def _swap():
        if previous and normalize_name(previous) != normalize_name(name):
            await _unload(previous)
        await ollama_client.warmup()

    task = asyncio.create_task(_swap())
    _background.add(task)
    task.add_done_callback(_background.discard)
    return name


# ── Скачивание ────────────────────────────────────────────────────────────────


def _humanize_error(message: str) -> str:
    low = message.lower()
    if "no space left" in low:
        return "На диске закончилось место. Освободите место и повторите — скачанная часть сохранится."
    if "file does not exist" in low or "not found" in low:
        return "Модель не найдена в каталоге Ollama."
    if any(s in low for s in ("dial tcp", "lookup", "i/o timeout", "tls", "connection reset", "eof")):
        return "Нет связи с registry.ollama.ai. Проверьте интернет и повторите — загрузка продолжится с того же места."
    return f"Ollama: {message}"


class ModelPullManager:
    """Одна загрузка за раз; прогресс опрашивается фронтендом, поэтому переживает уход со страницы."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._samples: deque[tuple[float, int]] = deque()
        self._state: dict = {"status": "idle", "model": None}

    def snapshot(self) -> dict:
        return dict(self._state)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, name: str, activate: bool) -> dict:
        if self.running:
            if self._state.get("model") == name:
                return self.snapshot()
            raise RuntimeError(f"Уже идёт загрузка модели {self._state.get('model')}")
        self._samples.clear()
        self._state = {
            "status": "running",
            "phase": "manifest",
            "model": name,
            "activate": activate,
            "activated": False,
            "completed": 0,
            "total": 0,
            "speed_bps": None,
            "eta_s": None,
            "error": None,
            "started_at": time.time(),
            "finished_at": None,
        }
        self._task = asyncio.create_task(self._run(name, activate))
        return self.snapshot()

    async def cancel(self) -> dict:
        if self.running and self._task:
            if self._state.get("phase") == "activating":
                # Модель уже скачана, а запись в БД нельзя прервать посередине:
                # дожидаемся активации, чтобы выбор в БД и в памяти не разошёлся
                await asyncio.shield(self._task)
                return self.snapshot()
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        return self.snapshot()

    def _update_progress(self, layers: dict[str, tuple[int, int]]) -> None:
        completed = sum(c for c, _ in layers.values())
        total = sum(t for _, t in layers.values())
        now = time.monotonic()
        self._samples.append((now, completed))
        while len(self._samples) > 2 and now - self._samples[0][0] > _SPEED_WINDOW_S:
            self._samples.popleft()
        speed = None
        if len(self._samples) >= 2:
            (t0, c0), (t1, c1) = self._samples[0], self._samples[-1]
            if t1 - t0 >= 1.0:
                speed = max(0.0, (c1 - c0) / (t1 - t0))
        self._state.update(
            completed=completed,
            total=total,
            speed_bps=speed,
            eta_s=(total - completed) / speed if speed and total > completed else None,
        )

    async def _run(self, name: str, activate: bool) -> None:
        layers: dict[str, tuple[int, int]] = {}
        completed = False
        try:
            client = ollama_client._get_client()
            timeout = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)
            async with client.stream(
                "POST", f"{ollama_client.base_url}/api/pull", json={"model": name, "stream": True}, timeout=timeout
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    try:
                        body = json.loads(body).get("error", body)
                    except (json.JSONDecodeError, AttributeError):
                        pass
                    raise RuntimeError(_humanize_error(body or f"HTTP {response.status_code}"))

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("error"):
                        raise RuntimeError(_humanize_error(event["error"]))
                    status = event.get("status", "")
                    if event.get("digest") and event.get("total"):
                        layers[event["digest"]] = (int(event.get("completed") or 0), int(event["total"]))
                        self._state["phase"] = "downloading"
                        self._update_progress(layers)
                    elif status.startswith("verifying"):
                        self._state.update(phase="verifying", speed_bps=None, eta_s=None)
                    elif status.startswith(("writing", "removing")):
                        self._state["phase"] = "writing"
                    elif status == "success":
                        completed = True
                        break

            # Ollama завершает поток событием success или error. Поток, закрывшийся без них
            # (чужой ответ прокси, оборванное соединение), не значит, что модель скачана
            if not completed:
                raise RuntimeError("Ollama не подтвердила окончание загрузки. Повторите — загрузка продолжится с того же места.")

            if activate:
                self._state["phase"] = "activating"
                # shield: отмена задачи не должна оборвать активацию между записью в БД
                # и переключением ollama_client.model
                await asyncio.shield(activate_model(name))
                self._state["activated"] = True
            self._state.update(status="success", phase="done")
            logger.info(f"Model pulled: {name}")
        except asyncio.CancelledError:
            self._state["status"] = "cancelled"
            logger.info(f"Model pull cancelled: {name}")
            raise
        except httpx.ConnectError:
            self._state.update(status="error", error="Ollama не запущена — запустите её и повторите.")
        except httpx.HTTPError as e:
            self._state.update(status="error", error=_humanize_error(str(e) or e.__class__.__name__))
        except Exception as e:
            logger.error(f"Model pull failed: {e}")
            self._state.update(status="error", error=str(e))
        finally:
            self._state.update(finished_at=time.time(), speed_bps=None, eta_s=None)


pull_manager = ModelPullManager()


def catalog_name(name: str) -> str | None:
    """Каноническое имя модели из каталога или None, если такой модели в каталоге нет."""
    model = find_model(name)
    return model.name if model else None
