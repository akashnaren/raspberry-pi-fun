"""Keyed HTTP API for apps: chat, health, OpenAPI, and the docs page."""

from __future__ import annotations

import hmac
import json
import os

# Public name of the only generative checkpoint on pi4. Low, medium, and high
# change the decode budget. They do not select another model.
FLASH_MODE = "flash"
FLASH_CHECKPOINT = "qwen3:0.6b"
API_KEY_ENV = "PI_GPT_API_KEY"

# Levels match the page. They do not select another checkpoint.
MODE_THINK = {
    "flash": "medium",
    "low": "low",
    "medium": "medium",
    "high": "high",
}


def configured_key() -> str:
    return os.environ.get(API_KEY_ENV, "").strip()


def flash_checkpoint() -> str:
    """Tag Ollama actually runs. Flash is that checkpoint's public name."""
    from pair import runtime

    name = (runtime.MODEL or "").strip()
    return name or FLASH_CHECKPOINT


def presented_key(headers) -> str:
    """Bearer wins when Authorization is set. Otherwise X-API-Key."""
    authorization = (headers.get("Authorization") or "").strip()
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer":
            return token.strip()
        return ""
    return (headers.get("X-API-Key") or "").strip()


def authorize(headers) -> tuple[int, str] | None:
    """None when the caller presented the configured key."""
    expected = configured_key()
    if not expected:
        return 503, f"{API_KEY_ENV} is not set"
    presented = presented_key(headers)
    if not presented or not hmac.compare_digest(presented, expected):
        return 401, "missing or invalid API key"
    return None


def apply_mode(data: dict) -> str:
    """Select Flash when mode is omitted, and always pin the Flash checkpoint.

    An explicit think value still chooses the decode budget. Otherwise the
    mode's budget is used. Flash uses the medium budget.
    """
    raw = data.get("mode", None)
    if raw is None:
        name = FLASH_MODE
    elif not isinstance(raw, str):
        raise ValueError("mode must be a string")
    else:
        name = raw.strip().lower() or FLASH_MODE
    if name not in MODE_THINK:
        allowed = "flash, low, medium, or high"
        raise ValueError(f"unknown mode {name!r}. Use {allowed}.")
    data["model"] = flash_checkpoint()
    if not str(data.get("think") or "").strip():
        data["think"] = MODE_THINK[name]
    data.pop("mode", None)
    return name


def stamp(payload: dict, mode: str) -> dict:
    """Name the public model on a completion body. No-op for the LAN page."""
    if not mode or not isinstance(payload, dict):
        return payload
    payload["mode"] = mode
    payload["model"] = FLASH_MODE
    payload["checkpoint"] = flash_checkpoint()
    return payload


def openapi_document() -> dict:
    checkpoint = flash_checkpoint()
    description = (
        "HTTP API so other apps can call Pi GPT. "
        f"Set {API_KEY_ENV} in the router environment. "
        "Send that value as `Authorization: Bearer <key>` or as the "
        "`X-API-Key` header. Do not put the key in the query string. "
        f"If {API_KEY_ENV} is unset, chat and health return 503. "
        "A missing or wrong key returns 401. "
        "POST /api/chat uses the same inference cap as the page. "
        "When every slot is in use the request waits in a queue of 8 for up to "
        "60 seconds. The page shows Waiting for a free slot. A full queue or a "
        "wait that runs out returns 503 with a short message and does not name "
        "the board. A map hit does not take a slot. "
        "`GET /openapi.json` and `GET /docs` do not require the key. "
        "If `mode` is omitted, the model is Flash. "
        f"Flash is the fleet checkpoint `{checkpoint}` on pi4 "
        f"(the default tag is `{FLASH_CHECKPOINT}` unless MESH_MODEL is set). "
        "`low`, `medium`, and `high` keep that same checkpoint and set Ollama's "
        "`think` field. Low and Medium are think=false at temperature 0.7, "
        "top_p 0.8, top_k 20, presence_penalty 1.5. Low allows 64 answer tokens "
        "and Medium allows 384. "
        "High is think=true (temperature 0.6, top_p 0.95, top_k 20) with a "
        "192-token or 25-second thinking cap, then a separate 768-token answer. "
        "On the LAN page, High stays on Flash unless Pro was chosen. "
        "Reasoning is `reasoning_content`, never `content`. Omitted mode uses Medium. "
        "The LAN page (`/`, `/health`, `/v1/chat/completions`) does not use this key."
    )
    error = {
        "type": "object",
        "properties": {"error": {"type": "string"}},
        "required": ["error"],
    }
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Pi GPT API",
            "version": "1.0.0",
            "description": description,
        },
        "servers": [{"url": "/"}],
        "tags": [
            {"name": "chat", "description": "One completion."},
            {"name": "health", "description": "Router and peer snapshot."},
            {"name": "meta", "description": "This document and the Swagger UI."},
        ],
        "paths": {
            "/api/chat": {
                "post": {
                    "operationId": "postChat",
                    "tags": ["chat"],
                    "summary": "Chat with Pi GPT",
                    "description": (
                        "One user turn, or a short session in `messages`. "
                        "Omitting `mode` selects the Flash model. "
                        "A stored sentence is returned with `pi_model` `canned` "
                        "and does not call pi4. A miss is generated only on pi4."
                    ),
                    "security": [{"bearerAuth": []}, {"apiKeyAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ChatRequest"},
                                "example": {
                                    "messages": [{"role": "user", "content": "status"}]
                                },
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Assistant message.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ChatResponse"
                                    }
                                }
                            },
                        },
                        "400": {
                            "description": "The body or mode was rejected.",
                            "content": {"application/json": {"schema": error}},
                        },
                        "401": {
                            "description": "Missing or invalid API key.",
                            "content": {"application/json": {"schema": error}},
                        },
                        "413": {
                            "description": "The JSON body is larger than 1 MB.",
                            "content": {"application/json": {"schema": error}},
                        },
                        "502": {
                            "description": "pi4 did not answer, or the pin was refused.",
                            "content": {"application/json": {"schema": error}},
                        },
                        "503": {
                            "description": (
                                f"{API_KEY_ENV} is not set, or the inference queue is full. "
                                "A full cap waits in a queue of 8, and the page shows "
                                "Waiting for a free slot. A full queue or a wait that runs "
                                "out returns 503 with a short message, the same as "
                                "POST /v1/chat/completions. A map hit does not take a slot."
                            ),
                            "content": {"application/json": {"schema": error}},
                        },
                    },
                }
            },
            "/api/health": {
                "get": {
                    "operationId": "getHealth",
                    "tags": ["health"],
                    "summary": "Health of the router and peers",
                    "description": (
                        "Same snapshot as `GET /health`, plus the public model name. "
                        "Requires the API key."
                    ),
                    "security": [{"bearerAuth": []}, {"apiKeyAuth": []}],
                    "responses": {
                        "200": {
                            "description": "Router is answering.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Health"}
                                }
                            },
                        },
                        "401": {
                            "description": "Missing or invalid API key.",
                            "content": {"application/json": {"schema": error}},
                        },
                        "503": {
                            "description": f"{API_KEY_ENV} is not set.",
                            "content": {"application/json": {"schema": error}},
                        },
                    },
                }
            },
            "/openapi.json": {
                "get": {
                    "operationId": "getOpenAPI",
                    "tags": ["meta"],
                    "summary": "OpenAPI document",
                    "security": [],
                    "responses": {
                        "200": {
                            "description": "This document.",
                            "content": {
                                "application/json": {"schema": {"type": "object"}}
                            },
                        }
                    },
                }
            },
            "/docs": {
                "get": {
                    "operationId": "getDocs",
                    "tags": ["meta"],
                    "summary": "Swagger UI",
                    "description": "Swagger UI for this document. No API key.",
                    "security": [],
                    "responses": {
                        "200": {
                            "description": "HTML page.",
                            "content": {"text/html": {"schema": {"type": "string"}}},
                        }
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": (
                        f"The value of the {API_KEY_ENV} environment variable. "
                        "Header: Authorization: Bearer <key>."
                    ),
                },
                "apiKeyAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Key",
                    "description": (
                        f"The same {API_KEY_ENV} value, in the X-API-Key header."
                    ),
                },
            },
            "schemas": {
                "ChatMessage": {
                    "type": "object",
                    "required": ["role", "content"],
                    "properties": {
                        "role": {
                            "type": "string",
                            "enum": ["system", "user", "assistant"],
                        },
                        "content": {"type": "string"},
                    },
                },
                "ChatRequest": {
                    "type": "object",
                    "required": ["messages"],
                    "properties": {
                        "messages": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/ChatMessage"},
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["flash", "low", "medium", "high"],
                            "default": "flash",
                            "description": (
                                "Omit this field to use the Flash model "
                                f"(`{checkpoint}`) at the medium decode budget. "
                                "low, medium, and high keep the Flash checkpoint."
                            ),
                        },
                        "stream": {
                            "type": "boolean",
                            "default": False,
                            "description": "When true, the reply is server-sent events.",
                        },
                        "think": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                            "description": (
                                "Optional thinking level. When set, it replaces "
                                "the level implied by mode. It does not change "
                                "the Flash checkpoint. Reasoning comes back on "
                                "reasoning_content, not in content."
                            ),
                        },
                        "model": {
                            "type": "string",
                            "description": (
                                "Ignored. Omitted mode selects Flash, and every "
                                "listed mode stays on the Flash checkpoint."
                            ),
                        },
                    },
                },
                "ChatResponse": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "object": {"type": "string"},
                        "model": {
                            "type": "string",
                            "description": "Public model name. Flash is `flash`.",
                            "example": "flash",
                        },
                        "mode": {
                            "type": "string",
                            "example": "flash",
                        },
                        "checkpoint": {
                            "type": "string",
                            "description": "Ollama tag used for a miss.",
                            "example": checkpoint,
                        },
                        "choices": {"type": "array", "items": {"type": "object"}},
                        "pi_model": {"type": "string"},
                        "pi_peer": {"type": "string"},
                        "pi_chip": {"type": "string"},
                        "pi_think": {"type": "string"},
                    },
                },
                "Health": {
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean"},
                        "model": {
                            "type": "string",
                            "description": "Fleet checkpoint tag.",
                        },
                        "pro_model": {
                            "type": "string",
                            "description": "Pro tag. Both tags stay resident.",
                            "example": "qwen3:1.7b",
                        },
                        "public_model": {
                            "type": "string",
                            "example": "flash",
                        },
                        "default_mode": {
                            "type": "string",
                            "example": "flash",
                        },
                        "checkpoint": {"type": "string"},
                        "peers_up": {"type": "integer"},
                        "peers": {"type": "array", "items": {"type": "object"}},
                        "slots": {"type": "integer"},
                        "in_flight": {"type": "integer"},
                        "cache_ttl": {"type": "number"},
                        "uptime_s": {"type": "integer"},
                        "services": {"type": "object"},
                    },
                },
            },
        },
    }


def openapi_bytes() -> bytes:
    return json.dumps(openapi_document(), indent=2).encode("utf-8")


def swagger_html() -> bytes:
    """Swagger UI for /openapi.json, with the same text if the script cannot load."""
    checkpoint = flash_checkpoint()
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pi GPT Swagger UI</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui.css">
  <style>
    body {{ margin: 0; font-family: "Source Serif 4", Georgia, serif; color: #1c1917; background: #fafaf9; }}
    #fallback {{ max-width: 42rem; margin: 0 auto; padding: 2rem 1.25rem 4rem; }}
    h1 {{ font-size: 1.75rem; font-weight: 600; letter-spacing: -0.02em; }}
    code, pre {{ font-family: ui-monospace, monospace; font-size: 0.92rem; }}
    pre {{ background: #fff; border: 1px solid #e7e5e4; padding: 0.9rem 1rem; overflow: auto; }}
    a {{ color: #9a3412; }}
    .muted {{ color: #57534e; }}
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <article id="fallback">
    <h1>Pi GPT Swagger UI</h1>
    <p class="muted">This page is the Swagger UI for <a href="/openapi.json">/openapi.json</a>. The script below loads the UI from the OpenAPI document. The text here is the same contract when that script is unavailable.</p>
    <h2>Auth</h2>
    <p>Set <code>{API_KEY_ENV}</code> on the router. Send it on chat and health as <code>Authorization: Bearer &lt;key&gt;</code> or <code>X-API-Key: &lt;key&gt;</code>. An unset variable returns 503. A wrong key returns 401. The docs and the OpenAPI JSON do not require the key. Do not put the key in a query string.</p>
    <h2>Chat</h2>
    <p><code>POST /api/chat</code>. If <code>mode</code> is omitted, the model is Flash (<code>{checkpoint}</code> on pi4) at the medium budget. <code>low</code>, <code>medium</code>, and <code>high</code> stay on that checkpoint. A full inference cap waits in a queue of 8 and the page shows Waiting for a free slot. A full queue returns 503, the same short message as the page. A map hit does not take a slot.</p>
    <pre>curl -sS http://127.0.0.1:18080/api/chat \\
  -H 'content-type: application/json' \\
  -H 'authorization: Bearer YOUR_KEY' \\
  -d '{{"messages":[{{"role":"user","content":"status"}}]}}'</pre>
    <h2>Health</h2>
    <p><code>GET /api/health</code> with the same key. <code>public_model</code> is <code>flash</code>.</p>
  </article>
  <script src="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
  <script>
    if (window.SwaggerUIBundle) {{
      document.getElementById("fallback").hidden = true;
      window.ui = SwaggerUIBundle({{
        url: "/openapi.json",
        dom_id: "#swagger-ui",
        deepLinking: true,
        presets: [SwaggerUIBundle.presets.apis],
      }});
    }}
  </script>
</body>
</html>
"""
    return page.encode("utf-8")
