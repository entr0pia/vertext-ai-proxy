import asyncio
from collections.abc import AsyncGenerator
import json
import logging
import mimetypes
import re
import time
from typing import Any
import uuid

from fastapi import Response
from fastapi.responses import JSONResponse, StreamingResponse

from vertext_ai_proxy.core.exceptions import OpenAIAPIException
from vertext_ai_proxy.schemas.responses import (
    ResponseCreateRequest,
    ResponseDeleteResult,
)
from vertext_ai_proxy.services.response_store import ActiveResponseHandle, ResponseStore
from vertext_ai_proxy.services.vertex_client import VertexClient

logger = logging.getLogger(__name__)

_DATA_URI_RE = re.compile(r"^data:([^;]+);base64,(.+)$", re.DOTALL)


class ResponseService:
    """将 OpenAI Responses API 协议翻译至 Vertex AI 原生 :generateContent / :streamGenerateContent。"""

    def __init__(
        self,
        client: VertexClient,
        store: ResponseStore | None = None,
    ) -> None:
        self.client = client
        self.store = store or ResponseStore(
            max_items=client.config.proxy.response_store_max_items,
            ttl_seconds=client.config.proxy.response_store_ttl_seconds,
        )

    async def create_response(self, request: ResponseCreateRequest) -> Response:
        # 1. 恢复 previous_response_id 上下文（如提供）
        prior_contents: list[dict[str, Any]] = []
        function_call_names: dict[str, str] = {}
        if request.previous_response_id:
            prev_record = self.store.get(request.previous_response_id)
            if prev_record is None:
                raise OpenAIAPIException(
                    status_code=404,
                    message=f"Previous response '{request.previous_response_id}' not found.",
                    code="not_found",
                    param="previous_response_id",
                    err_type="invalid_request_error",
                )
            prior_contents = [dict(c) for c in prev_record.gemini_contents]
            function_call_names = dict(prev_record.function_call_names)

        # 2. 翻译为 Vertex AI :generateContent 请求体
        system_texts, new_contents = self._translate_inputs(
            request.input,
            request.instructions,
            function_call_names,
        )
        all_contents = prior_contents + new_contents
        if not all_contents:
            raise OpenAIAPIException(
                status_code=400,
                message="Input must contain at least one non-empty user or tool content item.",
                code="invalid_parameter",
                param="input",
                err_type="invalid_request_error",
            )

        payload = self._build_vertex_payload(request, system_texts, all_contents)
        response_id = f"resp_{uuid.uuid4().hex[:24]}"
        created_at = int(time.time())

        if request.stream:
            return await self._handle_streaming(
                request=request,
                payload=payload,
                response_id=response_id,
                created_at=created_at,
                all_contents=all_contents,
                function_call_names=function_call_names,
            )

        return await self._handle_non_streaming(
            request=request,
            payload=payload,
            response_id=response_id,
            created_at=created_at,
            all_contents=all_contents,
            function_call_names=function_call_names,
        )

    async def get_response(self, response_id: str) -> JSONResponse:
        result = self.store.get_response_or_active(response_id)
        if result is None:
            raise OpenAIAPIException(
                status_code=404,
                message=f"Response '{response_id}' not found.",
                code="not_found",
                param="response_id",
                err_type="invalid_request_error",
            )
        return JSONResponse(status_code=200, content=result)

    async def delete_response(self, response_id: str) -> JSONResponse:
        deleted = self.store.delete(response_id)
        if not deleted:
            raise OpenAIAPIException(
                status_code=404,
                message=f"Response '{response_id}' not found.",
                code="not_found",
                param="response_id",
                err_type="invalid_request_error",
            )
        payload = ResponseDeleteResult(id=response_id)
        return JSONResponse(status_code=200, content=payload.model_dump())

    async def cancel_response(self, response_id: str) -> JSONResponse:
        cancelled = self.store.cancel(response_id)
        if cancelled is None:
            raise OpenAIAPIException(
                status_code=404,
                message=f"Response '{response_id}' not found.",
                code="not_found",
                param="response_id",
                err_type="invalid_request_error",
            )
        return JSONResponse(status_code=200, content=cancelled)

    async def _handle_non_streaming(
        self,
        request: ResponseCreateRequest,
        payload: dict[str, Any],
        response_id: str,
        created_at: int,
        all_contents: list[dict[str, Any]],
        function_call_names: dict[str, str],
    ) -> JSONResponse:
        target_url = self.client.get_generate_content_url(request.model, stream=False)
        headers = await self.client.get_auth_headers()

        initial_resp = self._build_openai_response(
            request=request,
            response_id=response_id,
            created_at=created_at,
            status="in_progress",
            output_items=[],
            output_text="",
            usage=self._empty_usage(),
        )

        async def _do_post() -> Any:
            return await self.client.http_client.post(
                target_url,
                headers=headers,
                json=payload,
                timeout=self.client.config.gcp.timeout_seconds,
            )

        task = asyncio.create_task(_do_post())
        handle = self.store.register_active(
            response_id=response_id,
            initial_response=initial_resp,
            task=task,
            store_on_cancel=request.store,
        )

        try:
            resp = await task
        except asyncio.CancelledError:
            cancelled_resp = self._build_openai_response(
                request=request,
                response_id=response_id,
                created_at=created_at,
                status="cancelled",
                output_items=[],
                output_text="",
                usage=self._empty_usage(),
            )
            if request.store:
                self.store.save(
                    response_id=response_id,
                    response=cancelled_resp,
                    gemini_contents=all_contents,
                    function_call_names=function_call_names,
                )
            return JSONResponse(status_code=200, content=cancelled_resp)
        except Exception as e:
            logger.error(f"Failed to connect to Vertex AI generateContent: {e}")
            raise OpenAIAPIException(
                status_code=502,
                message=f"Failed to connect to Vertex AI: {str(e)}",
                code="upstream_connect_error",
                err_type="api_error",
            )
        finally:
            self.store.unregister_active(response_id)

        if handle.cancel_event.is_set():
            cancelled_resp = self._build_openai_response(
                request=request,
                response_id=response_id,
                created_at=created_at,
                status="cancelled",
                output_items=[],
                output_text="",
                usage=self._empty_usage(),
            )
            if request.store:
                self.store.save(
                    response_id=response_id,
                    response=cancelled_resp,
                    gemini_contents=all_contents,
                    function_call_names=function_call_names,
                )
            return JSONResponse(status_code=200, content=cancelled_resp)

        if resp.status_code != 200:
            err_msg = self.client.extract_upstream_error_message(resp)
            logger.error(
                f"Vertex AI generateContent returned error {resp.status_code}: {err_msg}"
            )
            raise OpenAIAPIException(
                status_code=resp.status_code,
                message=f"Vertex AI Responses error: {err_msg}",
                code="upstream_error",
                err_type="api_error",
            )

        resp_json = resp.json()
        output_items, combined_text, model_parts, usage = self._parse_vertex_response(
            resp_json, function_call_names
        )

        final_resp = self._build_openai_response(
            request=request,
            response_id=response_id,
            created_at=created_at,
            status="completed",
            output_items=output_items,
            output_text=combined_text,
            usage=usage,
        )

        if request.store:
            updated_contents = list(all_contents)
            if model_parts:
                updated_contents.append({"role": "model", "parts": model_parts})
            self.store.save(
                response_id=response_id,
                response=final_resp,
                gemini_contents=updated_contents,
                function_call_names=function_call_names,
            )

        return JSONResponse(status_code=200, content=final_resp)

    async def _handle_streaming(
        self,
        request: ResponseCreateRequest,
        payload: dict[str, Any],
        response_id: str,
        created_at: int,
        all_contents: list[dict[str, Any]],
        function_call_names: dict[str, str],
    ) -> Response:
        target_url = self.client.get_generate_content_url(request.model, stream=True)
        headers = await self.client.get_auth_headers(
            extra={"Accept-Encoding": "identity", "Accept": "text/event-stream"}
        )

        req = self.client.http_client.build_request(
            "POST",
            target_url,
            headers=headers,
            json=payload,
            timeout=self.client.config.gcp.timeout_seconds,
        )
        try:
            upstream_response = await self.client.http_client.send(req, stream=True)
        except Exception as e:
            logger.error(f"Failed to connect to Vertex AI streamGenerateContent: {e}")
            raise OpenAIAPIException(
                status_code=502,
                message=f"Failed to connect to Vertex AI: {str(e)}",
                code="upstream_connect_error",
                err_type="api_error",
            )

        if upstream_response.status_code != 200:
            await upstream_response.aread()
            await upstream_response.aclose()
            err_msg = self.client.extract_upstream_error_message(upstream_response)
            raise OpenAIAPIException(
                status_code=upstream_response.status_code,
                message=f"Vertex AI Responses streaming error: {err_msg}",
                code="upstream_error",
                err_type="api_error",
            )

        initial_resp = self._build_openai_response(
            request=request,
            response_id=response_id,
            created_at=created_at,
            status="in_progress",
            output_items=[],
            output_text="",
            usage=self._empty_usage(),
        )
        handle = self.store.register_active(
            response_id=response_id,
            initial_response=initial_resp,
            store_on_cancel=request.store,
        )

        async def sse_generator() -> AsyncGenerator[str, None]:
            seq = 0

            def _emit(event_type: str, data: dict[str, Any]) -> str:
                nonlocal seq
                data["type"] = event_type
                data["sequence_number"] = seq
                seq += 1
                return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

            output_items: list[dict[str, Any]] = []
            model_parts: list[dict[str, Any]] = []
            accumulated_text = ""
            text_item_id: str | None = None
            text_output_index: int | None = None
            latest_usage = self._empty_usage()

            try:
                yield _emit("response.created", {"response": initial_resp})
                yield _emit("response.in_progress", {"response": initial_resp})

                async for raw_line in upstream_response.aiter_lines():
                    if handle.cancel_event.is_set():
                        break

                    line = raw_line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue

                    try:
                        chunk_json = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    usage_meta = chunk_json.get("usageMetadata")
                    if isinstance(usage_meta, dict):
                        latest_usage = self._extract_usage(usage_meta)

                    candidates = chunk_json.get("candidates") or []
                    if not candidates:
                        continue
                    parts = (candidates[0].get("content") or {}).get("parts") or []

                    for part in parts:
                        if "text" in part and isinstance(part["text"], str):
                            delta_text = part["text"]
                            if not delta_text:
                                continue
                            if text_item_id is None:
                                text_item_id = f"msg_{uuid.uuid4().hex[:24]}"
                                text_output_index = len(output_items)
                                msg_item: dict[str, Any] = {
                                    "id": text_item_id,
                                    "type": "message",
                                    "status": "in_progress",
                                    "role": "assistant",
                                    "content": [],
                                }
                                output_items.append(msg_item)
                                yield _emit(
                                    "response.output_item.added",
                                    {
                                        "output_index": text_output_index,
                                        "item": msg_item,
                                    },
                                )
                                yield _emit(
                                    "response.content_part.added",
                                    {
                                        "item_id": text_item_id,
                                        "output_index": text_output_index,
                                        "content_index": 0,
                                        "part": {
                                            "type": "output_text",
                                            "text": "",
                                            "annotations": [],
                                        },
                                    },
                                )

                            accumulated_text += delta_text
                            yield _emit(
                                "response.output_text.delta",
                                {
                                    "item_id": text_item_id,
                                    "output_index": text_output_index,
                                    "content_index": 0,
                                    "delta": delta_text,
                                },
                            )

                        elif "functionCall" in part and isinstance(part["functionCall"], dict):
                            fn_call = part["functionCall"]
                            fn_name = str(fn_call.get("name", ""))
                            fn_args = fn_call.get("args") or {}
                            args_str = json.dumps(fn_args, ensure_ascii=False)
                            call_id = f"call_{uuid.uuid4().hex[:24]}"
                            fc_id = f"fc_{uuid.uuid4().hex[:24]}"
                            function_call_names[call_id] = fn_name
                            model_parts.append({"functionCall": {"name": fn_name, "args": fn_args}})

                            fc_output_index = len(output_items)
                            fc_item_start: dict[str, Any] = {
                                "id": fc_id,
                                "type": "function_call",
                                "status": "in_progress",
                                "call_id": call_id,
                                "name": fn_name,
                                "arguments": "",
                            }
                            output_items.append(fc_item_start)
                            yield _emit(
                                "response.output_item.added",
                                {
                                    "output_index": fc_output_index,
                                    "item": fc_item_start,
                                },
                            )
                            yield _emit(
                                "response.function_call_arguments.delta",
                                {
                                    "item_id": fc_id,
                                    "output_index": fc_output_index,
                                    "delta": args_str,
                                },
                            )
                            yield _emit(
                                "response.function_call_arguments.done",
                                {
                                    "item_id": fc_id,
                                    "output_index": fc_output_index,
                                    "arguments": args_str,
                                },
                            )
                            fc_item_done = {
                                **fc_item_start,
                                "status": "completed",
                                "arguments": args_str,
                            }
                            output_items[fc_output_index] = fc_item_done
                            yield _emit(
                                "response.output_item.done",
                                {
                                    "output_index": fc_output_index,
                                    "item": fc_item_done,
                                },
                            )

                # 收尾打开的文本输出项
                if text_item_id is not None and text_output_index is not None:
                    model_parts.insert(0, {"text": accumulated_text})
                    final_part = {
                        "type": "output_text",
                        "text": accumulated_text,
                        "annotations": [],
                    }
                    yield _emit(
                        "response.output_text.done",
                        {
                            "item_id": text_item_id,
                            "output_index": text_output_index,
                            "content_index": 0,
                            "text": accumulated_text,
                        },
                    )
                    yield _emit(
                        "response.content_part.done",
                        {
                            "item_id": text_item_id,
                            "output_index": text_output_index,
                            "content_index": 0,
                            "part": final_part,
                        },
                    )
                    done_msg_item = {
                        "id": text_item_id,
                        "type": "message",
                        "status": "cancelled" if handle.cancel_event.is_set() else "completed",
                        "role": "assistant",
                        "content": [final_part],
                    }
                    output_items[text_output_index] = done_msg_item
                    yield _emit(
                        "response.output_item.done",
                        {
                            "output_index": text_output_index,
                            "item": done_msg_item,
                        },
                    )

                final_status = "cancelled" if handle.cancel_event.is_set() else "completed"
                final_resp = self._build_openai_response(
                    request=request,
                    response_id=response_id,
                    created_at=created_at,
                    status=final_status,
                    output_items=output_items,
                    output_text=accumulated_text,
                    usage=latest_usage,
                )

                if request.store:
                    updated_contents = list(all_contents)
                    if model_parts:
                        updated_contents.append({"role": "model", "parts": model_parts})
                    self.store.save(
                        response_id=response_id,
                        response=final_resp,
                        gemini_contents=updated_contents,
                        function_call_names=function_call_names,
                    )

                terminal_event = (
                    "response.incomplete" if final_status == "cancelled" else "response.completed"
                )
                yield _emit(terminal_event, {"response": final_resp})
            except Exception as exc:
                logger.error(f"Error during Responses SSE stream translation: {exc}")
                failed_resp = self._build_openai_response(
                    request=request,
                    response_id=response_id,
                    created_at=created_at,
                    status="failed",
                    output_items=output_items,
                    output_text=accumulated_text,
                    usage=latest_usage,
                    error={"code": "internal_error", "message": str(exc)},
                )
                yield _emit("response.failed", {"response": failed_resp})
            finally:
                self.store.unregister_active(response_id)
                await upstream_response.aclose()

        return StreamingResponse(
            sse_generator(),
            status_code=200,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    def _translate_inputs(
        self,
        raw_input: str | list[dict[str, Any]],
        instructions: str | None,
        function_call_names: dict[str, str],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        system_texts: list[str] = []
        if instructions and instructions.strip():
            system_texts.append(instructions.strip())

        if isinstance(raw_input, str):
            return system_texts, [{"role": "user", "parts": [{"text": raw_input}]}]

        contents: list[dict[str, Any]] = []
        for item in raw_input:
            if not isinstance(item, dict):
                continue

            item_type = item.get("type")
            if item_type == "function_call":
                fn_name = str(item.get("name", ""))
                call_id = str(item.get("call_id", ""))
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
                contents.append(
                    {
                        "role": "model",
                        "parts": [{"functionCall": {"name": fn_name, "args": parsed_args}}],
                    }
                )
                continue

            if item_type == "function_call_output":
                call_id = str(item.get("call_id", ""))
                fn_name = str(item.get("name") or function_call_names.get(call_id, "unknown_function"))
                raw_output = item.get("output", "")
                response_payload: dict[str, Any]
                if isinstance(raw_output, dict):
                    response_payload = raw_output
                elif isinstance(raw_output, str):
                    try:
                        decoded = json.loads(raw_output)
                        response_payload = (
                            decoded if isinstance(decoded, dict) else {"result": decoded}
                        )
                    except json.JSONDecodeError:
                        response_payload = {"result": raw_output}
                else:
                    response_payload = {"result": raw_output}
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": fn_name,
                                    "response": response_payload,
                                }
                            }
                        ],
                    }
                )
                continue

            # 普通消息条目（user / assistant / system / developer）
            role = str(item.get("role", "user")).lower()
            raw_content = item.get("content", "")
            parts = self._translate_message_content(raw_content)

            if role in ("system", "developer"):
                for p in parts:
                    if "text" in p and p["text"].strip():
                        system_texts.append(p["text"].strip())
                continue

            gemini_role = "model" if role == "assistant" else "user"
            if parts:
                contents.append({"role": gemini_role, "parts": parts})

        return system_texts, contents

    def _translate_message_content(
        self, raw_content: str | list[dict[str, Any]] | Any
    ) -> list[dict[str, Any]]:
        if isinstance(raw_content, str):
            return [{"text": raw_content}] if raw_content else []
        if not isinstance(raw_content, list):
            return []

        parts: list[dict[str, Any]] = []
        for entry in raw_content:
            if isinstance(entry, str):
                if entry:
                    parts.append({"text": entry})
                continue
            if not isinstance(entry, dict):
                continue

            part_type = entry.get("type")
            if part_type in ("input_text", "output_text", "text"):
                text_val = entry.get("text", "")
                if isinstance(text_val, str) and text_val:
                    parts.append({"text": text_val})
            elif part_type in ("input_image", "image_url"):
                image_url_field = entry.get("image_url")
                url_str = (
                    image_url_field.get("url", "")
                    if isinstance(image_url_field, dict)
                    else str(image_url_field or "")
                )
                if url_str:
                    parts.append(self._image_url_to_part(url_str))
            elif part_type == "input_file":
                file_data = entry.get("file_data")
                if isinstance(file_data, str) and file_data:
                    m = _DATA_URI_RE.match(file_data.strip())
                    if m:
                        parts.append(
                            {"inlineData": {"mimeType": m.group(1), "data": m.group(2)}}
                        )
                    else:
                        mime = str(entry.get("mime_type") or "application/octet-stream")
                        parts.append({"inlineData": {"mimeType": mime, "data": file_data}})
        return parts

    @staticmethod
    def _image_url_to_part(url_str: str) -> dict[str, Any]:
        stripped = url_str.strip()
        match = _DATA_URI_RE.match(stripped)
        if match:
            return {
                "inlineData": {
                    "mimeType": match.group(1),
                    "data": match.group(2),
                }
            }
        guessed_mime, _ = mimetypes.guess_type(stripped)
        return {
            "fileData": {
                "mimeType": guessed_mime or "image/jpeg",
                "fileUri": stripped,
            }
        }

    def _build_vertex_payload(
        self,
        request: ResponseCreateRequest,
        system_texts: list[str],
        contents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"contents": contents}

        if system_texts:
            payload["systemInstruction"] = {
                "parts": [{"text": "\n\n".join(system_texts)}]
            }

        gen_config: dict[str, Any] = {}
        if request.temperature is not None:
            gen_config["temperature"] = request.temperature
        if request.top_p is not None:
            gen_config["topP"] = request.top_p
        if request.max_output_tokens is not None:
            gen_config["maxOutputTokens"] = request.max_output_tokens

        # Reasoning effort 映射
        if isinstance(request.reasoning, dict) and request.reasoning.get("effort"):
            effort = str(request.reasoning["effort"]).lower()
            clean_model = request.model.split("/")[-1].lower()
            if clean_model.startswith("gemini-2.5"):
                budget_map = {"minimal": 512, "low": 1024, "medium": 4096, "high": 16384}
                gen_config["thinkingConfig"] = {
                    "thinkingBudget": budget_map.get(effort, 4096)
                }
            else:
                level_map = {
                    "minimal": "LOW",
                    "low": "LOW",
                    "medium": "MEDIUM",
                    "high": "HIGH",
                }
                gen_config["thinkingConfig"] = {
                    "thinkingLevel": level_map.get(effort, "MEDIUM")
                }

        # Structured output (text.format) 映射
        if isinstance(request.text, dict) and isinstance(request.text.get("format"), dict):
            fmt = request.text["format"]
            fmt_type = fmt.get("type")
            if fmt_type == "json_object":
                gen_config["responseMimeType"] = "application/json"
            elif fmt_type == "json_schema":
                gen_config["responseMimeType"] = "application/json"
                if isinstance(fmt.get("schema"), dict):
                    gen_config["responseSchema"] = fmt["schema"]

        if gen_config:
            payload["generationConfig"] = gen_config

        # Function tools 映射
        if request.tools:
            fn_decls: list[dict[str, Any]] = []
            for tool in request.tools:
                if not isinstance(tool, dict) or tool.get("type") != "function":
                    continue
                fn_spec = tool.get("function") if isinstance(tool.get("function"), dict) else tool
                name = fn_spec.get("name")
                if not name:
                    continue
                decl: dict[str, Any] = {"name": name}
                if fn_spec.get("description"):
                    decl["description"] = fn_spec["description"]
                if isinstance(fn_spec.get("parameters"), dict):
                    decl["parameters"] = fn_spec["parameters"]
                fn_decls.append(decl)
            if fn_decls:
                payload["tools"] = [{"functionDeclarations": fn_decls}]

        # tool_choice 映射
        if request.tool_choice is not None:
            if isinstance(request.tool_choice, str):
                mode_map = {"auto": "AUTO", "none": "NONE", "required": "ANY"}
                mapped_mode = mode_map.get(request.tool_choice.lower())
                if mapped_mode:
                    payload["toolConfig"] = {
                        "functionCallingConfig": {"mode": mapped_mode}
                    }
            elif isinstance(request.tool_choice, dict):
                fn_name = request.tool_choice.get("name")
                if not fn_name and isinstance(request.tool_choice.get("function"), dict):
                    fn_name = request.tool_choice["function"].get("name")
                if fn_name:
                    payload["toolConfig"] = {
                        "functionCallingConfig": {
                            "mode": "ANY",
                            "allowedFunctionNames": [fn_name],
                        }
                    }

        return payload

    def _parse_vertex_response(
        self,
        resp_json: dict[str, Any],
        function_call_names: dict[str, str],
    ) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]], dict[str, Any]]:
        output_items: list[dict[str, Any]] = []
        text_chunks: list[str] = []
        model_parts: list[dict[str, Any]] = []

        candidates = resp_json.get("candidates") or []
        if candidates:
            parts = (candidates[0].get("content") or {}).get("parts") or []
            for part in parts:
                if "text" in part and isinstance(part["text"], str):
                    text_chunks.append(part["text"])
                    model_parts.append({"text": part["text"]})
                elif "functionCall" in part and isinstance(part["functionCall"], dict):
                    fn_call = part["functionCall"]
                    fn_name = str(fn_call.get("name", ""))
                    fn_args = fn_call.get("args") or {}
                    call_id = f"call_{uuid.uuid4().hex[:24]}"
                    function_call_names[call_id] = fn_name
                    model_parts.append({"functionCall": {"name": fn_name, "args": fn_args}})
                    output_items.append(
                        {
                            "id": f"fc_{uuid.uuid4().hex[:24]}",
                            "type": "function_call",
                            "status": "completed",
                            "call_id": call_id,
                            "name": fn_name,
                            "arguments": json.dumps(fn_args, ensure_ascii=False),
                        }
                    )

        combined_text = "".join(text_chunks)
        if combined_text or not output_items:
            output_items.insert(
                0,
                {
                    "id": f"msg_{uuid.uuid4().hex[:24]}",
                    "type": "message",
                    "status": "completed",
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

        usage = self._extract_usage(resp_json.get("usageMetadata") or {})
        return output_items, combined_text, model_parts, usage

    @staticmethod
    def _empty_usage() -> dict[str, Any]:
        return {
            "input_tokens": 0,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 0,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 0,
        }

    @staticmethod
    def _extract_usage(usage_meta: dict[str, Any]) -> dict[str, Any]:
        input_tokens = int(usage_meta.get("promptTokenCount", 0) or 0)
        output_tokens = int(usage_meta.get("candidatesTokenCount", 0) or 0)
        reasoning_tokens = int(usage_meta.get("thoughtsTokenCount", 0) or 0)
        cached_tokens = int(usage_meta.get("cachedContentTokenCount", 0) or 0)
        total_tokens = int(
            usage_meta.get("totalTokenCount", input_tokens + output_tokens + reasoning_tokens) or 0
        )
        return {
            "input_tokens": input_tokens,
            "input_tokens_details": {"cached_tokens": cached_tokens},
            "output_tokens": output_tokens + reasoning_tokens,
            "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
            "total_tokens": total_tokens,
        }

    @staticmethod
    def _build_openai_response(
        request: ResponseCreateRequest,
        response_id: str,
        created_at: int,
        status: str,
        output_items: list[dict[str, Any]],
        output_text: str,
        usage: dict[str, Any],
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": response_id,
            "object": "response",
            "created_at": created_at,
            "status": status,
            "error": error,
            "incomplete_details": {"reason": "cancelled"} if status == "cancelled" else None,
            "instructions": request.instructions,
            "max_output_tokens": request.max_output_tokens,
            "model": request.model,
            "output": output_items,
            "output_text": output_text,
            "parallel_tool_calls": True,
            "previous_response_id": request.previous_response_id,
            "reasoning": request.reasoning or {"effort": None, "summary": None},
            "store": request.store,
            "temperature": request.temperature,
            "text": request.text or {"format": {"type": "text"}},
            "tool_choice": request.tool_choice or "auto",
            "tools": request.tools or [],
            "top_p": request.top_p,
            "truncation": "disabled",
            "usage": usage,
            "metadata": request.metadata or {},
        }
