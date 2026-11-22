#!/usr/bin/env python3
"""Tests for .github/workflows/sync-tracker.yml dispatch configuration."""

import shutil
import subprocess
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
    assert "dispatches" in script
    assert "getOctokit" not in script
    assert "ghp_" not in script and "github_pat_" not in script

    node = shutil.which("node")
    if node:
        js_check = (
            "const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor; "
            "const fs = require('fs'); "
            "const src = fs.readFileSync(0, 'utf8'); "
            "new AsyncFunction('require','__dirname','__filename','github','octokit','context','core','exec','glob','io', src);"
        )
        res = subprocess.run([node, "-e", js_check], input=script, text=True, capture_output=True)
        assert res.returncode == 0, f"github-script AsyncFunction syntax/shadowing error: {res.stderr}"
