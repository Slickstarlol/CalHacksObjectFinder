"""Grok (xAI API) setup for RecallAI.

Uses xAI's Responses API through the official OpenAI Python SDK, as in
xAI's own docs (base URL https://api.x.ai/v1).

Set your key once (get one at https://console.x.ai):
    Windows:      setx XAI_API_KEY "xai-..."     (then open a new terminal)
    macOS/Linux:  export XAI_API_KEY=xai-...

Without a key everything still runs; the Grok features are just skipped.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
XAI_BASE_URL = "https://api.x.ai/v1"

# One model for everything by default; override per role if you like.
VISION_MODEL = os.environ.get("RECALL_VISION_MODEL", "grok-4.7")
AGENT_MODEL = os.environ.get("RECALL_AGENT_MODEL", "grok-4.7")

MAX_IMAGE_SIDE = 768  # downscale before sending: faster and cheaper


def get_client():
    """OpenAI SDK client pointed at xAI, or None if no key is set."""
    key = os.environ.get("XAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        print("[grok] pip install openai  to enable Grok features")
        return None
    return OpenAI(api_key=key, base_url=XAI_BASE_URL, timeout=60)


# ---------------------------------------------------------------------------
# Building requests
# ---------------------------------------------------------------------------
def image_part(image, max_side=MAX_IMAGE_SIDE, detail="high"):
    """input_image part from a path (relative to the project or absolute)
    or a BGR numpy array, sent as a base64 JPEG data URL."""
    if not isinstance(image, np.ndarray):
        path = Path(image)
        if not path.is_absolute():
            path = BASE_DIR / path
        image = cv2.imread(str(path))
        if image is None:
            raise FileNotFoundError(path)
    h, w = image.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1:
        image = cv2.resize(image, (int(w * scale), int(h * scale)),
                           interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    data = base64.b64encode(buf.tobytes()).decode()
    return {"type": "input_image",
            "image_url": f"data:image/jpeg;base64,{data}", "detail": detail}


def text_part(text):
    return {"type": "input_text", "text": text}


def function_tool(name, description, parameters):
    return {"type": "function", "name": name, "description": description,
            "parameters": parameters}


# ---------------------------------------------------------------------------
# Reading responses (works with SDK objects and plain dicts)
# ---------------------------------------------------------------------------
def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def function_calls(response):
    """[(call_id, name, args_dict), ...] for every function call in the output."""
    calls = []
    for item in _get(response, "output", []) or []:
        if _get(item, "type") != "function_call":
            continue
        raw = _get(item, "arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except json.JSONDecodeError:
            args = {}
        calls.append((_get(item, "call_id"), _get(item, "name"), args))
    return calls


def output_text(response):
    parts = []
    for item in _get(response, "output", []) or []:
        if _get(item, "type") != "message":
            continue
        for c in _get(item, "content", []) or []:
            if _get(c, "type") in ("output_text", "text"):
                parts.append(_get(c, "text", ""))
    return "".join(parts).strip()


def call_tool(client, model, content, tool, max_output_tokens=4096):
    """One request that must answer by calling `tool`; returns its arguments.

    tool_choice="required" with a single tool forces exactly that tool.
    """
    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": content}],
        tools=[tool],
        tool_choice="required",
        max_output_tokens=max_output_tokens,
    )
    for _, name, args in function_calls(response):
        if name == tool["name"]:
            return args
    raise RuntimeError(f"Grok did not call {tool['name']}")
