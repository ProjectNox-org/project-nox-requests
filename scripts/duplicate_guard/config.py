# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Configuration loader with safe environment overrides and conservative fallback."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import ALL_CATEGORIES, GuardConfig, ThresholdsConfig

DEFAULT_CONFIG_PATH = Path(".github/duplicate-guard.json")


def _parse_bool_env(name: str) -> bool | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    val = raw.strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return None


def load_config(config_path: Path | str | None = None) -> GuardConfig:
    """Load configuration JSON and apply environment variable overrides.

    If the configuration file is malformed, returns a conservative fail-safe
    configuration with auto-close disabled and dry-run enabled.
    """
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    data: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("Configuration root must be a JSON object")
            data = loaded
        except Exception:
            return GuardConfig(
                enabled=True,
                auto_close_enabled=False,
                dry_run=True,
            )

    t_raw = data.get("thresholds") if isinstance(data.get("thresholds"), dict) else {}
    ne = t_raw.get("new_extension") if isinstance(t_raw.get("new_extension"), dict) else {}
    bg = t_raw.get("bug") if isinstance(t_raw.get("bug"), dict) else {}
    ds = t_raw.get("dead_source") if isinstance(t_raw.get("dead_source"), dict) else {}
    en = t_raw.get("enhancement") if isinstance(t_raw.get("enhancement"), dict) else {}

    thresholds = ThresholdsConfig(
        new_extension_confirmed_name_edit_ratio=float(ne.get("confirmed_name_edit_ratio", 0.72)),
        new_extension_possible_name_edit_ratio=float(ne.get("possible_name_edit_ratio", 0.88)),
        bug_confirmed_text_similarity=float(bg.get("confirmed_text_similarity", 0.78)),
        bug_confirmed_structured_text_similarity=float(bg.get("confirmed_structured_text_similarity", 0.30)),
        bug_possible_text_similarity=float(bg.get("possible_text_similarity", 0.50)),
        bug_possible_same_part_similarity=float(bg.get("possible_same_part_similarity", 0.25)),
        bug_min_description_chars_for_auto_close=int(bg.get("min_description_chars_for_auto_close", 15)),
        dead_source_confirmed_text_similarity=float(ds.get("confirmed_text_similarity", 0.55)),
        dead_source_possible_text_similarity=float(ds.get("possible_text_similarity", 0.25)),
        enhancement_confirmed_combined_similarity=float(en.get("confirmed_combined_similarity", 0.85)),
        enhancement_confirmed_summary_overlap=float(en.get("confirmed_summary_overlap", 0.70)),
        enhancement_possible_tfidf_similarity=float(en.get("possible_tfidf_similarity", 0.42)),
        enhancement_possible_token_overlap=float(en.get("possible_token_overlap", 0.30)),
    )

    cats_raw = data.get("auto_close_categories")
    if isinstance(cats_raw, list):
        auto_close_categories = frozenset(str(c) for c in cats_raw if str(c) in ALL_CATEGORIES)
    else:
        auto_close_categories = frozenset(ALL_CATEGORIES)

    enabled = bool(data.get("enabled", True))
    auto_close_enabled = bool(data.get("auto_close_enabled", True))
    dry_run = bool(data.get("dry_run", False))

    if (env_enabled := _parse_bool_env("NOX_DUPLICATE_GUARD_ENABLED")) is not None:
        enabled = env_enabled
    if (env_auto_close := _parse_bool_env("NOX_DUPLICATE_GUARD_AUTO_CLOSE")) is not None:
        auto_close_enabled = env_auto_close
    if (env_dry_run := _parse_bool_env("NOX_DUPLICATE_GUARD_DRY_RUN")) is not None:
        dry_run = env_dry_run

    return GuardConfig(
        enabled=enabled,
        auto_close_enabled=auto_close_enabled,
        dry_run=dry_run,
        reconcile_lookback_days=max(1, int(data.get("reconcile_lookback_days", 30))),
        batch_size=max(1, int(data.get("batch_size", 50))),
        max_candidates_in_comment=max(1, min(5, int(data.get("max_candidates_in_comment", 3)))),
        dead_source_window_days=max(1, int(data.get("dead_source_window_days", 30))),
        bug_regression_days=max(0, int(data.get("bug_regression_days", 3))),
        auto_close_categories=auto_close_categories,
        thresholds=thresholds,
    )
