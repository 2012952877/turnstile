import subprocess
import sys
import typing
from inspect import signature
from pathlib import Path
from typing import get_args, get_origin

import azure.functions as func

from functions.telemetry.function_app import app, process_usage_events, telemetry_health

ROOT = Path(__file__).resolve().parents[3]


def test_metadata_indexing_does_not_import_backend_modules() -> None:
    script = """
import builtins

original_import = builtins.__import__

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "backend" or name.startswith("backend."):
        raise AssertionError(f"backend import during metadata indexing: {name}")
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded_import
from functions.telemetry import function_app

assert len(function_app.app.get_functions()) == 4
"""

    subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_trigger_binding_names_match_function_parameters() -> None:
    for function in app.get_functions():
        trigger = function.get_trigger()
        parameters = signature(function.get_user_function()).parameters

        assert trigger is not None
        assert trigger.name in parameters


def test_event_hub_batch_annotation_is_available_at_runtime() -> None:
    user_function = process_usage_events.build().get_user_function()
    annotation = signature(user_function).parameters["events"].annotation

    assert annotation == typing.List[func.EventHubEvent]  # noqa: UP006
    assert get_origin(annotation) is list
    assert get_args(annotation) == (func.EventHubEvent,)


def test_telemetry_health_proves_the_python_worker_is_running() -> None:
    response = telemetry_health.build().get_user_function()(None)

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.get_body() == b'{"status": "ok"}'
