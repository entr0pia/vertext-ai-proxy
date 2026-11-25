from datetime import datetime
import json
import mimetypes
import re
import time
from typing import Any
import uuid

from vertext_ai_proxy.core.exceptions import OpenAIAPIException
from vertext_ai_proxy.schemas.responses import ResponseCreateRequest

_DATA_URI_RE = re.compile(r"^data:([^;]+);base64,(.+)$", re.DOTALL)

_THINKING_LEVELS: dict[str, str] = {
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
}

_STATUS_MAP: dict[str, str] = {
    "completed": "completed",
    "requires_action": "completed",
    "in_progress": "in_progress",
    "queued": "queued",
    "incomplete": "incomplete",
    "failed": "failed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}


def strip_model_prefix(model: str) -> str:
    """剥离 google/ 等厂商前缀，返回 Vertex AI Interactions API 所需的纯模型名。"""
    return model.split("/")[-1]


def parse_iso_timestamp(value: Any) -> int:
    """将 ISO-8601 时间字符串或数值转换为 Unix 秒级时间戳。"""
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return int(dt.timestamp())
        except ValueError:
            pass
    return int(time.time())


def empty_usage() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 0,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 0,
    }


def translate_interactions_usage(usage_raw: dict[str, Any] | None) -> dict[str, Any]:
    """将 Vertex AI Interactions API 的 usage 对象映射为 OpenAI Responses usage 结构。"""
    if not isinstance(usage_raw, dict):
        return empty_usage()

    input_tokens = int(usage_raw.get("total_input_tokens", 0) or 0)
    raw_output_tokens = int(usage_raw.get("total_output_tokens", 0) or 0)
    cached_tokens = int(usage_raw.get("total_cached_tokens", 0) or 0)
    reasoning_tokens = int(usage_raw.get("total_thought_tokens", 0) or 0)
    raw_total = int(usage_raw.get("total_tokens", 0) or 0)

    # Interactions API 的 total_output_tokens 不含 total_thought_tokens；
    # OpenAI Responses 的 output_tokens 包含 reasoning_tokens。
    output_tokens = raw_output_tokens + reasoning_tokens
    total_tokens = raw_total if raw_total > 0 else (input_tokens + output_tokens)

    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": cached_tokens},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
        "total_tokens": total_tokens,
    }


def map_interaction_status(raw_status: Any) -> str:
    if not isinstance(raw_status, str) or not raw_status.strip():
        return "completed"
    return _STATUS_MAP.get(raw_status.strip().lower(), raw_status.strip().lower())


def _image_url_to_block(url_str: str) -> dict[str, Any]:
    stripped = url_str.strip()
    match = _DATA_URI_RE.match(stripped)
    if match:
        return {
            "type": "image",
            "mime_type": match.group(1),
            "data": match.group(2),
        }
    guessed_mime, _ = mimetypes.guess_type(stripped)
    block: dict[str, Any] = {"type": "image", "uri": stripped}
    if guessed_mime:
        block["mime_type"] = guessed_mime
    return block


def _file_entry_to_block(entry: dict[str, Any]) -> dict[str, Any] | None:
    file_data = entry.get("file_data")
    if isinstance(file_data, str) and file_data.strip():
        stripped = file_data.strip()
        match = _DATA_URI_RE.match(stripped)
        if match:
            return {
                "type": "document",
                "mime_type": match.group(1),
                "data": match.group(2),
            }
        mime = str(entry.get("mime_type") or "application/octet-stream")
        return {"type": "document", "mime_type": mime, "data": stripped}

    file_url = entry.get("file_url")
    if isinstance(file_url, str) and file_url.strip():
        stripped = file_url.strip()
        guessed_mime, _ = mimetypes.guess_type(stripped)
        mime = str(entry.get("mime_type") or guessed_mime or "application/pdf")
        return {"type": "document", "uri": stripped, "mime_type": mime}

    return None


def _translate_content_to_blocks(raw_content: Any) -> list[dict[str, Any]]:
    if isinstance(raw_content, str):
        return [{"type": "text", "text": raw_content}] if raw_content else []
    if not isinstance(raw_content, list):
        return []

    blocks: list[dict[str, Any]] = []
    for entry in raw_content:
        if isinstance(entry, str):
            if entry:
                blocks.append({"type": "text", "text": entry})
            continue
        if not isinstance(entry, dict):
            continue

        part_type = entry.get("type")
        if part_type in ("input_text", "output_text", "text"):
            text_val = entry.get("text", "")
            if isinstance(text_val, str) and text_val:
                blocks.append({"type": "text", "text": text_val})
        elif part_type in ("input_image", "image_url"):
            image_url_field = entry.get("image_url")
            url_str = (
                image_url_field.get("url", "")
                if isinstance(image_url_field, dict)
                else str(image_url_field or "")
            )
            if url_str:
                blocks.append(_image_url_to_block(url_str))
        elif part_type == "input_file":
            doc_block = _file_entry_to_block(entry)
            if doc_block is not None:
                blocks.append(doc_block)
    return blocks


def _format_function_result_content(raw_output: Any) -> list[dict[str, Any]]:
    if isinstance(raw_output, str):
        return [{"type": "text", "text": raw_output}]
    if isinstance(raw_output, list):
        blocks = _translate_content_to_blocks(raw_output)
        if blocks:
            return blocks
        return [{"type": "text", "text": json.dumps(raw_output, ensure_ascii=False)}]
    if isinstance(raw_output, dict):
        return [{"type": "text", "text": json.dumps(raw_output, ensure_ascii=False)}]
    return [{"type": "text", "text": str(raw_output)}]


def translate_request_to_interactions(
    request: ResponseCreateRequest,
    function_call_names: dict[str, str],
    stream: bool = False,
) -> dict[str, Any]:
    """将 OpenAI ResponseCreateRequest 翻译为 Vertex AI Interactions API 请求体。"""
    system_texts: list[str] = []
    if request.instructions and request.instructions.strip():
        system_texts.append(request.instructions.strip())

    interactions_input: Any
    if isinstance(request.input, str):
        interactions_input = request.input
    else:
        parsed_entries: list[tuple[str, Any]] = []
        for item in request.input:
            if not isinstance(item, dict):
                continue

            item_type = item.get("type")
            if item_type == "function_call":
                fn_name = str(item.get("name", ""))
                call_id = str(item.get("call_id") or item.get("id") or "")
                if call_id and fn_name:
                    function_call_names[call_id] = fn_name
                raw_args = item.get("arguments", "{}")
                if isinstance(raw_args, str):
                    try:
                        parsed_args = json.loads(raw_args) if raw_args.strip() else {}
                    except json.JSONDecodeError:
                        parsed_args = {"raw": raw_args}
                elif isinstance(raw_args, dict):
                    parsed_args = raw_args
                else:
                    parsed_args = {}
                parsed_entries.append(
                    (
                        "function_call",
                        {
                            "type": "function_call",
                            "id": call_id,
                            "name": fn_name,
                            "arguments": parsed_args,
                        },
                    )
                )
                continue

            if item_type == "function_call_output":
                call_id = str(item.get("call_id", ""))
                fn_name = str(
                    item.get("name") or function_call_names.get(call_id) or "unknown_function"
                )
                result_blocks = _format_function_result_content(item.get("output", ""))
                parsed_entries.append(
                    (
                        "function_result",
                        {
                            "type": "function_result",
                            "name": fn_name,
                            "call_id": call_id,
                            "result": result_blocks,
                        },
                    )
                )
                continue

            role = str(item.get("role", "user")).lower()
            blocks = _translate_content_to_blocks(item.get("content", ""))

            if role in ("system", "developer"):
                for block in blocks:
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        system_texts.append(text.strip())
                continue

            if blocks:
                turn_role = "model" if role == "assistant" else "user"
                parsed_entries.append((turn_role, blocks))

        if not parsed_entries:
            raise OpenAIAPIException(
                status_code=400,
                message="Input must contain at least one non-empty user or tool content item.",
                code="invalid_parameter",
                param="input",
                err_type="invalid_request_error",
            )

        if all(kind == "function_result" for kind, _ in parsed_entries):
            interactions_input = [entry for _, entry in parsed_entries]
        elif len(parsed_entries) == 1 and parsed_entries[0][0] == "user":
            interactions_input = parsed_entries[0][1]
        else:
            turns: list[dict[str, Any]] = []
            for kind, content_val in parsed_entries:
                if kind == "function_call":
                    turns.append({"role": "model", "content": [content_val]})
                elif kind == "function_result":
                    turns.append({"role": "user", "content": [content_val]})
                else:
                    turns.append({"role": kind, "content": content_val})
            interactions_input = turns

    payload: dict[str, Any] = {
        "model": strip_model_prefix(request.model),
        "input": interactions_input,
        "store": request.store,
    }

    if stream:
        payload["stream"] = True

    if system_texts:
        payload["system_instruction"] = "\n\n".join(system_texts)

    if request.previous_response_id:
        payload["previous_interaction_id"] = request.previous_response_id

    if request.background is not None:
        payload["background"] = request.background

    # generation_config 组装
    gen_config: dict[str, Any] = {}
    if request.temperature is not None:
        gen_config["temperature"] = request.temperature
    if request.top_p is not None:
        gen_config["top_p"] = request.top_p
    if request.max_output_tokens is not None:
        gen_config["max_output_tokens"] = request.max_output_tokens

    if isinstance(request.reasoning, dict) and request.reasoning.get("effort"):
        effort = str(request.reasoning["effort"]).lower()
        gen_config["thinking_level"] = _THINKING_LEVELS.get(effort, "medium")

    if request.tool_choice is not None:
        if isinstance(request.tool_choice, str):
            tc_lower = request.tool_choice.lower()
            if tc_lower in ("auto", "none"):
                gen_config["tool_choice"] = tc_lower
            elif tc_lower == "required":
                gen_config["tool_choice"] = "any"
        elif isinstance(request.tool_choice, dict):
            fn_name = request.tool_choice.get("name")
            if not fn_name and isinstance(request.tool_choice.get("function"), dict):
                fn_name = request.tool_choice["function"].get("name")
            if fn_name:
                gen_config["tool_choice"] = {
                    "allowed_tools": {"mode": "any", "tools": [fn_name]}
                }

    if gen_config:
        payload["generation_config"] = gen_config

    # Function tools 映射
    if request.tools:
        mapped_tools: list[dict[str, Any]] = []
        for tool in request.tools:
            if not isinstance(tool, dict) or tool.get("type") != "function":
                continue
            fn_spec = tool.get("function") if isinstance(tool.get("function"), dict) else tool
            name = fn_spec.get("name")
            if not name:
                continue
            tool_def: dict[str, Any] = {"type": "function", "name": name}
            if fn_spec.get("description"):
                tool_def["description"] = fn_spec["description"]
            if isinstance(fn_spec.get("parameters"), dict):
                tool_def["parameters"] = fn_spec["parameters"]
            mapped_tools.append(tool_def)
        if mapped_tools:
            payload["tools"] = mapped_tools

    # Structured output (text.format) -> top-level response_format
    if isinstance(request.text, dict) and isinstance(request.text.get("format"), dict):
        fmt = request.text["format"]
        fmt_type = fmt.get("type")
        if fmt_type == "json_object":
            payload["response_format"] = {
                "type": "text",
                "mime_type": "application/json",
            }
        elif fmt_type == "json_schema":
            response_fmt: dict[str, Any] = {
                "type": "text",
                "mime_type": "application/json",
            }
            if isinstance(fmt.get("schema"), dict):
                response_fmt["schema"] = fmt["schema"]
            payload["response_format"] = response_fmt

    return payload


def build_openai_response_object(
    *,
    response_id: str,
    created_at: int,
    status: str,
    model: str,
    output_items: list[dict[str, Any]],
    output_text: str,
    usage: dict[str, Any],
    request: ResponseCreateRequest | None = None,
    instructions: str | None = None,
    previous_response_id: str | None = None,
    store: bool = True,
    reasoning_summary: str | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造标准 OpenAI Responses 对象。"""
    if request is not None:
        reasoning_obj = dict(request.reasoning) if isinstance(request.reasoning, dict) else {"effort": None, "summary": None}
        if "effort" not in reasoning_obj:
            reasoning_obj["effort"] = None
        if "summary" not in reasoning_obj:
            reasoning_obj["summary"] = None
    else:
        reasoning_obj = {"effort": None, "summary": None}

    if reasoning_summary is not None:
        reasoning_obj["summary"] = reasoning_summary

    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "error": error,
        "incomplete_details": {"reason": "cancelled"} if status == "cancelled" else None,
        "instructions": request.instructions if request is not None else instructions,
        "max_output_tokens": request.max_output_tokens if request is not None else None,
        "model": model,
        "output": output_items,
        "output_text": output_text,
        "parallel_tool_calls": True,
        "previous_response_id": (
            request.previous_response_id if request is not None else previous_response_id
        ),
        "reasoning": reasoning_obj,
        "store": request.store if request is not None else store,
        "temperature": request.temperature if request is not None else None,
        "text": (request.text if request is not None and request.text else {"format": {"type": "text"}}),
        "tool_choice": (request.tool_choice if request is not None and request.tool_choice else "auto"),
        "tools": (request.tools if request is not None and request.tools else []),
        "top_p": request.top_p if request is not None else None,
        "truncation": "disabled",
        "usage": usage,
        "metadata": (request.metadata if request is not None and request.metadata else {}),
    }


def extract_function_calls_from_interaction(interaction: dict[str, Any]) -> dict[str, str]:
    """从 Interaction 对象中提取 call_id -> function_name 映射。"""
    mapping: dict[str, str] = {}
    steps = interaction.get("steps") or interaction.get("outputs") or []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("type") == "function_call":
            call_id = str(step.get("id") or step.get("call_id") or "")
            name = str(step.get("name") or "")
            if call_id and name:
                mapping[call_id] = name
    return mapping


def translate_interaction_to_openai_response(
    interaction: dict[str, Any],
    request: ResponseCreateRequest | None = None,
    function_call_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """将 Vertex AI Interaction 对象翻译为 OpenAI Responses JSON 对象。"""
    response_id = str(interaction.get("id") or f"int_{uuid.uuid4().hex[:24]}")
    status = map_interaction_status(interaction.get("status"))
    created_at = parse_iso_timestamp(interaction.get("created"))

    output_items: list[dict[str, Any]] = []
    text_chunks: list[str] = []
    thought_summaries: list[str] = []

    steps = interaction.get("steps") or interaction.get("outputs") or []
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_type = step.get("type")

        if step_type == "model_output":
            content_list = step.get("content") or []
            if isinstance(content_list, str) and content_list:
                text_chunks.append(content_list)
            elif isinstance(content_list, list):
                for block in content_list:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_val = block.get("text")
                        if isinstance(text_val, str) and text_val:
                            text_chunks.append(text_val)

        elif step_type == "text":
            # 兼容 outputs 中直接出现 text 块的情况
            text_val = step.get("text")
            if isinstance(text_val, str) and text_val:
                text_chunks.append(text_val)

        elif step_type == "function_call":
            fn_name = str(step.get("name", ""))
            call_id = str(
                step.get("id") or step.get("call_id") or f"call_{uuid.uuid4().hex[:24]}"
            )
            raw_args = step.get("arguments")
            if isinstance(raw_args, str):
                args_str = raw_args
            else:
                args_str = json.dumps(
                    raw_args if raw_args is not None else {}, ensure_ascii=False
                )
            if function_call_names is not None and call_id and fn_name:
                function_call_names[call_id] = fn_name
            output_items.append(
                {
                    "id": f"fc_{uuid.uuid4().hex[:24]}",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": call_id,
                    "name": fn_name,
                    "arguments": args_str,
                }
            )

        elif step_type == "thought":
            summary = step.get("summary")
            if isinstance(summary, str) and summary.strip():
                thought_summaries.append(summary.strip())
            elif isinstance(summary, list):
                for s_block in summary:
                    if isinstance(s_block, dict) and isinstance(s_block.get("text"), str):
                        if s_block["text"].strip():
                            thought_summaries.append(s_block["text"].strip())

    combined_text = "".join(text_chunks)
    if combined_text or (not output_items and status in ("completed", "incomplete")):
        output_items.insert(
            0,
            {
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "type": "message",
                "status": "cancelled" if status == "cancelled" else "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": combined_text,
                        "annotations": [],
                    }
                ],
            },
        )

    usage = translate_interactions_usage(interaction.get("usage"))
    model_name = (request.model if request is not None else None) or str(
        interaction.get("model") or ""
    )

    raw_error = interaction.get("error")
    error_obj: dict[str, Any] | None = None
    if isinstance(raw_error, dict):
        error_obj = {
            "code": str(raw_error.get("code") or "upstream_error"),
            "message": str(raw_error.get("message") or "Upstream interaction failed"),
        }
    elif isinstance(raw_error, str) and raw_error:
        error_obj = {"code": "upstream_error", "message": raw_error}

    return build_openai_response_object(
        response_id=response_id,
        created_at=created_at,
        status=status,
        model=model_name,
        output_items=output_items,
        output_text=combined_text,
        usage=usage,
        request=request,
        instructions=interaction.get("system_instruction"),
        previous_response_id=interaction.get("previous_interaction_id"),
        store=bool(interaction.get("store", True)),
        reasoning_summary="\n".join(thought_summaries) if thought_summaries else None,
        error=error_obj,
    )


class InteractionsSSETranslator:
    """将 Vertex AI Interactions SSE 事件流翻译为 OpenAI Responses 语义 SSE 事件。"""

    def __init__(
        self,
        request: ResponseCreateRequest,
        function_call_names: dict[str, str] | None = None,
    ) -> None:
        self.request = request
        self.function_call_names = function_call_names if function_call_names is not None else {}
        self.seq = 0
        self.response_id: str = f"int_{uuid.uuid4().hex[:24]}"
        self.created_at: int = int(time.time())
        self.created_emitted = False
        self.completed_emitted = False
        self.output_items: list[dict[str, Any]] = []
        self.accumulated_text = ""
        self.latest_usage = empty_usage()
        self.thought_summaries: list[str] = []
        self._active_steps: dict[int, dict[str, Any]] = {}

    def _emit(self, event_type: str, data: dict[str, Any]) -> str:
        payload = dict(data)
        payload["type"] = event_type
        payload["sequence_number"] = self.seq
        self.seq += 1
        return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def _ensure_created(self) -> list[str]:
        if self.created_emitted:
            return []
        self.created_emitted = True
        initial_resp = build_openai_response_object(
            response_id=self.response_id,
            created_at=self.created_at,
            status="in_progress",
            model=self.request.model,
            output_items=[],
            output_text="",
            usage=empty_usage(),
            request=self.request,
        )
        return [
            self._emit("response.created", {"response": initial_resp}),
            self._emit("response.in_progress", {"response": initial_resp}),
        ]

    def _start_message_step(self, step_index: int) -> list[str]:
        events = self._ensure_created()
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        output_index = len(self.output_items)
        msg_item: dict[str, Any] = {
            "id": msg_id,
            "type": "message",
            "status": "in_progress",
            "role": "assistant",
            "content": [],
        }
        self.output_items.append(msg_item)
        self._active_steps[step_index] = {
            "kind": "message",
            "item_id": msg_id,
            "output_index": output_index,
            "text": "",
        }
        events.append(
            self._emit(
                "response.output_item.added",
                {"output_index": output_index, "item": msg_item},
            )
        )
        events.append(
            self._emit(
                "response.content_part.added",
                {
                    "item_id": msg_id,
                    "output_index": output_index,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                },
            )
        )
        return events

    def _start_function_call_step(self, step_index: int, step: dict[str, Any]) -> list[str]:
        events = self._ensure_created()
        call_id = str(step.get("id") or step.get("call_id") or f"call_{uuid.uuid4().hex[:24]}")
        fn_name = str(step.get("name") or "")
        if call_id and fn_name:
            self.function_call_names[call_id] = fn_name

        fc_id = f"fc_{uuid.uuid4().hex[:24]}"
        output_index = len(self.output_items)
        fc_item: dict[str, Any] = {
            "id": fc_id,
            "type": "function_call",
            "status": "in_progress",
            "call_id": call_id,
            "name": fn_name,
            "arguments": "",
        }
        self.output_items.append(fc_item)
        state = {
            "kind": "function_call",
            "item_id": fc_id,
            "call_id": call_id,
            "name": fn_name,
            "output_index": output_index,
            "arguments": "",
        }
        self._active_steps[step_index] = state
        events.append(
            self._emit(
                "response.output_item.added",
                {"output_index": output_index, "item": fc_item},
            )
        )

        # 若 step.start 已携带非空参数，立即补发一次 arguments.delta
        initial_args = step.get("arguments")
        initial_args_str = ""
        if isinstance(initial_args, dict) and initial_args:
            initial_args_str = json.dumps(initial_args, ensure_ascii=False)
        elif isinstance(initial_args, str) and initial_args.strip() and initial_args.strip() != "{}":
            initial_args_str = initial_args

        if initial_args_str:
            state["arguments"] += initial_args_str
            events.append(
                self._emit(
                    "response.function_call_arguments.delta",
                    {
                        "item_id": fc_id,
                        "output_index": output_index,
                        "delta": initial_args_str,
                    },
                )
            )
        return events

    def _close_step(self, active: dict[str, Any], cancelled: bool = False) -> list[str]:
        kind = active.get("kind")
        events: list[str] = []
        if kind == "message":
            item_id = active["item_id"]
            output_index = active["output_index"]
            step_text = active["text"]
            final_part = {"type": "output_text", "text": step_text, "annotations": []}
            events.append(
                self._emit(
                    "response.output_text.done",
                    {
                        "item_id": item_id,
                        "output_index": output_index,
                        "content_index": 0,
                        "text": step_text,
                    },
                )
            )
            events.append(
                self._emit(
                    "response.content_part.done",
                    {
                        "item_id": item_id,
                        "output_index": output_index,
                        "content_index": 0,
                        "part": final_part,
                    },
                )
            )
            done_item = {
                "id": item_id,
                "type": "message",
                "status": "cancelled" if cancelled else "completed",
                "role": "assistant",
                "content": [final_part],
            }
            self.output_items[output_index] = done_item
            events.append(
                self._emit(
                    "response.output_item.done",
                    {"output_index": output_index, "item": done_item},
                )
            )
        elif kind == "function_call":
            item_id = active["item_id"]
            output_index = active["output_index"]
            args_str = active["arguments"] or "{}"
            events.append(
                self._emit(
                    "response.function_call_arguments.done",
                    {
                        "item_id": item_id,
                        "output_index": output_index,
                        "arguments": args_str,
                    },
                )
            )
            done_item = {
                "id": item_id,
                "type": "function_call",
                "status": "cancelled" if cancelled else "completed",
                "call_id": active["call_id"],
                "name": active["name"],
                "arguments": args_str,
            }
            self.output_items[output_index] = done_item
            events.append(
                self._emit(
                    "response.output_item.done",
                    {"output_index": output_index, "item": done_item},
                )
            )
        return events

    def process_event(self, event_data: dict[str, Any]) -> list[str]:
        """消费一条上游 Interactions SSE JSON 帧，返回 0..N 条 OpenAI SSE 文本帧。"""
        event_type = str(event_data.get("event_type") or "")
        events: list[str] = []

        if event_type == "interaction.created":
            interaction = event_data.get("interaction") or {}
            if isinstance(interaction, dict):
                if interaction.get("id"):
                    self.response_id = str(interaction["id"])
                if interaction.get("created"):
                    self.created_at = parse_iso_timestamp(interaction["created"])
            events.extend(self._ensure_created())
            return events

        if event_type == "interaction.status_update":
            if event_data.get("interaction_id") and not self.created_emitted:
                self.response_id = str(event_data["interaction_id"])
            return events

        if event_type == "step.start":
            step_index = int(event_data.get("index", 0))
            step = event_data.get("step") or {}
            step_type = step.get("type") if isinstance(step, dict) else None
            if step_type == "model_output":
                events.extend(self._start_message_step(step_index))
            elif step_type == "function_call":
                events.extend(self._start_function_call_step(step_index, step))
            elif step_type == "thought":
                self._active_steps[step_index] = {"kind": "thought"}
            return events

        if event_type == "step.delta":
            step_index = int(event_data.get("index", 0))
            delta = event_data.get("delta") or {}
            if not isinstance(delta, dict):
                return events
            delta_type = delta.get("type")
            active = self._active_steps.get(step_index)

            if delta_type == "text":
                text_val = delta.get("text")
                if isinstance(text_val, str) and text_val:
                    if active is None or active.get("kind") != "message":
                        events.extend(self._start_message_step(step_index))
                        active = self._active_steps[step_index]
                    active["text"] += text_val
                    self.accumulated_text += text_val
                    events.append(
                        self._emit(
                            "response.output_text.delta",
                            {
                                "item_id": active["item_id"],
                                "output_index": active["output_index"],
                                "content_index": 0,
                                "delta": text_val,
                            },
                        )
                    )
            elif delta_type == "arguments_delta":
                args_delta = delta.get("arguments")
                if isinstance(args_delta, str) and args_delta:
                    if active and active.get("kind") == "function_call":
                        active["arguments"] += args_delta
                        events.append(
                            self._emit(
                                "response.function_call_arguments.delta",
                                {
                                    "item_id": active["item_id"],
                                    "output_index": active["output_index"],
                                    "delta": args_delta,
                                },
                            )
                        )
            elif delta_type == "thought_summary":
                content = delta.get("content")
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    if content["text"]:
                        self.thought_summaries.append(content["text"])
            return events

        if event_type == "step.stop":
            step_index = int(event_data.get("index", 0))
            if isinstance(event_data.get("usage"), dict):
                self.latest_usage = translate_interactions_usage(event_data["usage"])
            active = self._active_steps.pop(step_index, None)
            if active is not None:
                events.extend(self._close_step(active, cancelled=False))
            return events

        if event_type == "interaction.completed":
            events.extend(self._ensure_created())
            for idx in list(self._active_steps.keys()):
                active = self._active_steps.pop(idx)
                events.extend(self._close_step(active, cancelled=False))

            interaction = event_data.get("interaction") or {}
            if isinstance(interaction, dict):
                if interaction.get("id"):
                    self.response_id = str(interaction["id"])
                if interaction.get("created"):
                    self.created_at = parse_iso_timestamp(interaction["created"])
                if isinstance(interaction.get("usage"), dict):
                    self.latest_usage = translate_interactions_usage(interaction["usage"])
                final_status = map_interaction_status(interaction.get("status"))
            else:
                final_status = "completed"

            final_resp = build_openai_response_object(
                response_id=self.response_id,
                created_at=self.created_at,
                status=final_status,
                model=self.request.model,
                output_items=self.output_items,
                output_text=self.accumulated_text,
                usage=self.latest_usage,
                request=self.request,
                reasoning_summary=(
                    "".join(self.thought_summaries) if self.thought_summaries else None
                ),
            )
            self.completed_emitted = True
            if final_status == "failed":
                terminal_event = "response.failed"
            elif final_status in ("incomplete", "cancelled"):
                terminal_event = "response.incomplete"
            else:
                terminal_event = "response.completed"
            events.append(self._emit(terminal_event, {"response": final_resp}))
            return events

        if event_type == "error":
            events.extend(self._ensure_created())
            raw_err = event_data.get("error") or {}
            error_obj = {
                "code": str(raw_err.get("code") or "upstream_error")
                if isinstance(raw_err, dict)
                else "upstream_error",
                "message": str(raw_err.get("message") or "Upstream interaction error")
                if isinstance(raw_err, dict)
                else str(raw_err),
            }
            failed_resp = build_openai_response_object(
                response_id=self.response_id,
                created_at=self.created_at,
                status="failed",
                model=self.request.model,
                output_items=self.output_items,
                output_text=self.accumulated_text,
                usage=self.latest_usage,
                request=self.request,
                error=error_obj,
            )
            self.completed_emitted = True
            events.append(self._emit("response.failed", {"response": failed_resp}))
            return events

        return events

    def finalize_if_needed(self, cancelled: bool = False) -> list[str]:
        """当上游流提前结束或被取消时，补齐关闭事件与终态事件。"""
        if self.completed_emitted:
            return []
        events = self._ensure_created()
        for idx in list(self._active_steps.keys()):
            active = self._active_steps.pop(idx)
            events.extend(self._close_step(active, cancelled=cancelled))

        final_status = "cancelled" if cancelled else "completed"
        final_resp = build_openai_response_object(
            response_id=self.response_id,
            created_at=self.created_at,
            status=final_status,
            model=self.request.model,
            output_items=self.output_items,
            output_text=self.accumulated_text,
            usage=self.latest_usage,
            request=self.request,
            reasoning_summary=(
                "".join(self.thought_summaries) if self.thought_summaries else None
            ),
        )
        self.completed_emitted = True
        terminal_event = "response.incomplete" if cancelled else "response.completed"
        events.append(self._emit(terminal_event, {"response": final_resp}))
        return events
