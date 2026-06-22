from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

import httpx

from app.config import AppConfig
from app.fredai_auth import FredAIAuth, FredAIAuthError


class FredAIClientError(RuntimeError):
    """Raised when the FredAI chat-completions call fails."""


@dataclass
class ChatCompletionResult:
    message: Dict[str, Any]
    raw_response: Dict[str, Any]
    event_count: int = 0
    text_chunks: List[str] = field(default_factory=list)


class FredAIClient:
    """Small OpenAI-compatible chat-completions client pointed only at FredAI."""

    def __init__(self, config: AppConfig, auth: Optional[FredAIAuth] = None):
        self._config = config
        self._auth = auth or FredAIAuth(config)

    def create_chat_completion(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        stream: Optional[bool] = None,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> ChatCompletionResult:
        use_stream = self._config.fredai_stream if stream is None else stream
        payload: Dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": use_stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format

        if use_stream:
            try:
                return self._stream_chat_completion(payload)
            except FredAIAuthError:
                raise
            except FredAIClientError:
                raise
            except Exception as exc:
                raise FredAIClientError(f"FredAI streaming call failed: {exc}") from exc

        return self._non_stream_chat_completion(payload)

    def _headers(self) -> Dict[str, str]:
        token = self._auth.token().access_token
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        jwt = self._auth.jwt_token()
        if jwt:
            headers["x-jwt-token"] = jwt
        return headers

    def _chat_url(self) -> str:
        return f"{self._config.fredai_base_url.rstrip('/')}/chat/completions"

    def _non_stream_chat_completion(self, payload: Dict[str, Any]) -> ChatCompletionResult:
        try:
            with httpx.Client(
                verify=self._config.fredai_verify_ssl,
                timeout=self._config.fredai_timeout_seconds,
            ) as client:
                response = client.post(self._chat_url(), headers=self._headers(), json=payload)
            response.raise_for_status()
            data = response.json()
        except FredAIAuthError:
            raise
        except Exception as exc:
            raise FredAIClientError(f"FredAI chat completion failed: {exc}") from exc
        return ChatCompletionResult(message=self._first_message(data), raw_response=data, event_count=1)

    def _stream_chat_completion(self, payload: Dict[str, Any]) -> ChatCompletionResult:
        chunks: List[Dict[str, Any]] = []
        event_count = 0
        with httpx.Client(
            verify=self._config.fredai_verify_ssl,
            timeout=self._config.fredai_timeout_seconds,
        ) as client:
            with client.stream("POST", self._chat_url(), headers=self._headers(), json=payload) as response:
                response.raise_for_status()
                for event in self._iter_sse_json(response.iter_lines()):
                    event_count += 1
                    chunks.append(event)
        message, text_chunks = self._message_from_stream_chunks(chunks)
        return ChatCompletionResult(
            message=message,
            raw_response={"stream": True, "chunks": chunks, "message": message},
            event_count=event_count,
            text_chunks=text_chunks,
        )

    @staticmethod
    def _iter_sse_json(lines: Iterable[str]) -> Iterable[Dict[str, Any]]:
        data_parts: List[str] = []
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                if data_parts:
                    data = "\n".join(data_parts).strip()
                    data_parts.clear()
                    if data and data != "[DONE]":
                        yield json.loads(data)
                continue
            if line.startswith("data:"):
                data_parts.append(line[5:].strip())
        if data_parts:
            data = "\n".join(data_parts).strip()
            if data and data != "[DONE]":
                yield json.loads(data)

    @staticmethod
    def _first_message(data: Dict[str, Any]) -> Dict[str, Any]:
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            return {"role": "assistant", "content": ""}
        message = choices[0].get("message") or {}
        if not isinstance(message, dict):
            return {"role": "assistant", "content": str(message)}
        message.setdefault("role", "assistant")
        message.setdefault("content", "")
        return message

    @classmethod
    def _message_from_stream_chunks(cls, chunks: List[Dict[str, Any]]) -> tuple[Dict[str, Any], List[str]]:
        content_parts: List[str] = []
        role = "assistant"
        tool_calls_by_index: Dict[int, Dict[str, Any]] = {}

        for chunk in chunks:
            for choice in chunk.get("choices", []) or []:
                delta = choice.get("delta") or {}
                if not isinstance(delta, dict):
                    continue
                if delta.get("role"):
                    role = str(delta["role"])
                if delta.get("content"):
                    content_parts.append(str(delta["content"]))
                for call_delta in delta.get("tool_calls", []) or []:
                    if not isinstance(call_delta, dict):
                        continue
                    index = int(call_delta.get("index", len(tool_calls_by_index)))
                    current = tool_calls_by_index.setdefault(
                        index,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if call_delta.get("id"):
                        current["id"] += str(call_delta["id"]) if current["id"] else str(call_delta["id"])
                    if call_delta.get("type"):
                        current["type"] = str(call_delta["type"])
                    fn_delta = call_delta.get("function") or {}
                    if isinstance(fn_delta, dict):
                        if fn_delta.get("name"):
                            current["function"]["name"] += str(fn_delta["name"])
                        if fn_delta.get("arguments"):
                            current["function"]["arguments"] += str(fn_delta["arguments"])

        message: Dict[str, Any] = {"role": role, "content": "".join(content_parts)}
        if tool_calls_by_index:
            message["tool_calls"] = [tool_calls_by_index[index] for index in sorted(tool_calls_by_index)]
        return message, content_parts
