#!/usr/bin/env python3
"""Single-file LM Studio-compatible server with an optional Mistral cloud backend.

Implements the endpoints used by the companion project:
  GET  /api/v1/models
  POST /api/v1/models/load
  POST /api/v1/models/unload
  POST /v1/chat/completions

Also implements GET /v1/models and basic Server-Sent Events for
/v1/chat/completions when stream=true.

When MISTRAL_API_KEY is configured, chat completions are sent to Mistral and
translated back into an LM Studio/OpenAI-compatible response. Without a key or
when the network is unavailable, it returns clearly marked simulated text.

Run:
    python lmstudio_mock.py
    python lmstudio_mock.py --port 1235

Then point your client at http://127.0.0.1:1234.
Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import time
import uuid
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Mistral cloud configuration
# ---------------------------------------------------------------------------
# Paste your Mistral API key between the quotes. Keep this file private and
# never commit it to a public repository while the key is filled in.
MISTRAL_API_KEY = ""

# Change this to any model ID enabled for your Mistral API account.
MISTRAL_MODEL = "mistral-small-latest"
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_TIMEOUT_SECONDS = 90

# With no API key, or when the network cannot reach Mistral, the server returns
# a clearly labelled local simulation. HTTP errors from Mistral (401, 429, etc.)
# are NOT hidden: they are returned to the client as JSON errors.
SIMULATE_WHEN_OFFLINE = True

# This is the single logical model exposed by the mock LM Studio server.
# The caller can keep using the usual load -> chat -> unload sequence.
MODEL_CATALOG: list[dict[str, Any]] = [
    {
        "type": "llm",
        "publisher": "mistral",
        "key": MISTRAL_MODEL,
        "display_name": f"{MISTRAL_MODEL} (Mistral cloud)",
        "architecture": "mistral",
        "quantization": {"name": "API", "bits_per_weight": 0},
        "size_bytes": 0,
        "params_string": "Cloud",
        "max_context_length": 32768,
        "format": "api",
        "capabilities": {
            "vision": False,
            "trained_for_tool_use": True,
        },
        "description": "Modèle cloud Mistral relayé par ce serveur local.",
    },
]

DEFAULT_CONTEXT_LENGTH = 4096
DEFAULT_EVAL_BATCH_SIZE = 512


class MockLMStudioState:
    def __init__(self) -> None:
        self.catalog = {item["key"]: deepcopy(item) for item in MODEL_CATALOG}
        self.loaded: dict[str, dict[str, Any]] = {}
        self.instance_counts: dict[str, int] = {}

    def list_native_models(self) -> dict[str, Any]:
        result = []
        for key, source in self.catalog.items():
            item = deepcopy(source)
            item["loaded_instances"] = [
                {
                    "id": instance_id,
                    "config": deepcopy(instance["config"]),
                }
                for instance_id, instance in self.loaded.items()
                if instance["model"] == key
            ]
            result.append(item)
        return {"models": result}

    def list_openai_models(self) -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": key,
                    "object": "model",
                    "created": 0,
                    "owned_by": model["publisher"],
                }
                for key, model in self.catalog.items()
            ],
        }

    def load_model(self, body: dict[str, Any]) -> dict[str, Any]:
        model_key = body.get("model")
        if not isinstance(model_key, str) or not model_key:
            raise APIError(400, "The 'model' field must be a non-empty string.")
        if model_key not in self.catalog:
            raise APIError(404, f"Model '{model_key}' was not found in the mock catalogue.")

        self.instance_counts[model_key] = self.instance_counts.get(model_key, 0) + 1
        instance_id = model_key
        if any(item["model"] == model_key for item in self.loaded.values()):
            instance_id = f"{model_key}#{self.instance_counts[model_key]}"

        config: dict[str, Any] = {
            "context_length": body.get(
                "context_length", DEFAULT_CONTEXT_LENGTH
            )
        }
        # These are documented load settings. Include only settings relevant to
        # this simulated instance, while preserving explicit values from caller.
        for field in (
            "eval_batch_size",
            "flash_attention",
            "num_experts",
            "offload_kv_cache_to_gpu",
        ):
            if field in body:
                config[field] = body[field]
        if "eval_batch_size" not in config:
            config["eval_batch_size"] = DEFAULT_EVAL_BATCH_SIZE

        self.loaded[instance_id] = {"model": model_key, "config": config}
        result: dict[str, Any] = {
            "type": self.catalog[model_key]["type"],
            "instance_id": instance_id,
            "load_time_seconds": 0.01,
            "status": "loaded",
        }
        if body.get("echo_load_config") is True:
            result["load_config"] = deepcopy(config)
        return result

    def unload_model(self, body: dict[str, Any]) -> dict[str, Any]:
        instance_id = body.get("instance_id")
        if not isinstance(instance_id, str) or not instance_id:
            raise APIError(400, "The 'instance_id' field must be a non-empty string.")
        if instance_id not in self.loaded:
            raise APIError(404, f"Loaded model instance '{instance_id}' was not found.")
        del self.loaded[instance_id]
        return {"instance_id": instance_id}

    def ensure_model_loaded(self, model_key: Any) -> None:
        if not isinstance(model_key, str) or model_key not in self.catalog:
            raise APIError(404, f"Model '{model_key}' was not found in the mock catalogue.")
        if not any(item["model"] == model_key for item in self.loaded.values()):
            raise APIError(400, f"Model '{model_key}' is not loaded. Call /api/v1/models/load first.")


class APIError(Exception):
    def __init__(self, status: int, message: str, code: str = "invalid_request_error") -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code

    def as_json(self) -> dict[str, Any]:
        return {
            "error": {
                "message": self.message,
                "type": self.code,
                "param": None,
                "code": self.status,
            }
        }


STATE = MockLMStudioState()


def content_to_text(content: Any) -> str:
    """Extract readable text from string or OpenAI-style multimodal content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return " ".join(parts)
    return ""


def approximate_tokens(text: str) -> int:
    """Rough token estimate; exact tokenization requires the actual model."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def simulate_answer(messages: list[dict[str, Any]], model_key: str) -> str:
    """Return an explicit mock response, not pretend model-generated prose."""
    last_user = None
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            last_user = content_to_text(message.get("content"))
            break

    if model_key == "mock/echo-model" and last_user:
        return f"[MOCK ECHO] {last_user}"
    if last_user:
        excerpt = last_user.strip()
        if len(excerpt) > 500:
            excerpt = excerpt[:497] + "..."
        return (
            f"[SIMULATION — {model_key}] Message reçu. "
            f"Ceci est une réponse factice : aucun modèle d'IA n'a été exécuté.\n\n"
            f"Dernier message utilisateur : {excerpt}"
        )
    return (
        f"[SIMULATION — {model_key}] Requête reçue. "
        "Aucun message utilisateur exploitable n'a été trouvé et aucun modèle d'IA n'a été exécuté."
    )


MISTRAL_REQUEST_FIELDS = {
    "temperature",
    "top_p",
    "max_tokens",
    "stop",
    "random_seed",
    "frequency_penalty",
    "presence_penalty",
    "response_format",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "n",
    "safe_prompt",
    "min_tokens",
    "top_k",
    "repetition_penalty",
    "prompt_mode",
    "reasoning_effort",
    "service_tier",
    "logprobs",
    "metadata",
    "prompt_cache_key",
    "prediction",
    "guardrails",
    "prompt_logprobs",
    "top_logprobs",
    "top_prompt_logprobs",
}


def build_mistral_request(body: dict[str, Any]) -> dict[str, Any]:
    """Translate an LM Studio-style request into Mistral's Chat API request."""
    payload: dict[str, Any] = {
        "model": MISTRAL_MODEL,
        "messages": body["messages"],
    }
    for field in MISTRAL_REQUEST_FIELDS:
        if field in body:
            payload[field] = body[field]
    # Mistral's API supports server-sent events. The response is adapted back
    # to the client's requested stream mode below.
    payload["stream"] = bool(body.get("stream", False))
    return payload


def normalize_mistral_response(
    source: dict[str, Any], requested_model: str
) -> dict[str, Any]:
    """Convert Mistral Chat Completion JSON to the expected LM-style shape."""
    try:
        created = int(source.get("created", time.time()))
    except (TypeError, ValueError, OverflowError):
        created = int(time.time())

    normalized_choices: list[dict[str, Any]] = []
    for index, choice in enumerate(source.get("choices") or []):
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            message = {"role": "assistant", "content": str(message)}
        else:
            message = deepcopy(message)
            message.setdefault("role", "assistant")
            message.setdefault("content", "")

        normalized_choices.append(
            {
                "index": choice.get("index", index),
                "message": message,
                "logprobs": choice.get("logprobs"),
                "finish_reason": choice.get("finish_reason") or "stop",
            }
        )

    usage_source = source.get("usage") or {}
    if not isinstance(usage_source, dict):
        usage_source = {}
    prompt_tokens = usage_source.get("prompt_tokens")
    completion_tokens = usage_source.get("completion_tokens")
    total_tokens = usage_source.get("total_tokens")
    if prompt_tokens is None:
        prompt_tokens = 0
    if completion_tokens is None:
        completion_tokens = 0
    if total_tokens is None:
        total_tokens = prompt_tokens + completion_tokens

    normalized: dict[str, Any] = {
        "id": source.get("id") or ("chatcmpl-" + uuid.uuid4().hex[:24]),
        "object": "chat.completion",
        "created": created,
        # Preserve the model identifier requested from the local server. In
        # this setup it is normally the same as MISTRAL_MODEL.
        "model": requested_model,
        "choices": normalized_choices,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
        "system_fingerprint": source.get("system_fingerprint") or "mistral-api-proxy",
    }
    return normalized


def make_mistral_error(status: int, raw_body: bytes) -> dict[str, Any]:
    """Convert an upstream Mistral error into the common OpenAI error shape."""
    message = f"Mistral API returned HTTP {status}."
    error_type = "mistral_api_error"
    param = None
    try:
        payload = json.loads(raw_body.decode("utf-8"))
        detail = payload.get("error", payload) if isinstance(payload, dict) else payload
        if isinstance(detail, dict):
            message = (
                detail.get("message")
                or detail.get("detail")
                or detail.get("type")
                or message
            )
            error_type = detail.get("type") or error_type
            param = detail.get("param")
        elif isinstance(detail, str):
            message = detail
    except (UnicodeDecodeError, json.JSONDecodeError):
        if raw_body:
            message = raw_body.decode("utf-8", errors="replace")[:1000]

    return {
        "error": {
            "message": str(message),
            "type": str(error_type),
            "param": param,
            "code": status,
        }
    }


class LMStudioMockHandler(BaseHTTPRequestHandler):
    server_version = "LMStudioMistralProxy/1.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _send_sse(self, payloads: list[dict[str, Any]]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for payload in payloads:
            line = "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise APIError(400, "Invalid Content-Length header.")
        if length <= 0:
            raise APIError(400, "A JSON request body is required.")
        if length > 10_000_000:
            raise APIError(413, "Request body is too large.")
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise APIError(400, "Request body must contain valid UTF-8 JSON.")
        if not isinstance(parsed, dict):
            raise APIError(400, "The JSON request body must be an object.")
        return parsed

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        path = urlparse(self.path).path
        if path == "/api/v1/models":
            self._send_json(200, STATE.list_native_models())
        elif path == "/v1/models":
            self._send_json(200, STATE.list_openai_models())
        elif path == "/":
            self._send_json(
                200,
                {
                    "name": "LM Studio-compatible Mistral proxy",
                    "status": "ok",
                    "chat_backend": "mistral" if MISTRAL_API_KEY.strip() else "offline_simulation",
                    "backend_model": MISTRAL_MODEL,
                    "implemented_endpoints": [
                        "GET /api/v1/models",
                        "POST /api/v1/models/load",
                        "POST /api/v1/models/unload",
                        "POST /v1/chat/completions",
                        "GET /v1/models",
                    ],
                    "note": (
                        "Chat completions are forwarded to Mistral when an API key is configured; "
                        "otherwise clearly labelled offline simulation responses are returned."
                    ),
                },
            )
        else:
            self._send_json(404, APIError(404, f"Unknown endpoint: {path}").as_json())

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        path = urlparse(self.path).path
        try:
            body = self._read_json()
            if path == "/api/v1/models/load":
                self._send_json(200, STATE.load_model(body))
            elif path == "/api/v1/models/unload":
                self._send_json(200, STATE.unload_model(body))
            elif path == "/v1/chat/completions":
                self._chat_completions(body)
            else:
                self._send_json(404, APIError(404, f"Unknown endpoint: {path}").as_json())
        except APIError as exc:
            self._send_json(exc.status, exc.as_json())
        except (BrokenPipeError, ConnectionResetError):
            # Client disconnected while receiving a response.
            pass
        except Exception as exc:  # Keep the server alive after unexpected errors.
            self._send_json(
                500,
                APIError(500, f"Internal mock-server error: {type(exc).__name__}: {exc}", "server_error").as_json(),
            )

    def _chat_completions(self, body: dict[str, Any]) -> None:
        model_key = body.get("model")
        STATE.ensure_model_loaded(model_key)

        messages = body.get("messages")
        if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
            raise APIError(400, "The 'messages' field must be an array of message objects.")

        for message in messages:
            role = message.get("role")
            if not isinstance(role, str) or not isinstance(message.get("content"), (str, list, type(None))):
                raise APIError(400, "Each message must contain a string role and string/list/null content.")

        # No key: remain useful as an offline protocol simulator.
        if not MISTRAL_API_KEY.strip():
            print("[Mistral] No API key configured; returning an offline simulation.")
            result = self._build_simulated_completion(messages, model_key, "no MISTRAL_API_KEY configured")
            self._send_completion_result(result, body.get("stream") is True)
            return

        mistral_payload = build_mistral_request(body)
        request = Request(
            MISTRAL_API_URL,
            data=json.dumps(mistral_payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {MISTRAL_API_KEY.strip()}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if body.get("stream") is True else "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=MISTRAL_TIMEOUT_SECONDS) as upstream:
                if body.get("stream") is True:
                    self._proxy_mistral_stream(upstream, model_key)
                    return
                try:
                    mistral_result = json.loads(upstream.read().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise APIError(502, f"Mistral returned invalid JSON: {exc}", "upstream_error")

            if not isinstance(mistral_result, dict):
                raise APIError(502, "Mistral returned an unexpected response format.", "upstream_error")
            normalized = normalize_mistral_response(mistral_result, model_key)
            self._send_json(200, normalized)
            print(f"[Mistral] Completion OK (backend model: {MISTRAL_MODEL}).")

        except HTTPError as exc:
            # An HTTP response arrived, so this is not an offline condition.
            error_body = exc.read()
            error_payload = make_mistral_error(exc.code, error_body)
            self._send_json(exc.code, error_payload)
            print(f"[Mistral] API error HTTP {exc.code}.")
        except (URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
            if not SIMULATE_WHEN_OFFLINE:
                raise APIError(503, f"Could not reach Mistral: {exc}", "service_unavailable")
            print(f"[Mistral] Network unavailable ({type(exc).__name__}); returning an offline simulation.")
            result = self._build_simulated_completion(messages, model_key, "Mistral API unreachable")
            self._send_completion_result(result, body.get("stream") is True)

    def _build_simulated_completion(
        self, messages: list[dict[str, Any]], model_key: str, reason: str
    ) -> dict[str, Any]:
        answer = (
            f"[OFFLINE SIMULATION — {reason}] Aucun appel Mistral n'a été effectué.\n\n"
            + simulate_answer(messages, model_key)
        )
        request_text = "\n".join(content_to_text(item.get("content")) for item in messages)
        prompt_tokens = approximate_tokens(request_text)
        completion_tokens = approximate_tokens(answer)
        return {
            "id": "chatcmpl-" + uuid.uuid4().hex[:24],
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_key,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer},
                    "logprobs": None,
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "system_fingerprint": "lmstudio-mock-offline",
        }

    def _send_completion_result(self, result: dict[str, Any], stream: bool) -> None:
        if not stream:
            self._send_json(200, result)
            return

        chunk_base = {
            "id": result["id"],
            "object": "chat.completion.chunk",
            "created": result["created"],
            "model": result["model"],
        }
        message = result["choices"][0]["message"]
        content = message.get("content") or ""
        chunks = [
            {
                **chunk_base,
                "choices": [
                    {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
                ],
            },
            {
                **chunk_base,
                "choices": [
                    {"index": 0, "delta": {"content": content}, "finish_reason": None}
                ],
            },
            {
                **chunk_base,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
        ]
        self._send_sse(chunks)

    def _proxy_mistral_stream(self, upstream: Any, model_key: str) -> None:
        """Forward Mistral SSE chunks while normalizing the LM-compatible fields."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        stream_id = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())

        try:
            for raw_line in upstream:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(chunk, dict):
                    continue

                chunk_id = chunk.get("id") or stream_id
                try:
                    chunk_created = int(chunk.get("created", created))
                except (TypeError, ValueError, OverflowError):
                    chunk_created = created
                outgoing: dict[str, Any] = {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": chunk_created,
                    "model": model_key,
                }
                choices = []
                for index, choice in enumerate(chunk.get("choices") or []):
                    if not isinstance(choice, dict):
                        continue
                    choices.append(
                        {
                            "index": choice.get("index", index),
                            "delta": choice.get("delta") or {},
                            "finish_reason": choice.get("finish_reason"),
                        }
                    )
                if choices:
                    outgoing["choices"] = choices
                if isinstance(chunk.get("usage"), dict):
                    outgoing["usage"] = chunk["usage"]
                event = "data: " + json.dumps(outgoing, ensure_ascii=False) + "\n\n"
                self.wfile.write(event.encode("utf-8"))
                self.wfile.flush()

            # Some compatible providers close the stream without an explicit
            # [DONE] event. Send it once at EOF to finish the client stream.
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            error_event = {
                "error": {
                    "message": f"Mistral stream interrupted: {type(exc).__name__}: {exc}",
                    "type": "stream_error",
                    "param": None,
                    "code": 502,
                }
            }
            try:
                self.wfile.write(("data: " + json.dumps(error_event) + "\n\n").encode("utf-8"))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass



def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate the LM Studio HTTP API for local testing.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=1234, help="HTTP port (default: 1234).")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), LMStudioMockHandler)
    server.daemon_threads = True
    print("LM Studio-compatible Mistral proxy")
    print(f"Listening on http://{args.host}:{args.port}")
    if MISTRAL_API_KEY.strip():
        print(f"Chat backend: Mistral ({MISTRAL_MODEL})")
    else:
        print("Chat backend: offline simulation (set MISTRAL_API_KEY to enable Mistral)")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping mock server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
