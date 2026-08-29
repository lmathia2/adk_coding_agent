"""Local Magnitude inference discovery and composition generation."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import yaml

from harness.config import DEFAULT_COMPOSITION_PATH, parse_harness_composition

MAGNITUDE_API_KEY = "magnitude-local"
MAGNITUDE_BASE_URL = "http://127.0.0.1:10100/inference/v1"
MINIMUM_MAGNITUDE_VERSION = (0, 0, 8)
_MAGNITUDE_STARTUP_ATTEMPTS = 960


class MagnitudeConnectionError(RuntimeError):
    """Raised when the local Magnitude inference service cannot be prepared."""


@dataclass(frozen=True, slots=True)
class MagnitudeConnection:
    config_path: Path
    endpoint: str
    model_id: str


JsonFetcher = Callable[[str, float], object]
JsonPoster = Callable[[str, Mapping[str, object], float], object]
CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _fetch_json(url: str, timeout_seconds: float) -> object:
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            return json.load(response)
    except (OSError, URLError, ValueError) as error:
        raise MagnitudeConnectionError(f"Magnitude is not reachable at {url}: {error}") from error


def _post_json(
    url: str,
    payload: Mapping[str, object],
    timeout_seconds: float,
) -> object:
    request = Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return json.load(response)
    except (OSError, URLError, ValueError) as error:
        raise MagnitudeConnectionError(
            f"Magnitude model probe failed at {url}: {error}"
        ) from error


def probe_magnitude_model(
    *,
    endpoint: str,
    model_id: str,
    reasoning: str | None = None,
    timeout_seconds: float = 180,
    post_json: JsonPoster = _post_json,
) -> None:
    """Require one real completion before announcing a local model as responsive."""

    payload: dict[str, object] = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Reply OK"}],
        "max_tokens": 8,
        "stream": False,
    }
    if reasoning is not None:
        payload["reasoning_effort"] = reasoning
    response = post_json(
        f"{endpoint.rstrip('/')}/chat/completions",
        payload,
        timeout_seconds,
    )
    if not isinstance(response, Mapping):
        raise MagnitudeConnectionError("Magnitude model probe returned a non-object response")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise MagnitudeConnectionError("Magnitude model probe returned no completion choice")


def _model_ids(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, Mapping):
        raise MagnitudeConnectionError("Magnitude model discovery returned a non-object response")
    raw_models = payload.get("models", payload.get("data"))
    if not isinstance(raw_models, list):
        raise MagnitudeConnectionError("Magnitude model discovery response has no model list")
    model_ids: list[str] = []
    for entry in raw_models:
        value = entry.get("id") if isinstance(entry, Mapping) else entry
        if isinstance(value, str) and value.strip() and value not in model_ids:
            model_ids.append(value)
    if not model_ids:
        raise MagnitudeConnectionError(
            "Magnitude has no installed local model; run `magnitude setup` first"
        )
    return tuple(model_ids)


def _selected_model(state_path: Path) -> str | None:
    try:
        payload = json.loads(state_path.expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    slots = payload.get("slots")
    primary = slots.get("primary") if isinstance(slots, Mapping) else None
    selected = primary.get("providerModelId") if isinstance(primary, Mapping) else None
    return selected if isinstance(selected, str) and selected.strip() else None


def select_model_id(
    available: Sequence[str],
    *,
    requested: str | None,
    state_path: Path,
) -> str:
    """Choose an explicit model, then Magnitude's selected model, then its first model."""

    choices = tuple(dict.fromkeys(available))
    if requested is not None:
        if requested not in choices:
            raise MagnitudeConnectionError(
                f"Magnitude model {requested!r} is unavailable; available: {', '.join(choices)}"
            )
        return requested
    selected = _selected_model(state_path)
    if selected in choices:
        return selected
    return choices[0]


def _run_magnitude_server(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    normalized = value.strip().removeprefix("v").split("-", 1)[0]
    parts = normalized.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        return None
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def _require_compatible_magnitude(command_runner: CommandRunner) -> None:
    try:
        completed = command_runner(("magnitude", "--version"))
    except (OSError, subprocess.SubprocessError) as error:
        raise MagnitudeConnectionError(
            "Magnitude is required; run `./install.sh --magnitude`"
        ) from error
    version = _version_tuple(completed.stdout)
    if completed.returncode != 0 or version is None:
        raise MagnitudeConnectionError(
            "Magnitude's version could not be determined; run `./install.sh --magnitude`"
        )
    if version < MINIMUM_MAGNITUDE_VERSION:
        required = ".".join(str(part) for part in MINIMUM_MAGNITUDE_VERSION)
        raise MagnitudeConnectionError(
            f"Magnitude {required}+ is required for external harnesses; found "
            f"{completed.stdout.strip()}. Run `./install.sh --magnitude`"
        )


def _start_service(command_runner: CommandRunner) -> str | None:
    _require_compatible_magnitude(command_runner)
    try:
        completed = command_runner(("magnitude", "server", "start"))
    except (OSError, subprocess.SubprocessError) as error:
        raise MagnitudeConnectionError(
            "Magnitude is not running and could not be started; install or update "
            "it with `./install.sh --magnitude`, then run `magnitude setup`"
        ) from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return detail or f"exit status {completed.returncode}"
    return None


def _discover_models(
    endpoint: str,
    *,
    fetch_json: JsonFetcher,
    attempts: int = 20,
) -> tuple[str, ...]:
    url = f"{endpoint.rstrip('/')}/models"
    last_error: MagnitudeConnectionError | None = None
    for attempt in range(attempts):
        try:
            payload = fetch_json(url, 2.0)
        except MagnitudeConnectionError as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(0.25)
        else:
            return _model_ids(payload)
    assert last_error is not None
    raise last_error


def write_magnitude_config(
    destination: Path,
    *,
    endpoint: str,
    model_id: str,
    reasoning: str | None = None,
    template_path: Path = DEFAULT_COMPOSITION_PATH,
) -> Path:
    """Atomically write a validated local-model composition derived from the default."""

    payload = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    coding = payload["harness"]["config"]["models"]["coding"]
    coding.clear()
    coding.update(
        {
            "provider": "openai_compatible",
            "name": model_id,
            "base_url": endpoint.rstrip("/"),
            "api_key": {"env": "MAGNITUDE_API_KEY"},
            "retry": {
                "attempts": 3,
                "exponential_base": 2,
                "initial_delay_seconds": 1,
                "retry_statuses": [429, 500, 502, 503, 504],
            },
        }
    )
    if reasoning is not None:
        coding["reasoning"] = reasoning
    parse_harness_composition(payload)
    rendered = yaml.safe_dump(payload, sort_keys=False)
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
    return destination


def prepare_magnitude_connection(
    *,
    state_root: Path,
    endpoint: str = MAGNITUDE_BASE_URL,
    requested_model: str | None = None,
    reasoning: str | None = None,
    magnitude_state_path: Path | None = None,
    start_service: bool = True,
    fetch_json: JsonFetcher = _fetch_json,
    command_runner: CommandRunner = _run_magnitude_server,
) -> MagnitudeConnection:
    """Ensure Magnitude is reachable, discover a model, and write runtime config."""

    try:
        models = _discover_models(endpoint, fetch_json=fetch_json, attempts=1)
    except MagnitudeConnectionError:
        if not start_service:
            raise
        startup_error = _start_service(command_runner)
        try:
            models = _discover_models(
                endpoint,
                fetch_json=fetch_json,
                attempts=_MAGNITUDE_STARTUP_ATTEMPTS,
            )
        except MagnitudeConnectionError as error:
            if startup_error is None:
                raise
            raise MagnitudeConnectionError(
                "Magnitude did not become ready after `magnitude server start` reported: "
                f"{startup_error}. Run `./install.sh --magnitude`, then "
                "`magnitude setup` once"
            ) from error
    selected = select_model_id(
        models,
        requested=requested_model,
        state_path=magnitude_state_path or Path.home() / ".magnitude" / "state" / "models.json",
    )
    config_path = write_magnitude_config(
        state_root / "server" / "magnitude.yaml",
        endpoint=endpoint,
        model_id=selected,
        reasoning=reasoning,
    )
    return MagnitudeConnection(
        config_path=config_path,
        endpoint=endpoint.rstrip("/"),
        model_id=selected,
    )


__all__ = [
    "MAGNITUDE_API_KEY",
    "MAGNITUDE_BASE_URL",
    "MINIMUM_MAGNITUDE_VERSION",
    "MagnitudeConnection",
    "MagnitudeConnectionError",
    "prepare_magnitude_connection",
    "probe_magnitude_model",
    "select_model_id",
    "write_magnitude_config",
]
