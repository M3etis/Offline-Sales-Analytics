"""Каталог локальных моделей и подбор модели под конфигурацию компьютера.

Модуль намеренно не зависит ни от чего, кроме стандартной библиотеки:
его же читает скрипт сборки desktop-приложения (desktop/scripts/bundle-model.py),
чтобы список рекомендуемых моделей был один на всё приложение.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

#: Ключ system_settings с моделью, выбранной в интерфейсе. Здесь, а не в model_manager:
#: модуль без зависимостей, его подключают и скрипты запуска, и сборка desktop
ACTIVE_MODEL_KEY = "ollama_model"

GB = 1_000_000_000


@dataclass(frozen=True)
class CatalogModel:
    name: str
    title: str
    params_b: float
    #: Размер загрузки (квантизация Q4_K_M), ГБ
    size_gb: float
    #: Сколько памяти нужно для работы с контекстом 8192 токенов (веса + KV-кэш + буферы), ГБ
    memory_gb: float
    description: str
    #: Участвует в автоматической рекомендации. Промпты приложения настроены под Qwen2.5 Instruct.
    primary: bool = True


CATALOG: tuple[CatalogModel, ...] = (
    CatalogModel(
        name="qwen2.5:3b",
        title="Qwen2.5 3B",
        params_b=3.1,
        size_gb=1.9,
        memory_gb=3.0,
        description="Самая лёгкая. Для слабых ноутбуков: простые вопросы, короткие ответы.",
    ),
    CatalogModel(
        name="qwen2.5:7b",
        title="Qwen2.5 7B",
        params_b=7.6,
        size_gb=4.7,
        memory_gb=6.0,
        description="Баланс скорости и качества. Уверенно справляется с типовыми вопросами по продажам.",
    ),
    CatalogModel(
        name="qwen2.5:14b",
        title="Qwen2.5 14B",
        params_b=14.8,
        size_gb=9.0,
        memory_gb=11.5,
        description="Точнее строит сложные запросы с несколькими таблицами и пишет развёрнутые выводы.",
    ),
    CatalogModel(
        name="qwen2.5:32b",
        title="Qwen2.5 32B",
        params_b=32.8,
        size_gb=20.0,
        memory_gb=23.5,
        description="Максимальное качество анализа. Нужна мощная видеокарта или Mac с большим объёмом памяти.",
    ),
    CatalogModel(
        name="qwen2.5-coder:7b",
        title="Qwen2.5 Coder 7B",
        params_b=7.6,
        size_gb=4.7,
        memory_gb=6.0,
        description="Сильнее в SQL, но тексты выводов на русском получаются суше.",
        primary=False,
    ),
    CatalogModel(
        name="qwen2.5-coder:14b",
        title="Qwen2.5 Coder 14B",
        params_b=14.8,
        size_gb=9.0,
        memory_gb=11.5,
        description="Сильнее в SQL, но тексты выводов на русском получаются суше.",
        primary=False,
    ),
)

#: На процессоре большие модели отвечают слишком долго — рекомендуем не больше 8B параметров
CPU_MAX_RECOMMENDED_PARAMS_B = 8.0


def find_model(name: str) -> CatalogModel | None:
    target = normalize_name(name)
    return next((m for m in CATALOG if normalize_name(m.name) == target), None)


def normalize_name(name: str) -> str:
    """«qwen2.5» и «qwen2.5:latest» — одна и та же модель в Ollama."""
    name = (name or "").strip()
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def min_ram_gb(model: CatalogModel) -> int:
    """Сколько оперативной памяти нужно компьютеру без видеокарты, чтобы модель вообще запустилась."""
    needed = model.memory_gb + max(2.5, model.memory_gb * 0.25)
    for ram in (4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128):
        if ram >= needed:
            return ram
    return int(needed + 0.999)


# ── Оценка под железо ──────────────────────────────────────────────────────────


def format_gb(value: float) -> str:
    """«4,7 ГБ» — размер для подписей (им же пользуется сборка desktop, bundle-model.py)."""
    return f"{value:g} ГБ".replace(".", ",")


def memory_budgets(hw: dict) -> dict:
    """Сколько памяти модель может занять «быстро» (целиком на ускорителе) и вообще.

    hw — результат app.services.hardware.detect_hardware().
    """
    ram = float(hw.get("ram_gb") or 0)
    # Системе, самому приложению и распознаванию речи тоже нужна память
    ram_free_for_model = max(0.0, ram - max(2.5, ram * 0.2))
    accel = hw.get("accelerator")

    if accel == "apple":
        # macOS отдаёт видеоядру около 2/3 общей памяти (на больших объёмах — около 3/4)
        gpu = ram * (0.75 if ram > 32 else 0.67)
        fast = min(gpu, ram_free_for_model)
        total = ram_free_for_model
    elif accel == "nvidia":
        vram = float(hw.get("vram_gb") or 0)
        fast = vram * 0.9
        # Что не влезло в видеопамять, Ollama досчитает на процессоре
        total = fast + ram_free_for_model
    else:
        fast = 0.0
        total = ram_free_for_model

    return {"fast_gb": round(fast, 1), "total_gb": round(total, 1)}


def evaluate(hw: dict, installed: list[str], active: str | None) -> dict:
    """Оценить каждую модель каталога и выбрать рекомендуемую.

    Возвращает {"budgets": ..., "recommended": name | None, "models": [...]}.
    """
    budgets = memory_budgets(hw)
    fast, total = budgets["fast_gb"], budgets["total_gb"]
    accel = hw.get("accelerator")
    disk_free = hw.get("disk_free_gb")
    installed_norm = {normalize_name(n) for n in installed}
    active_norm = normalize_name(active) if active else None

    rows = []
    for m in CATALOG:
        is_installed = normalize_name(m.name) in installed_norm
        if accel in ("apple", "nvidia") and m.memory_gb <= fast:
            fit, reason = "fast", "Целиком помещается в память видеоядра — ответы будут быстрыми"
        elif m.memory_gb <= total:
            fit = "slow"
            if accel in ("apple", "nvidia"):
                reason = "Не помещается в память видеоядра целиком — часть работы уйдёт на процессор, ответы будут медленнее"
            else:
                reason = "Видеокарта не найдена — модель будет работать на процессоре, ответы заметно медленнее"
        else:
            fit = "no"
            reason = f"Нужно около {format_gb(m.memory_gb)} памяти, а для модели доступно примерно {format_gb(total)}"

        disk_ok = True
        if not is_installed and disk_free is not None and disk_free < m.size_gb * 1.05:
            disk_ok = False

        rows.append(
            {
                **asdict(m),
                "min_ram_gb": min_ram_gb(m),
                "installed": is_installed,
                "active": active_norm == normalize_name(m.name),
                "fit": fit,
                "fit_reason": reason,
                "disk_ok": disk_ok,
                "recommended": False,
            }
        )

    recommended = pick_recommended(rows, accel)
    for row in rows:
        row["recommended"] = row["name"] == recommended

    return {"budgets": budgets, "recommended": recommended, "models": rows}


def pick_recommended(rows: list[dict], accel: str | None) -> str | None:
    primary = [r for r in rows if r["primary"]]
    by_size = lambda r: r["params_b"]  # noqa: E731

    fast = [r for r in primary if r["fit"] == "fast"]
    if fast:
        return max(fast, key=by_size)["name"]

    slow = [r for r in primary if r["fit"] == "slow"]
    if accel not in ("apple", "nvidia"):
        cpu_friendly = [r for r in slow if r["params_b"] <= CPU_MAX_RECOMMENDED_PARAMS_B]
        if cpu_friendly:
            return max(cpu_friendly, key=by_size)["name"]
    if slow:
        # Видеоядро есть, но ни одна модель не влезает целиком — берём самую лёгкую, чтобы не тормозила
        return min(slow, key=by_size)["name"]
    return None


# ── CLI для скрипта сборки ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "list":
        print(json.dumps([{**asdict(m), "min_ram_gb": min_ram_gb(m)} for m in CATALOG], ensure_ascii=False))
    else:
        print("usage: model_catalog.py list", file=sys.stderr)
        sys.exit(2)
