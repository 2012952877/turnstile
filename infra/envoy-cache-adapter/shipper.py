from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

LOG_PATH = Path("/tmp/turnstile-envoy/usage.jsonl")
EVENT_HUB_RESOURCE = "https://eventhubs.azure.net/"


def _text(row: dict[str, Any], key: str, default: str = "unattributed") -> str:
    value = row.get(key)
    return str(value) if value not in (None, "", "-") else default


def _number(row: dict[str, Any], key: str) -> int:
    value = row.get(key)
    return int(float(value)) if value not in (None, "", "-") else 0


def normalize_access_row(row: dict[str, Any]) -> dict[str, Any] | None:
    request_id = _text(row, "request_id", "")
    if not request_id or _number(row, "response_code") >= 400:
        return None
    prompt_tokens = _number(row, "prompt_tokens")
    output_tokens = _number(row, "output_tokens")
    cache_read_tokens = _number(row, "cache_read_tokens")
    cache_write_tokens = _number(row, "cache_write_tokens")
    if prompt_tokens + output_tokens + cache_read_tokens + cache_write_tokens == 0:
        return None
    cached_tokens = cache_read_tokens + cache_write_tokens
    api_format = _text(row, "api_format", "openai_chat")
    input_tokens = (
        prompt_tokens
        if api_format == "anthropic_messages"
        else max(prompt_tokens - cached_tokens, 0)
    )
    return {
        "id": request_id,
        "request_id": request_id,
        "correlation_id": _text(row, "correlation_id", request_id),
        "ts": _text(row, "timestamp"),
        "team": _text(row, "department"),
        "organization": _text(row, "organization"),
        "organization_id": _text(row, "organization_id"),
        "department": _text(row, "department"),
        "department_id": _text(row, "department_id"),
        "project": _text(row, "project"),
        "project_id": _text(row, "project_id"),
        "user": _text(row, "user"),
        "user_id": _text(row, "user_id"),
        "agent": _text(row, "agent"),
        "agent_id": _text(row, "agent_id"),
        "workflow": _text(row, "workflow"),
        "run_id": _text(row, "run_id", request_id),
        "turn_index": max(_number(row, "turn_index"), 1),
        "provider": _text(row, "provider", "microsoft_foundry"),
        "model": _text(row, "model"),
        "model_id": _text(row, "model_id"),
        "runtime": _text(row, "runtime"),
        "request_source": _text(row, "request_source"),
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "cache_write_tokens": cache_write_tokens,
        "output_tokens": output_tokens,
        "latency_ms": _number(row, "latency_ms"),
        "status": str(_number(row, "response_code")),
        "status_code": _number(row, "response_code"),
        "estimated_cost": None,
        "error_message": None,
        "estimated": False,
        "ingest_source": "eventhub",
        "ingest_error": None,
        "budget_admission": None,
        "model_admission": None,
    }


class ManagedIdentityToken:
    def __init__(self) -> None:
        self._token = ""
        self._expires_on = 0

    def get(self) -> str:
        now = int(time.time())
        if self._token and self._expires_on - now > 300:
            return self._token
        endpoint = os.environ["IDENTITY_ENDPOINT"]
        separator = "&" if "?" in endpoint else "?"
        url = endpoint + separator + urlencode(
            {"resource": EVENT_HUB_RESOURCE, "api-version": "2019-08-01"}
        )
        request = Request(
            url,
            headers={"X-IDENTITY-HEADER": os.environ["IDENTITY_HEADER"]},
        )
        with urlopen(request, timeout=10) as response:
            payload = json.load(response)
        self._token = str(payload["access_token"])
        self._expires_on = int(payload["expires_on"])
        return self._token


class EventHubSender:
    def __init__(self) -> None:
        namespace = os.environ["EVENT_HUB_FQDN"].strip()
        event_hub = quote(os.environ["EVENT_HUB_NAME"].strip(), safe="")
        self._url = f"https://{namespace}/{event_hub}/messages"
        self._tokens = ManagedIdentityToken()

    def send(self, event: dict[str, Any]) -> None:
        body = json.dumps(event, separators=(",", ":")).encode("utf-8")
        request = Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._tokens.get()}",
                "Content-Type": "application/json; charset=utf-8",
                "BrokerProperties": json.dumps({"PartitionKey": event["id"]}),
            },
        )
        with urlopen(request, timeout=10) as response:
            if response.status not in (200, 201, 202):
                raise RuntimeError(f"Event Hub returned HTTP {response.status}")


def run() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.touch(exist_ok=True)
    sender = EventHubSender()
    with LOG_PATH.open("r", encoding="utf-8") as source:
        while True:
            position = source.tell()
            line = source.readline()
            if not line:
                time.sleep(0.05)
                continue
            if not line.endswith("\n"):
                source.seek(position)
                time.sleep(0.05)
                continue
            try:
                row = json.loads(line)
                event = normalize_access_row(row)
                if event is None:
                    continue
                while True:
                    try:
                        sender.send(event)
                        print(f"sent exact usage for {event['id']}", flush=True)
                        break
                    except Exception as error:  # noqa: BLE001
                        print(
                            f"usage send failed ({type(error).__name__}); retrying",
                            flush=True,
                        )
                        time.sleep(1)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                print(f"skipped malformed access row ({type(error).__name__})", flush=True)


if __name__ == "__main__":
    run()