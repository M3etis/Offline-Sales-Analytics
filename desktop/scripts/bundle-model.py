#!/usr/bin/env python3
"""Встраивание локальной модели в desktop-приложение.

Модель выбирается из списка рекомендуемых (app/services/model_catalog.py) или берётся текущая —
та, которой сейчас пользуется приложение. Выбранная модель копируется из локальной папки Ollama
в desktop/resources/ollama-models, а её имя записывается в bundle.json: по нему собранное
приложение понимает, какую модель использовать по умолчанию.

    python3 scripts/bundle-model.py                     # интерактивный выбор
    python3 scripts/bundle-model.py --model current     # текущая модель приложения
    python3 scripts/bundle-model.py --model qwen2.5:7b  # конкретная модель из списка
    python3 scripts/bundle-model.py --model none        # без модели: пользователь скачает её в приложении
    BUNDLE_MODEL=qwen2.5:7b bash scripts/package.sh     # то же через переменную окружения
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DESKTOP = Path(__file__).resolve().parent.parent
ROOT = DESKTOP.parent
BUNDLE_DIR = DESKTOP / "resources" / "ollama-models"
BUNDLE_META = BUNDLE_DIR / "bundle.json"
DEFAULT_REGISTRY = "registry.ollama.ai"


def load_module(name: str, path: Path):
    """Модули каталога и железа зависят только от stdlib — грузим их напрямую, без пакета app."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # нужно dataclasses при загрузке по пути
    spec.loader.exec_module(module)
    return module


catalog = load_module("model_catalog", ROOT / "app" / "services" / "model_catalog.py")
hardware = load_module("hardware", ROOT / "app" / "services" / "hardware.py")


def say(message: str = "") -> None:
    print(message, file=sys.stderr)


# ── Текущая модель приложения ──────────────────────────────────────────────────


def _read_env_value(key: str) -> str | None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip().strip("'\"") or None
    return None


def _db_path() -> Path:
    raw = os.environ.get("DB_PATH") or _read_env_value("DB_PATH") or "data/processed/sales.duckdb"
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


def current_model() -> tuple[str, str]:
    """(модель, откуда взята). Порядок как в приложении: выбор в интерфейсе → .env → значение по умолчанию."""
    db = _db_path()
    if db.exists():
        try:
            import duckdb  # есть в venv проекта

            con = duckdb.connect(str(db), read_only=True)
            try:
                row = con.execute(
                    "SELECT setting_value FROM system_settings WHERE setting_key = ?", [catalog.ACTIVE_MODEL_KEY]
                ).fetchone()
            finally:
                con.close()
            if row and row[0]:
                return row[0], "выбрана в интерфейсе приложения"
        except ImportError:
            pass
        except Exception as e:  # база занята запущенным сервером или ещё не создана
            if "lock" in str(e).lower():
                say("[bundle] База занята запущенным сервером приложения — модель, выбранную в интерфейсе, не узнать.")
                say("         Беру модель из .env. Остановите сервер, чтобы учесть выбор из интерфейса.")
            else:
                say(f"[bundle] Не удалось прочитать настройки из базы ({e.__class__.__name__}) — смотрю .env")

    from_env = os.environ.get("OLLAMA_MODEL") or _read_env_value("OLLAMA_MODEL")
    if from_env:
        return from_env, "из .env"

    config = (ROOT / "app" / "core" / "config.py").read_text(encoding="utf-8")
    match = re.search(r'OLLAMA_MODEL:\s*str\s*=\s*"([^"]+)"', config)
    return (match.group(1) if match else "qwen2.5:14b"), "значение по умолчанию"


# ── Локальная папка Ollama ─────────────────────────────────────────────────────


def ollama_models_dir() -> Path:
    # Та же папка, где бэкенд считает свободное место (app/services/hardware.py)
    return hardware.models_dir()


def manifest_relpath(name: str) -> Path:
    """qwen2.5:7b → manifests/registry.ollama.ai/library/qwen2.5/7b"""
    repo, tag = name, "latest"
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        repo, tag = name.rsplit(":", 1)
    parts = repo.split("/")
    if len(parts) == 1:
        host, namespace, model = DEFAULT_REGISTRY, "library", parts[0]
    elif len(parts) == 2:
        host, namespace, model = DEFAULT_REGISTRY, parts[0], parts[1]
    else:
        host, namespace, model = parts[0], parts[1], "/".join(parts[2:])
    return Path("manifests") / host / namespace / model / tag


def is_installed(name: str) -> bool:
    return (ollama_models_dir() / manifest_relpath(name)).is_file()


def pull(name: str) -> None:
    exe = shutil.which("ollama")
    if not exe:
        raise SystemExit(
            f"[bundle] Модели {name} нет локально, а команда ollama не найдена.\n"
            f"         Установите Ollama (https://ollama.com) и выполните: ollama pull {name}"
        )
    say(f"[bundle] Скачиваю {name} через ollama pull…")
    if subprocess.run([exe, "pull", name]).returncode != 0:
        raise SystemExit(f"[bundle] ollama pull {name} завершилась с ошибкой. Запущен ли сервер Ollama (ollama serve)?")
    if not is_installed(name):
        raise SystemExit(
            f"[bundle] После загрузки манифест не найден в {ollama_models_dir()}.\n"
            "         Если сервер Ollama использует другую папку моделей, укажите её в OLLAMA_MODELS."
        )


# ── Выбор ──────────────────────────────────────────────────────────────────────


def recommended_here() -> str | None:
    """Модель, которую приложение порекомендовало бы этому (сборочному) компьютеру."""
    try:
        return catalog.evaluate(hardware.detect_hardware(), [], None)["recommended"]
    except Exception:
        return None


def resolve_choice(raw: str, current: str) -> str | None:
    """Имя модели, None — «без модели». Неизвестное имя — ошибка."""
    value = raw.strip()
    if value.lower() == "current":
        return current
    if value.lower() in ("none", "no", "-"):
        return None
    model = catalog.find_model(value)
    if model:
        return model.name
    if catalog.normalize_name(value) == catalog.normalize_name(current):
        return current
    names = ", ".join(m.name for m in catalog.CATALOG)
    raise SystemExit(f"[bundle] Модели «{value}» нет в списке рекомендуемых. Доступны: {names}, а также current или none.")


def ask(current: str, source: str) -> str | None:
    here = recommended_here()
    options: list[str | None] = [current]
    say("Какую модель встроить в приложение?")
    say()
    say(f"  1) Текущая: {current} ({source}){' · скачана' if is_installed(current) else ' · нужно скачать'}")
    width = max(len(m.name) for m in catalog.CATALOG)
    for i, m in enumerate(catalog.CATALOG, start=2):
        options.append(m.name)
        status = "скачана" if is_installed(m.name) else "нужно скачать"
        tags = []
        if m.name == here:
            tags.append("подходит этому компьютеру")
        if not m.primary:
            tags.append("специализированная")
        suffix = f"  [{', '.join(tags)}]" if tags else ""
        say(f"  {i}) {m.name:<{width}}  {catalog.format_gb(m.size_gb):>7}  · для ПК от {catalog.min_ram_gb(m)} ГБ ОЗУ · {status}{suffix}")
    say("  0) Без модели — пользователь выберет и скачает её в самом приложении")
    say()

    while True:
        try:
            answer = input("Номер или имя модели [1]: ").strip()
        except EOFError:
            answer = ""
        if not answer:
            return current
        if answer.isdigit():
            idx = int(answer)
            if idx == 0:
                return None
            if 1 <= idx <= len(options):
                return options[idx - 1]
            say(f"Нет пункта {idx}")
            continue
        try:
            return resolve_choice(answer, current)
        except SystemExit as e:
            say(str(e))


# ── Копирование ────────────────────────────────────────────────────────────────


def _blob_name(digest: str) -> str:
    return digest.replace(":", "-")


def _place_blob(src: Path, dst: Path) -> str:
    if dst.exists():
        if dst.stat().st_size == src.stat().st_size:
            return "уже на месте"
        dst.unlink()
    try:
        os.link(src, dst)  # жёсткая ссылка: мгновенно и без лишних гигабайт на диске
        return "ссылка"
    except OSError:
        shutil.copyfile(src, dst)
        return "копия"


def bundle(name: str | None) -> None:
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    blobs_dir = BUNDLE_DIR / "blobs"

    if name is None:
        shutil.rmtree(BUNDLE_DIR / "manifests", ignore_errors=True)
        shutil.rmtree(blobs_dir, ignore_errors=True)
        BUNDLE_META.write_text(json.dumps({"model": None}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        say("[bundle] Модель не встраивается: приложение предложит скачать подходящую при первом запуске.")
        return

    # Сначала убеждаемся, что новая модель целиком есть у Ollama, и только потом трогаем прошлую сборку:
    # иначе неудачный запуск (нет сети, нет файла) оставлял бы bundle.json с моделью без файлов
    if not is_installed(name):
        pull(name)

    src_root = ollama_models_dir()
    rel = manifest_relpath(name)
    manifest_src = src_root / rel
    manifest = json.loads(manifest_src.read_text(encoding="utf-8"))
    digests = [manifest["config"]["digest"]] + [layer["digest"] for layer in manifest.get("layers", [])]
    needed = {_blob_name(d) for d in digests}
    for digest in digests:
        src = src_root / "blobs" / _blob_name(digest)
        if not src.is_file():
            raise SystemExit(f"[bundle] Не найден файл модели {src}. Перекачайте её: ollama pull {name}")

    # С этого момента прошлая сборка меняется. Пока копирование не закончено, bundle.json
    # говорит «модели нет» — прерванный запуск не выдаст неполную модель за готовую
    BUNDLE_META.write_text(json.dumps({"model": None}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(BUNDLE_DIR / "manifests", ignore_errors=True)

    # Убираем файлы модели, встроенной в прошлую сборку
    blobs_dir.mkdir(parents=True, exist_ok=True)
    for stale in blobs_dir.iterdir():
        if stale.name not in needed:
            stale.unlink()

    total = 0
    for digest in digests:
        src = src_root / "blobs" / _blob_name(digest)
        how = _place_blob(src, blobs_dir / _blob_name(digest))
        size = src.stat().st_size
        total += size
        if size > 50_000_000:
            say(f"[bundle] {_blob_name(digest)[:19]}…  {catalog.format_gb(round(size / 1e9, 1))}  ({how})")

    # Манифест — последним, как и в model-seed.ts: Ollama не увидит модель с недостающими файлами
    (BUNDLE_DIR / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(manifest_src, BUNDLE_DIR / rel)

    BUNDLE_META.write_text(
        json.dumps(
            {"model": name, "bundled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    say(f"[bundle] Встроена модель {name}: {catalog.format_gb(round(total / 1e9, 1))}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Встроить локальную модель в desktop-приложение")
    parser.add_argument("--model", help="current, none или имя модели из списка рекомендуемых")
    parser.add_argument("--list", action="store_true", help="показать список рекомендуемых моделей и выйти")
    args = parser.parse_args()

    current, source = current_model()

    if args.list:
        for m in catalog.CATALOG:
            say(f"{m.name:<20} {catalog.format_gb(m.size_gb):>7}  для ПК от {catalog.min_ram_gb(m)} ГБ ОЗУ  {m.description}")
        say(f"\nТекущая модель приложения: {current} ({source})")
        return

    raw = args.model or os.environ.get("BUNDLE_MODEL")
    if raw:
        choice = resolve_choice(raw, current)
    elif sys.stdin.isatty():
        choice = ask(current, source)
    else:
        say(f"[bundle] Модель не указана (--model или BUNDLE_MODEL), беру текущую: {current} ({source})")
        choice = current

    bundle(choice)


if __name__ == "__main__":
    main()
