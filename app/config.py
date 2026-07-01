from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv


DEFAULT_MODEL = "azure-openai-chat"
DEFAULT_MAX_AGENT_ITERATIONS = 4
DEFAULT_SESSION_CONTEXT_MESSAGES = 16
DEFAULT_MEMORY_CHAR_LIMIT = 2800
DEFAULT_SESSION_SEARCH_LIMIT = 3
DEFAULT_KNOWLEDGE_PREFETCH_ENABLED = True

DEFAULT_SYSTEM_PROMPT = """You are the CRT Cost Agent.

You receive requests through an internal workspace API. Your job is to help users
understand, validate, and eventually dashboard CRT Cost data. The initial target
process is a deal-level database where each row is a deal with CRT Cost and
related features such as UPB, payoff date, settle year, deal type, and derived
calculation columns. Answer clearly, use available tools when useful, preserve
durable memory carefully, and help users continue work across sessions.

Boundaries:
- Use FredAI as the only model API boundary.
- Do not claim a memory, note, routine, or search result was saved unless a tool result confirms it.
- Separate facts found in workspace memory/history from your own inference.
- Ask concise follow-up questions when important facts are missing.
- Keep answers practical and focused on the user's immediate workspace task.
"""

FREDAI_PRESET_URLS = {
    "Proxy_Azure": "http://localhost:3003/v1/",
    "Local_Azure": "http://localhost:3000/fredai-orchestration-service/api",
    "Direct_Azure": "https://apigee-prod.itp01.p.fhlmc.com/genpop-virtual-expert/api/user/",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_dotenv() -> None:
    root_env = project_root() / ".env"
    if root_env.exists():
        load_dotenv(root_env)
    else:
        load_dotenv()


def app_home() -> Path:
    raw = os.getenv("WORKSPACE_AGENT_HOME", "").strip()
    return Path(raw).expanduser().resolve() if raw else project_root() / ".runtime"


def state_db_path() -> Path:
    raw = os.getenv("WORKSPACE_AGENT_STATE_DB", "").strip()
    return Path(raw).expanduser().resolve() if raw else app_home() / "state.sqlite3"


def memory_dir() -> Path:
    raw = os.getenv("WORKSPACE_AGENT_MEMORY_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else app_home() / "memories"


def workspace_root() -> Path:
    raw = os.getenv("WORKSPACE_AGENT_ROOT", "").strip()
    return Path(raw).expanduser().resolve() if raw else project_root()


def env_path() -> Path:
    return project_root() / ".env"


def ensure_dirs() -> None:
    for path in (app_home(), memory_dir(), app_home() / "cache"):
        path.mkdir(parents=True, exist_ok=True)


def _parse_env_line(line: str) -> Optional[tuple[str, str]]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def load_env_file(path: Optional[Path] = None) -> Dict[str, str]:
    path = path or env_path()
    if not path.exists():
        return {}
    values: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if parsed:
            values[parsed[0]] = parsed[1]
    return values


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1.0, float(raw))
    except ValueError:
        return default


def _fredai_base_url() -> str:
    preset = os.getenv("FREDAI_PRESET", "Direct_Azure").strip() or "Direct_Azure"
    override_by_preset = {
        "Proxy_Azure": os.getenv("FREDAI_PROXY_URL", "").strip(),
        "Local_Azure": os.getenv("FREDAI_LOCAL_URL", "").strip(),
        "Direct_Azure": os.getenv("FREDAI_DIRECT_URL", "").strip(),
    }
    explicit = os.getenv("FREDAI_BASE_URL", "").strip()
    url = explicit or override_by_preset.get(preset) or FREDAI_PRESET_URLS.get(preset)
    if not url:
        raise ValueError(f"Unknown FredAI preset: {preset}")
    return url.rstrip("/")


@dataclass(frozen=True)
class AppConfig:
    model: str
    fredai_base_url: str
    fredai_stream: bool
    fredai_verify_ssl: bool
    fredai_timeout_seconds: float
    fredai_oauth_url: str
    fredai_client_id: str
    fredai_client_secret: str
    fredai_oauth_username: str
    fredai_oauth_password_b64: str
    fredai_jwt_token: str
    max_agent_iterations: int
    session_context_messages: int
    scheduler_enabled: bool
    memory_char_limit: int
    memory_prefetch_enabled: bool
    session_search_aux_enabled: bool
    session_search_limit: int
    trace_enabled: bool
    trace_full_media: bool
    delivery_url: str
    knowledge_prefetch_enabled: bool = DEFAULT_KNOWLEDGE_PREFETCH_ENABLED
    system_prompt: str = DEFAULT_SYSTEM_PROMPT


def load_config() -> AppConfig:
    _read_dotenv()
    ensure_dirs()
    return AppConfig(
        model=os.getenv("FREDAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        fredai_base_url=_fredai_base_url(),
        fredai_stream=_bool_env("FREDAI_STREAM", True),
        fredai_verify_ssl=_bool_env("FREDAI_VERIFY_SSL", False),
        fredai_timeout_seconds=_float_env("FREDAI_TIMEOUT_SECONDS", 120.0),
        fredai_oauth_url=os.getenv("FREDAI_OAUTH_URL", "https://auth.fhlmc.com/as/token.oauth2").strip(),
        fredai_client_id=os.getenv("FREDAI_CLIENT_ID", "").strip(),
        fredai_client_secret=os.getenv("FREDAI_CLIENT_SECRET", "").strip(),
        fredai_oauth_username=os.getenv("FREDAI_OAUTH_USERNAME", "").strip(),
        fredai_oauth_password_b64=os.getenv("FREDAI_OAUTH_PASSWORD_B64", "").strip(),
        fredai_jwt_token=os.getenv("FREDAI_JWT_TOKEN", "").strip(),
        max_agent_iterations=_int_env("WORKSPACE_AGENT_MAX_AGENT_ITERATIONS", DEFAULT_MAX_AGENT_ITERATIONS),
        session_context_messages=_int_env("WORKSPACE_AGENT_SESSION_CONTEXT_MESSAGES", DEFAULT_SESSION_CONTEXT_MESSAGES),
        scheduler_enabled=_bool_env("WORKSPACE_AGENT_SCHEDULER_ENABLED", True),
        memory_char_limit=_int_env("WORKSPACE_AGENT_MEMORY_CHAR_LIMIT", DEFAULT_MEMORY_CHAR_LIMIT),
        memory_prefetch_enabled=_bool_env("WORKSPACE_AGENT_MEMORY_PREFETCH_ENABLED", True),
        session_search_aux_enabled=_bool_env("WORKSPACE_AGENT_SESSION_SEARCH_AUX_ENABLED", True),
        session_search_limit=_int_env("WORKSPACE_AGENT_SESSION_SEARCH_LIMIT", DEFAULT_SESSION_SEARCH_LIMIT),
        trace_enabled=_bool_env("WORKSPACE_AGENT_TRACE_ENABLED", True),
        trace_full_media=_bool_env("WORKSPACE_AGENT_TRACE_FULL_MEDIA", False),
        delivery_url=os.getenv("WORKSPACE_AGENT_DELIVERY_URL", "").strip(),
        knowledge_prefetch_enabled=_bool_env(
            "WORKSPACE_AGENT_KNOWLEDGE_PREFETCH_ENABLED",
            DEFAULT_KNOWLEDGE_PREFETCH_ENABLED,
        ),
    )

