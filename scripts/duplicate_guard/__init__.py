# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Project Nox Contributors & Awerkori

"""Nox Duplicate Guard — automated duplicate prevention, detection, and reconciliation."""

from .actions import GuardActionExecutor
from .config import load_config
from .matcher import DuplicateMatcher
from .models import (
    ActionResult,
    CandidateEvaluation,
    GuardConfig,
    GuardDecision,
    NormalizedUrl,
    ParsedIssue,
    RawIssue,
)
from .parser import parse_issue

__all__ = [
    "ActionResult",
    "CandidateEvaluation",
    "DuplicateMatcher",
    "GuardActionExecutor",
    "GuardConfig",
    "GuardDecision",
    "NormalizedUrl",
    "ParsedIssue",
    "RawIssue",
    "load_config",
    "parse_issue",
]
