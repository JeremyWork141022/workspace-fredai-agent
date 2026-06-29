from __future__ import annotations

import argparse
import asyncio
import json

from app.config import load_config
from app.orchestrator import WorkspaceAgentOrchestrator


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Run one CRT Analytics Agent turn.")
    parser.add_argument("message", nargs="+", help="User message")
    parser.add_argument("--workspace-id", default="local")
    parser.add_argument("--user-id", default="cli")
    parser.add_argument("--session-id", default="")
    args = parser.parse_args()

    orchestrator = WorkspaceAgentOrchestrator(load_config())
    result = await orchestrator.respond(
        workspace_id=args.workspace_id,
        user_id=args.user_id,
        session_id=args.session_id or None,
        message=" ".join(args.message),
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
