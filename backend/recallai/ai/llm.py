"""Grok (xAI API) setup for RecallAI.

Uses xAI's Responses API through the official OpenAI Python SDK, as in
xAI's own docs (base URL https://api.x.ai/v1).

Put your key (from https://console.x.ai) in a file named .env at the
repository root - it is git-ignored, so it never gets pushed:
    XAI_API_KEY=xai-...
An XAI_API_KEY environment variable also works and takes priority.
Run `python -m recallai.ai.check_grok` to test the whole setup.

Without a key everything still runs; the Grok features are just skipped.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import cv2
import numpy as np

from recallai import paths

# Relative image paths ("snapshots/x.jpg", as stored in SQLite) resolve here.
DATA_DIR = paths.DATA_DIR
XAI_BASE_URL = "https://api.x.ai/v1"
ENV_FILE = paths.ENV_FILE   # .env at the repository root


def load_env_file(path=ENV_FILE):
    """Minimal .env reader (KEY=value lines). Real environment variables win.
    Returns the keys it set."""
    loaded = []
    try:
        text = Path(path).read_text(encoding="utf-8-sig")  # tolerate Notepad's BOM
    except OSError:
        return loaded
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value and not os.environ.get(key):
            os.environ[key] = value
            loaded.append(key)
    return loaded


load_env_file()

# One model for everything by default; override per role if you like.
VISION_MODEL = os.environ.get("RECALL_VISION_MODEL", "grok-4.7")
AGENT_MODEL = os.environ.get("RECALL_AGENT_MODEL", "grok-4.7")

MAX_IMAGE_SIDE = 768  # downscale before sending: faster and cheaper


def get_client():
    """OpenAI SDK client pointed at xAI, or None if no key is set."""
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        print("[grok] pip install openai  to enable Grok features")
        return None
    try:
        client = OpenAI(api_key=key, base_url=XAI_BASE_URL, timeout=60)
    except Exception as e:  # e.g. old openai + new httpx ("proxies")
        print(f'[grok] openai package is broken/too old ({e}): pip install -U "openai>=1.66"')
        return None
    if not hasattr(client, "responses"):
        print('[grok] your openai package is too old: pip install -U "openai>=1.66"')
        return None
    return client


# ---------------------------------------------------------------------------
# Building requests
# ---------------------------------------------------------------------------
def image_part(image, max_side=MAX_IMAGE_SIDE, detail="high"):
    """input_image part from a path (relative to the project or absolute)
    or a BGR numpy array, sent as a base64 JPEG data URL."""
    if not isinstance(image, np.ndarray):
        path = Path(image)
        if not path.is_absolute():
            path = DATA_DIR / path
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
