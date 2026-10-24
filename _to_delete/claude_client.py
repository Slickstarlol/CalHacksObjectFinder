"""Shared Claude (Anthropic API) setup for RecallAI.

Set your key once before running anything:
    Windows (PowerShell):  setx ANTHROPIC_API_KEY "sk-ant-..."   (then open a new terminal)
    macOS/Linux:           export ANTHROPIC_API_KEY=sk-ant-...

Without a key everything still runs; the Claude features are just skipped.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent

# Fast + cheap model for the per-sighting vision call (runs every ~15 s).
VISION_MODEL = os.environ.get("RECALL_VISION_MODEL", "claude-haiku-5-5")
# Smarter model for answering questions and deciding on alerts.
AGENT_MODEL = os.environ.get("RECALL_AGENT_MODEL", "claude-sonnet-5-5")

MAX_IMAGE_SIDE = 768  # downscale before sending: faster and cheaper


def get_client():
    """anthropic.Anthropic() if a key is configured, else None."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        print("[claude] pip install anthropic  to enable Claude features")
        return None
    return anthropic.Anthropic()


def image_block(image, max_side=MAX_IMAGE_SIDE):
    """Build an image content block from a path (relative to the project
    folder or absolute) or a BGR numpy array."""
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
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg",
                   "data": base64.b64encode(buf.tobytes()).decode()},
    }


def block_to_dict(block):
    """SDK content block (or plain dict) -> dict, so it can be sent back."""
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    return dict(vars(block))


def text_of(response):
    return "".join(b.text for b in response.content
                   if getattr(b, "type", None) == "text").strip()


def tool_input(response, name):
    """Input of the first tool_use block called `name`, or None."""
    for b in response.content:
        if getattr(b, "type", None) == "tool_use" and b.name == name:
            return b.input
    return None
