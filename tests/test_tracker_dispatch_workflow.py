#!/usr/bin/env python3
"""Tests for .github/workflows/sync-tracker.yml dispatch configuration."""

from pathlib import Path
import yaml


WORKFLOW_PATH = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "sync-tracker.yml"


def load_workflow():
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_sync_tracker_triggers_and_permissions():
    wf = load_workflow()
    triggers = wf.get("on") or wf.get(True)
    assert "issues" in triggers
    assert set(triggers["issues"]["types"]) >= {
        "opened",
        "edited",
        "deleted",
        "closed",
        "reopened",
        "labeled",
        "unlabeled",
    }
    assert "workflow_dispatch" in triggers
    assert wf["permissions"] == {"contents": "read"}
    assert wf["concurrency"]["group"] == "sync-project-nox-tracker"
    assert wf["concurrency"]["cancel-in-progress"] is False


def test_sync_tracker_job_filters_prs_and_uses_secret():
    wf = load_workflow()
    job = wf["jobs"]["dispatch-tracker-update"]
    assert "!github.event.issue.pull_request" in job["if"]

    step = job["steps"][0]
    assert step["env"]["TRACKER_DISPATCH_TOKEN"] == "${{ secrets.TRACKER_DISPATCH_TOKEN }}"
    script = step["with"]["script"]
    assert "ProjectNox-org" in script
    assert "project-nox-tracker" in script
    assert "issues-updated" in script
    assert "createDispatchEvent" in script
    assert "ghp_" not in script and "github_pat_" not in script
