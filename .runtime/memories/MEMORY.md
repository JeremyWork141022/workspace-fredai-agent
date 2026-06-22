Workspace FredAI Agent identity: this agent serves an internal workspace API, keeps durable session and memory context, and uses FredAI as the only model gateway.
---ENTRY---
Memory policy: MEMORY.md is for stable agent operating rules and retrieval policy. USER.md is for stable user preferences and profile facts. Raw logs, large documents, repeated task data, and bulky notes belong in SQLite workspace notes or conversation history.
---ENTRY---
Retrieval policy: use curated memory as always-on guidance, automatic prefetch as temporary turn context, workspace_note_search for durable workspace facts, and session_search for older conversation details outside the recent context window.