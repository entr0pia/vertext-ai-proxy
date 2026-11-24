import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import time
from typing import Any

from vertext_ai_proxy.core.exceptions import OpenAIAPIException


@dataclass
class StoredResponseRecord:
    """持久化在内存中的单条 Responses 历史记录。"""

    response: dict[str, Any]
    gemini_contents: list[dict[str, Any]]
    function_call_names: dict[str, str] = field(default_factory=dict)
    expires_at: float = 0.0


@dataclass
class ActiveResponseHandle:
    """正在执行中的 Responses 生成任务取消句柄。"""

    response_id: str
    cancel_event: asyncio.Event
    partial_response: dict[str, Any]
    task: asyncio.Task[Any] | None = None
    store_on_cancel: bool = True


class ResponseStore:
    """有界 LRU + TTL 内存态 Responses 存储与活动任务取消注册表。"""

    def __init__(self, max_items: int = 500, ttl_seconds: int = 3600) -> None:
        self.max_items = max(1, max_items)
        self.ttl_seconds = max(1, ttl_seconds)
        self._records: OrderedDict[str, StoredResponseRecord] = OrderedDict()
        self._active: dict[str, ActiveResponseHandle] = {}

    def _evict_expired(self, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        expired_ids = [
            rid for rid, rec in self._records.items() if rec.expires_at <= current
        ]
        for rid in expired_ids:
            self._records.pop(rid, None)

    def save(
        self,
        response_id: str,
        response: dict[str, Any],
        gemini_contents: list[dict[str, Any]],
        function_call_names: dict[str, str] | None = None,
    ) -> None:
        now = time.monotonic()
        self._evict_expired(now)
        record = StoredResponseRecord(
            response=deepcopy(response),
            gemini_contents=deepcopy(gemini_contents),
            function_call_names=dict(function_call_names or {}),
            expires_at=now + self.ttl_seconds,
        )
        if response_id in self._records:
            self._records.move_to_end(response_id)
        self._records[response_id] = record

        while len(self._records) > self.max_items:
            self._records.popitem(last=False)

    def get(self, response_id: str) -> StoredResponseRecord | None:
        now = time.monotonic()
        self._evict_expired(now)
        record = self._records.get(response_id)
        if record is None:
            return None
        self._records.move_to_end(response_id)
        return record

    def get_response_or_active(self, response_id: str) -> dict[str, Any] | None:
        active = self._active.get(response_id)
        if active is not None:
            return deepcopy(active.partial_response)
        record = self.get(response_id)
        if record is None:
            return None
        return deepcopy(record.response)

    def delete(self, response_id: str) -> bool:
        self._evict_expired()
        existed = self._records.pop(response_id, None) is not None
        active = self._active.pop(response_id, None)
        if active is not None:
            active.cancel_event.set()
            if active.task and not active.task.done():
                active.task.cancel()
            existed = True
        return existed

    def register_active(
        self,
        response_id: str,
        initial_response: dict[str, Any],
        task: asyncio.Task[Any] | None = None,
        store_on_cancel: bool = True,
    ) -> ActiveResponseHandle:
        handle = ActiveResponseHandle(
            response_id=response_id,
            cancel_event=asyncio.Event(),
            partial_response=deepcopy(initial_response),
            task=task,
            store_on_cancel=store_on_cancel,
        )
        self._active[response_id] = handle
        return handle

    def unregister_active(self, response_id: str) -> None:
        self._active.pop(response_id, None)

    def cancel(self, response_id: str) -> dict[str, Any] | None:
        active = self._active.get(response_id)
        if active is not None:
            active.cancel_event.set()
            if active.task and not active.task.done():
                active.task.cancel()
            active.partial_response["status"] = "cancelled"
            cancelled_copy = deepcopy(active.partial_response)
            if active.store_on_cancel:
                self.save(
                    response_id=response_id,
                    response=cancelled_copy,
                    gemini_contents=[],
                    function_call_names={},
                )
            return cancelled_copy

        record = self.get(response_id)
        if record is None:
            return None

        current_status = record.response.get("status")
        if current_status == "cancelled":
            return deepcopy(record.response)
        if current_status in ("in_progress", "queued"):
            record.response["status"] = "cancelled"
            return deepcopy(record.response)

        raise OpenAIAPIException(
            status_code=400,
            message=f"Cannot cancel response '{response_id}' with status '{current_status}'.",
            code="invalid_request_error",
            param="response_id",
            err_type="invalid_request_error",
        )
