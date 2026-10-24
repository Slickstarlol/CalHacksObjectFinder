# RecallAI – CalHacks Object Finder

Remembers where **your** belongings were last seen. YOLO finds objects, DINOv2
checks whether each one is yours (compared with photos you register), and each
sighting is saved with a snapshot, a close-up, nearby objects, and optional
phone GPS.

## Setup
```
pip install -r requirements.txt
setx XAI_API_KEY "xai-..."     # Windows; key from https://console.x.ai, then open a new terminal
```
The first run downloads DINOv2 (~90 MB) from Hugging Face. Without an API key
everything still runs; the Grok features are skipped (alerts use a plain template).

## Grok as the agent
Uses xAI's Responses API through the OpenAI Python SDK (`base_url=https://api.x.ai/v1`), model `grok-4.7`.

| Capability | How | File |
|---|---|---|
| Recognizes **your** specific item | DINOv2 match, then Grok compares the frame with your reference photos; sightings it rejects are hidden | `recognition.py`, `interpreter.py` |
| Interprets where it was last seen | Grok writes a sentence ("On the gray table, left of the laptop") and a place label for each sighting | `interpreter.py` |
| Searches historical memories | Agent tools: `list_belongings`, `search_sightings` (by item, text, time window), `get_last_seen`, `look_at_photo`, `get_context` | `agent.py` |
| Remembers *when* it saw each item | Python computes every time and duration from the sightings table (Grok only phrases them): last seen + time since, "where was it at 10 AM?" (latest sighting at or before), time windows, and "what haven't you seen in 30 min?". Agent tools `get_last_seen` (new) and `list_belongings(not_seen_for_minutes)`; `GET /api/belongings/{id}/last-seen[?at=ISO]`; live "Last seen 23 minutes ago" labels on /search | `temporal_memory.py` |
| Understands natural-language questions | "Ask RecallAI" box on /search; `POST /api/ask` | `agent.py`, `server.py` |
| Suggests where to look and why | System prompt: best spot + evidence + backup places, citing sightings `[#id]` with photos | `agent.py` |
| Proactively flags forgotten items | Rules (moved >150 m from item / out of view 10+ min); Grok decides and writes the alert; shown on /search and the phone GPS page | `watcher.py` |

Try it from the terminal: `python agent.py "where did I leave my mouse?"`
Describe sightings saved before the key was set: `python interpreter.py --backfill`
Models can be changed with `RECALL_VISION_MODEL` / `RECALL_AGENT_MODEL`.

## Use
1. **Register belongings** (5–8 photos each, different angles and lighting):
   ```
   python register.py --list                 # what's registered / needs photos
   python register.py --id 3617519a583a      # add photos to "My Bag"
   python register.py --name "My Keys" --category "cell phone"
   ```
   In the window: **S** saves an auto-cropped photo (green box), **B** lets you draw the box, **Q** finishes.
2. **Start the server** (search page + optional phone GPS):
   `uvicorn server:app --host 0.0.0.0 --port 8000`
3. **Start the camera:** `python webcam.py` (`--camera 0`, `--threshold 0.75`)
4. **Search:** open http://localhost:8000/search

## How it decides
| Event | When |
|---|---|
| `seen` | Item is in view; saved at most every 15 s |
| `left_view` | Item has been out of view for 5 s; keeps the **last frame it was visible in** |

Match threshold (DINOv2): 0.70. Measured on this project's own frames:
same item across frames 0.77–0.95, other chairs in the same room ≤0.64,
strangers' objects from COCO ≤0.50. If someone else's item gets labelled as
yours, raise `--threshold`. If yours is missed, add more reference photos.

YOLO confuses some classes (a gaming mouse up close came out as
"cell phone"/"remote"), so a belonging is also checked against those related
classes. See `CONFUSABLE_GROUPS` in `recognition.py`.

## Files
- `webcam.py`: camera loop (YOLO, then recognition, then recorder)
- `recognition.py`: "is it mine?" matcher (DINOv2 / CLIP / colour-histogram fallback)
- `recorder.py`: decides what to save and when
- `register.py`: register belongings and their reference photos
- `db.py`: database schema; upgrades the old `memory.db` in place
- `llm.py`, `interpreter.py`, `agent.py`, `watcher.py`: Grok features
- `temporal_memory.py`: all time/duration math (last seen, at-time recall, inventory)
- `server.py`, `search.py`, `search.html`, `gps.html`: web app

## Tests
```
pytest -m "not slow"   # fast tests (Grok is mocked), no downloads
pytest                 # everything, incl. real-frame YOLO + DINOv2 tests
```
