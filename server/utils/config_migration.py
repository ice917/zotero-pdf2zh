from __future__ import annotations

import copy
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import toml


def _package_name(requirement: str) -> str:
    raw = str(requirement).strip()
    name = re.split(r"[<>=!~\[\s@]", raw, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def _merge_managed_requirements(default_items: list, current_items: list) -> list:
    """Use release constraints for managed packages and preserve user extras.

    [自研补丁 2026-09-03] 用户手动钉死版本的托管包(包名后带版本说明符,
    如 ``pdf2zh==1.9.11``)必须保留用户钉版; 旧实现每次启动迁移都用 release
    默认约束覆写, 用户锁的版本静默丢失。未钉版的托管包仍跟随 release 约束,
    额外包原样保留。
    """
    defaults = [str(item) for item in default_items]
    current = [str(item) for item in current_items]
    managed_names = {_package_name(item) for item in defaults}

    def _has_version_pin(requirement: str) -> bool:
        tail = requirement[len(_package_name(requirement)):]
        return bool(re.search(r"[<>=!~]", tail))

    user_pinned = {
        _package_name(item): item
        for item in current
        if _package_name(item) in managed_names and _has_version_pin(item)
    }

    merged = []
    seen = set()
    for item in defaults:
        name = _package_name(item)
        merged.append(user_pinned.get(name, item))
        seen.add(name)
    for item in current:
        name = _package_name(item)
        if name in managed_names or name in seen:
            continue
        merged.append(item)
        seen.add(name)
    return merged


def _merge_defaults(default: Any, current: Any) -> Any:
    """Fill missing defaults while preserving user/application state."""
    if isinstance(default, dict) and isinstance(current, dict):
        merged = copy.deepcopy(current)
        for key, default_value in default.items():
            if key not in merged:
                merged[key] = copy.deepcopy(default_value)
                continue
            current_value = merged[key]
            if (
                key == "translators"
                and isinstance(default_value, list)
                and isinstance(current_value, list)
            ):
                merged[key] = _merge_translators(default_value, current_value)
            elif (
                key == "packages"
                and isinstance(default_value, list)
                and isinstance(current_value, list)
            ):
                merged[key] = _merge_managed_requirements(
                    default_value, current_value
                )
            else:
                merged[key] = _merge_defaults(default_value, current_value)
        return merged
    return copy.deepcopy(current)


def _merge_translators(default_items: list, current_items: list) -> list:
    merged = copy.deepcopy(current_items)
    positions: dict[str, int] = {}
    for index, item in enumerate(merged):
        if isinstance(item, dict) and item.get("name"):
            positions[str(item["name"])] = index

    for default_item in default_items:
        if not isinstance(default_item, dict) or not default_item.get("name"):
            if default_item not in merged:
                merged.append(copy.deepcopy(default_item))
            continue
        name = str(default_item["name"])
        if name not in positions:
            positions[name] = len(merged)
            merged.append(copy.deepcopy(default_item))
            continue
        index = positions[name]
        if isinstance(merged[index], dict):
            merged[index] = _merge_defaults(default_item, merged[index])
    return merged


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def _backup_invalid(path: Path) -> Path:
    candidate = path.with_name(path.name + ".invalid.bak")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(path.name + f".invalid.{counter}.bak")
        counter += 1
    shutil.copy2(path, candidate)
    return candidate


def _migrate_json(active: Path, example: Path) -> None:
    with example.open("r", encoding="utf-8") as handle:
        defaults = json.load(handle)

    if not active.exists():
        _atomic_write_text(
            active,
            json.dumps(defaults, ensure_ascii=False, indent=4) + "\n",
        )
        print(f"🔍 [配置文件] 创建 {active.name}")
        return

    try:
        with active.open("r", encoding="utf-8") as handle:
            current = json.load(handle)
    except ValueError as exc:
        # [自研补丁 2026-09-03] 仅"解析失败"(JSONDecodeError 等 ValueError)
        # 才允许备份+恢复默认; OSError/PermissionError(文件被占用)等
        # 一律跳过迁移并告警, 绝不覆写用户配置
        backup = None
        try:
            backup = _backup_invalid(active)
        except Exception as bak_exc:
            print(f"⚠️ [配置文件] {active.name} 备份失败 ({bak_exc})，"
                  f"放弃恢复默认以保护用户配置。")
            return
        _atomic_write_text(
            active,
            json.dumps(defaults, ensure_ascii=False, indent=4) + "\n",
        )
        print(
            f"⚠️ [配置文件] {active.name} 无法解析 ({exc})；"
            f"已备份到 {backup.name} 并恢复默认配置。"
        )
        return
    except OSError as exc:
        print(f"⚠️ [配置文件] {active.name} 读取失败 ({exc})，"
              f"本次跳过迁移，保留现有文件不动。")
        return

    merged = _merge_defaults(defaults, current)
    if merged != current:
        _atomic_write_text(
            active,
            json.dumps(merged, ensure_ascii=False, indent=4) + "\n",
        )
        print(f"✅ [配置迁移] {active.name}: 已迁移托管默认并保留用户配置。")
    else:
        print(f"✅ [配置迁移] {active.name}: 无需修改。")


def _migrate_toml(active: Path, example: Path) -> None:
    defaults = toml.load(example)

    if not active.exists():
        _atomic_write_text(active, toml.dumps(defaults))
        print(f"🔍 [配置文件] 创建 {active.name}")
        return

    try:
        current = toml.load(active)
    except ValueError as exc:
        # [自研补丁 2026-09-03] 同 JSON 分支: 仅解析失败才恢复默认
        backup = None
        try:
            backup = _backup_invalid(active)
        except Exception as bak_exc:
            print(f"⚠️ [配置文件] {active.name} 备份失败 ({bak_exc})，"
                  f"放弃恢复默认以保护用户配置。")
            return
        _atomic_write_text(active, toml.dumps(defaults))
        print(
            f"⚠️ [配置文件] {active.name} 无法解析 ({exc})；"
            f"已备份到 {backup.name} 并恢复默认配置。"
        )
        return
    except OSError as exc:
        print(f"⚠️ [配置文件] {active.name} 读取失败 ({exc})，"
              f"本次跳过迁移，保留现有文件不动。")
        return

    merged = _merge_defaults(defaults, current)
    if merged != current:
        _atomic_write_text(active, toml.dumps(merged))
        print(f"✅ [配置迁移] {active.name}: 已补充新默认字段，保留现有用户值。")
    else:
        print(f"✅ [配置迁移] {active.name}: 无需修改。")


def migrate_config_file(active_path: str | os.PathLike[str]) -> None:
    active = Path(active_path)
    example = active.with_name(active.name + ".example")
    if not example.exists():
        if not active.exists():
            raise FileNotFoundError(f"缺少配置文件及模板: {active}")
        return

    if active.suffix.lower() == ".toml":
        _migrate_toml(active, example)
    elif active.suffix.lower() == ".json":
        _migrate_json(active, example)
    elif not active.exists():
        shutil.copy2(example, active)


def prepare_config_files(config_paths: dict[str, str]) -> None:
    print("🔍 [配置文件] 检查并迁移配置...")
    for path in config_paths.values():
        migrate_config_file(path)
    print("✅ [配置文件] 配置检查完成\n")
