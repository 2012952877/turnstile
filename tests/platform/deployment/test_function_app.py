import typing
from inspect import signature
from typing import get_args, get_origin

import azure.functions as func

from functions.telemetry.function_app import process_usage_events


def test_event_hub_batch_annotation_is_available_at_runtime() -> None:
    user_function = process_usage_events.build().get_user_function()
    annotation = signature(user_function).parameters["events"].annotation

    assert annotation == typing.List[func.EventHubEvent]  # noqa: UP006
    assert get_origin(annotation) is list
    assert get_args(annotation) == (func.EventHubEvent,)