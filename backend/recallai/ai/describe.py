"""Grok describes a person from the camera's photos of them.

Clicking a person in the live dashboard's People panel calls
POST /api/people/{id}/describe. Grok looks at the newest photos the camera
saved when they were named on the live feed (data/people/<id>/, see
vision/people_id.py) and answers with what they look like (clothes, colours,
hair, things they carry) so you can spot them, plus whether every photo shows
the same person, since a camera label can be wrong. It describes what is
visible; it never guesses who someone is or anything sensitive about them.

The answer is cached next to the photos (grok.json) until the photos change.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from recallai.ai.llm import VISION_MODEL, call_tool, function_tool, image_part, text_part

MAX_PHOTOS = 4
CACHE_NAME = "grok.json"

TOOL = function_tool(
    "describe_person",
    "Record what this person looks like.",
    {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "3-6 words to spot them at a glance, "
                               "e.g. 'Black hoodie, round glasses'.",
            },
            "description": {
                "type": "string",
                "description": "One or two sentences: clothing and colours, hair, "
                               "glasses or hats, bags or anything they carry.",
            },
            "same_person": {
                "type": "string", "enum": ["yes", "no", "unsure"],
                "description": "Do all the photos show the same person?",
            },
        },
        "required": ["summary", "description", "same_person"],
    },
)

PROMPT = """You are Khris, the visual memory of RecallAI. {owner} named this person "{name}"{relationship} on the live camera.
The images are camera crops saved when they were named, newest first.

Describe what {name} looks like so {owner} can spot them in a crowded room: clothing and colours, hair, glasses or hats, bags or anything they carry. Only describe what is visible.
Do not guess who they are, and don't mention age, ethnicity, health, body size or other sensitive traits.
Also say whether every photo shows the same person (camera labels can be wrong).
Call describe_person."""


def _key(photos):
    return [Path(p).name for p in photos]


def cached(folder, photos):
    """The saved description if it was made from exactly these photos."""
    try:
        data = json.loads((Path(folder) / CACHE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("photos") == _key(photos) else None


def save_cache(folder, photos, result):
    data = {**result, "photos": _key(photos), "at": datetime.now(timezone.utc).isoformat()}
    try:
        (Path(folder) / CACHE_NAME).write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass
    return data


def describe(client, person, photos, owner="the user", model=VISION_MODEL):
    """One Grok call. `person` is a people row (name, relationship);
    `photos` are image paths, newest first. Returns summary, description
    and same_person ("yes" / "no" / "unsure")."""
    relationship = f" ({person['relationship']})" if person.get("relationship") else ""
    content = [image_part(p, max_side=384) for p in photos[:MAX_PHOTOS]]
    content.append(text_part(PROMPT.format(owner=owner, name=person["name"],
                                           relationship=relationship)))
    args = call_tool(client, model, content, TOOL)
    same = args.get("same_person")
    return {
        "summary": str(args.get("summary") or "").strip()[:80],
        "description": str(args.get("description") or "").strip()[:600],
        "same_person": same if same in ("yes", "no", "unsure") else "unsure",
    }
