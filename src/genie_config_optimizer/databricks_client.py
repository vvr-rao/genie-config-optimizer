from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests


TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"}


class GenieAPIError(RuntimeError):
    pass


@dataclass
class AskResult:
    conversation_id: str
    message_id: str
    status: str
    message: dict[str, Any]
    sql: str | None
    query_description: str | None
    text_response: str | None
    rows: list[list[Any]] | None


class GenieClient:
    def __init__(self, host: str, token: str, *, timeout: float = 30.0):
        self.host = host.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"{self.host}{path}"

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        resp = self.session.request(method, self._url(path), timeout=self.timeout, **kwargs)
        if not resp.ok:
            raise GenieAPIError(
                f"{method} {path} -> {resp.status_code}: {resp.text[:500]}"
            )
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    def get_space(self, space_id: str, *, include_serialized: bool = True) -> dict[str, Any]:
        params = {"include_serialized_space": "true"} if include_serialized else {}
        return self._request("GET", f"/api/2.0/genie/spaces/{space_id}", params=params)

    def update_space(self, space_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/api/2.0/genie/spaces/{space_id}", data=json.dumps(body))

    def start_conversation(self, space_id: str, content: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/2.0/genie/spaces/{space_id}/start-conversation",
            data=json.dumps({"content": content}),
        )

    def get_message(self, space_id: str, conversation_id: str, message_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages/{message_id}",
        )

    def get_attachment_query_result(
        self, space_id: str, conversation_id: str, message_id: str, attachment_id: str
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}"
            f"/messages/{message_id}/attachments/{attachment_id}/query-result",
        )

    def poll_until_done(
        self,
        space_id: str,
        conversation_id: str,
        message_id: str,
        *,
        poll_interval: float = 5.0,
        max_wait: float = 600.0,
        on_poll: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        start = time.monotonic()
        while True:
            msg = self.get_message(space_id, conversation_id, message_id)
            status = msg.get("status", "")
            if on_poll is not None:
                on_poll(status)
            if status in TERMINAL_STATUSES:
                return msg
            if time.monotonic() - start > max_wait:
                raise GenieAPIError(
                    f"Timed out waiting for message {message_id} (last status: {status})"
                )
            time.sleep(poll_interval)

    def ask(
        self,
        space_id: str,
        question: str,
        *,
        poll_interval: float = 5.0,
        max_wait: float = 600.0,
        on_poll: Callable[[str], None] | None = None,
    ) -> AskResult:
        started = self.start_conversation(space_id, question)
        conversation_id = started["conversation_id"]
        message_id = started["message_id"]
        message = self.poll_until_done(
            space_id,
            conversation_id,
            message_id,
            poll_interval=poll_interval,
            max_wait=max_wait,
            on_poll=on_poll,
        )

        sql: str | None = None
        query_description: str | None = None
        text_response: str | None = None
        rows: list[list[Any]] | None = None

        for att in message.get("attachments", []) or []:
            query = att.get("query")
            if query:
                sql = sql or query.get("query")
                query_description = query_description or query.get("description")
                if att.get("attachment_id"):
                    try:
                        result = self.get_attachment_query_result(
                            space_id, conversation_id, message_id, att["attachment_id"]
                        )
                        sr = result.get("statement_response", {}) or {}
                        data_array = (
                            (sr.get("result", {}) or {}).get("data_array")
                            if sr
                            else None
                        )
                        if data_array is not None:
                            rows = data_array
                    except GenieAPIError:
                        pass
            text = att.get("text")
            if text and isinstance(text, dict):
                text_response = text_response or text.get("content")

        return AskResult(
            conversation_id=conversation_id,
            message_id=message_id,
            status=message.get("status", ""),
            message=message,
            sql=sql,
            query_description=query_description,
            text_response=text_response,
            rows=rows,
        )
