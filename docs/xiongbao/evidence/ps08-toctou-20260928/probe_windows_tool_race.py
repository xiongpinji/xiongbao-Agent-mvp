"""Diagnostic only: real Harness graph, local scripted model, synthetic Temp data."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import threading
import traceback
from unittest.mock import patch

REPO = Path(os.environ["QA_REPO"]).resolve()
OUTPUT = Path(os.environ["QA_OUTPUT"]).resolve()
EXPECTED = os.environ["QA_EXPECTED_SOURCE"]
assert os.name == "nt", "This probe only classifies Windows NTFS junction behavior"
assert OUTPUT.is_relative_to(REPO.parent / "output")
OUTPUT.mkdir(parents=True, exist_ok=True)
sys.path[:0] = [str(REPO), str(REPO / "src")]

import importlib.metadata  # noqa: E402
import octop  # noqa: E402
from deepagents.backends.filesystem import FilesystemBackend  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from octop.infra.agents.project_task_file_boundary import (  # noqa: E402
    PROJECT_TASK_FILE_TOOLS,
    ProjectTaskFileToolBoundaryMiddleware,
)
from octop.infra.projects import file_tasks  # noqa: E402
from tests.unit.agents.test_project_task_file_boundary import (  # noqa: E402
    _RecordingChatModel,
    _build_agent_at,
    _last_tool_message,
    _require_junction,
    _run_scripts,
    _run_thread,
)

SOURCE = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
assert SOURCE == EXPECTED
assert Path(octop.__file__).resolve() == REPO / "src" / "octop" / "__init__.py"
assert file_tasks.PROJECT_TASK_FILES_MODE_ENABLED is False
BASE = Path(tempfile.mkdtemp(prefix="xiongbao-030-toctou-")).resolve()
assert BASE.parent == Path(tempfile.gettempdir()).resolve()
assert BASE.name.startswith("xiongbao-030-toctou-")
INSIDE = "XIONGBAO_SYNTHETIC_INSIDE"
CANARY = "XIONGBAO_SYNTHETIC_OUTSIDE_CANARY"
WRITE = "XIONGBAO_SYNTHETIC_OUTSIDE_WRITE"
report = {
    "source": SOURCE,
    "repo": str(REPO),
    "octop_module": octop.__file__,
    "platform": platform.platform(),
    "python": sys.version.split()[0],
    "deepagents_version": importlib.metadata.version("deepagents"),
    "filesystem_module": inspect.getfile(FilesystemBackend),
    "source_files_enabled": file_tasks.PROJECT_TASK_FILES_MODE_ENABLED,
    "fixture_files_enabled": False,
    "temp_base": str(BASE),
    "provider": "local-recording-only",
    "external_model_requests": 0,
    "cases": [],
    "outcome": "RUNNING",
}
real_veto = ProjectTaskFileToolBoundaryMiddleware._path_veto
real_open = os.open


def lexical(value):
    try:
        return os.path.normcase(os.path.abspath(os.fsdecode(value)))
    except TypeError:
        return None


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_temp(path):
    resolved = path.resolve()
    assert resolved.is_relative_to(BASE), f"Computed target outside fixture: {path}"
    return resolved


def plant(child, outside):
    assert_temp(child.parent)
    assert_temp(outside)
    assert not os.path.lexists(child)
    _require_junction(child, outside)
    assert child.is_junction() and child.resolve() == outside.resolve()


async def run_case(label, tool_name, stable):
    case = BASE / label
    child = case / "managed" / "child"
    root = child.parent
    saved = root / "child_saved"
    outside = case / "outside"
    child.mkdir(parents=True)
    outside.mkdir()
    (child / "target.txt").write_text(INSIDE, encoding="utf-8")
    canary = outside / "target.txt"
    canary.write_text(CANARY, encoding="utf-8")
    canary_before = digest(canary)
    read = tool_name == "read_file"
    race_path = "child/target.txt" if read else "child/new.txt"
    target = child / ("target.txt" if read else "new.txt")
    baseline_args = (
        {"file_path": "child/target.txt"} if read
        else {"file_path": "child/baseline.txt", "content": INSIDE}
    )
    target_args = {"file_path": race_path} if read else {"file_path": race_path, "content": WRITE}
    model = _RecordingChatModel(scripts=[
        *_run_scripts(tool_name, baseline_args, call_id=label + "_clean"),
        *_run_scripts(tool_name, target_args, call_id=label + "_target"),
    ])
    agent = _build_agent_at(root, model, checkpointer=MemorySaver())
    state = {
        "label": label, "tool": tool_name, "stable_plant": stable,
        "baseline_pass": False, "guard_started": 0, "guard_passed": 0,
        "guard_refused": 0, "target_open_hits": 0,
        "swap_fired": False, "junction_installed": False,
        "tool_status": None, "outside_canary_in_result": False,
        "outside_write_observed": False, "events": [],
        "classification": "INCONCLUSIVE",
        "exception": None, "close_error": None, "restore_error": None,
    }
    pass_event = threading.Event()
    expected_target = lexical(target)
    expected_root = lexical(root)

    def traced_veto(self, checked_root, name, args):
        matches = (
            lexical(checked_root) == expected_root
            and name == tool_name and args.get("file_path") == race_path
        )
        if matches:
            state["guard_started"] += 1
            state["events"].append({"event": "guard_started"})
        try:
            result = real_veto(self, checked_root, name, args)
        except Exception:
            if matches:
                state["guard_refused"] += 1
                state["events"].append({"event": "guard_refused"})
            raise
        if matches:
            state["guard_passed"] += 1
            pass_event.set()
            state["events"].append({"event": "guard_passed"})
        return result

    def one_open(path, flags, *args, **kwargs):
        if lexical(path) != expected_target:
            return real_open(path, flags, *args, **kwargs)
        state["target_open_hits"] += 1
        assert pass_event.is_set(), "Target open reached before guard passed"
        state["events"].append({
            "event": "target_open",
            "flags": int(flags),
            "stack": [
                {"file": frame.filename, "line": frame.lineno, "name": frame.name}
                for frame in traceback.extract_stack(limit=12)[:-1]
            ],
        })
        if not stable and not state["swap_fired"]:
            state["swap_fired"] = True
            assert_temp(child)
            assert_temp(saved)
            assert not child.is_junction() and not os.path.lexists(saved)
            child.rename(saved)
            plant(child, outside)
            state["junction_installed"] = True
            state["events"].append({"event": "junction_installed", "target": str(outside)})
        return real_open(path, flags, *args, **kwargs)

    try:
        baseline = await _run_thread(agent, label)
        clean = _last_tool_message(baseline)
        assert clean.status != "error"
        if read:
            assert INSIDE in str(clean.content)
        else:
            assert (child / "baseline.txt").read_text(encoding="utf-8") == INSIDE
        state["baseline_pass"] = True

        if stable:
            assert_temp(child)
            assert_temp(saved)
            child.rename(saved)
            plant(child, outside)
            state["junction_installed"] = True

        with (
            patch.object(ProjectTaskFileToolBoundaryMiddleware, "_path_veto", traced_veto),
            patch.object(os, "open", one_open),
        ):
            result = await _run_thread(agent, label)
            message = _last_tool_message(result)
        state["tool_status"] = message.status
        state["outside_canary_in_result"] = CANARY in str(message.content)
        written = outside / "new.txt"
        state["outside_write_observed"] = (
            written.is_file() and written.read_text(encoding="utf-8") == WRITE
        )
        assert digest(canary) == canary_before
        state["outside_canary_unchanged"] = True
        state["outside_names"] = sorted(p.name for p in outside.iterdir())
        state["local_model_invocations"] = len(model.invocations)
        state["model_tool_sets"] = model.invocations
        assert all(set(names) == PROJECT_TASK_FILE_TOOLS for names in model.invocations)

        if stable:
            assert state["guard_refused"] == 1 and state["guard_passed"] == 0
            assert state["target_open_hits"] == 0 and message.status == "error"
            assert not state["outside_canary_in_result"] and not state["outside_write_observed"]
            assert state["outside_names"] == ["target.txt"]
            state["classification"] = "STABLE_PLANT_REFUSED"
        else:
            assert state["guard_passed"] == 1 and state["target_open_hits"] == 1
            assert state["junction_installed"] and state["swap_fired"]
            escape = state["outside_canary_in_result"] if read else state["outside_write_observed"]
            if escape:
                assert message.status != "error"
                state["classification"] = "CHECK_USE_ESCAPE_CONFIRMED"
            elif message.status == "error":
                state["classification"] = "SWAPPED_THEN_REFUSED"
            else:
                raise AssertionError("Successful tool result without expected synthetic marker")
    except Exception as error:
        state["exception"] = type(error).__name__ + ": " + str(error)
        state["classification"] = "INCONCLUSIVE"
    finally:
        try:
            await agent.aclose()
        except Exception as error:
            state["close_error"] = type(error).__name__ + ": " + str(error)
        try:
            assert_temp(root)
            assert_temp(outside)
            if child.is_junction():
                assert child.resolve() == outside.resolve()
                child.rmdir()  # Removes only our junction, never its target.
            if saved.exists():
                assert_temp(saved)
                assert not os.path.lexists(child)
                saved.rename(child)
            assert child.is_dir() and not child.is_junction()
            assert (child / "target.txt").read_text(encoding="utf-8") == INSIDE
            assert digest(canary) == canary_before
            state["child_restored"] = True
            state["outside_preserved"] = True
        except Exception as error:
            state["restore_error"] = type(error).__name__ + ": " + str(error)
            state["child_restored"] = False
        if state["close_error"] or state["restore_error"]:
            state["classification"] = "INCONCLUSIVE"
        state["patches_restored"] = (
            os.open is real_open and ProjectTaskFileToolBoundaryMiddleware._path_veto is real_veto
        )
        report["cases"].append(state)
        (OUTPUT / "tool-race-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({
            key: state.get(key) for key in [
                "label", "classification", "baseline_pass", "guard_passed",
                "guard_refused", "target_open_hits", "outside_canary_in_result",
                "outside_write_observed", "child_restored", "exception", "close_error"
            ]
        }), flush=True)


async def main():
    for label, tool, stable in [
        ("stable-read", "read_file", True),
        ("race-read", "read_file", False),
        ("stable-write", "write_file", True),
        ("race-write", "write_file", False),
    ]:
        await run_case(label, tool, stable)
    assert file_tasks.PROJECT_TASK_FILES_MODE_ENABLED is False
    report["local_model_invocations"] = sum(c.get("local_model_invocations", 0) for c in report["cases"])
    if any(c["classification"] == "INCONCLUSIVE" for c in report["cases"]):
        report["outcome"] = "INCONCLUSIVE"
        return 2
    report["outcome"] = (
        "COMPLETE_WITH_CHECK_USE_ESCAPE"
        if any(c["classification"] == "CHECK_USE_ESCAPE_CONFIRMED" for c in report["cases"])
        else "COMPLETE_NO_ESCAPE_OBSERVED"
    )
    return 0


try:
    exit_code = asyncio.run(main())
except Exception:
    report["outcome"] = "HARNESS_FAILED"
    report["fatal_error"] = traceback.format_exc()
    exit_code = 2
finally:
    report["temp_retained"] = True
    report["recursive_cleanup_performed"] = False
    (OUTPUT / "tool-race-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({
    "source": SOURCE, "outcome": report["outcome"], "source_files_enabled": False,
    "temp_base": str(BASE), "output": str(OUTPUT),
    "local_model_invocations": report.get("local_model_invocations"),
    "external_model_requests": 0,
}), flush=True)
sys.exit(exit_code)
