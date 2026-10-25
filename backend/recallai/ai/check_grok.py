"""Check that RecallAI can talk to Grok, or to Claude, step by step.

    python -m recallai.ai.check_grok            # Grok (xAI) — what agent.py uses
    python -m recallai.ai.check_grok --claude   # Claude (Anthropic): same tool call + photo
    python -m recallai.ai.check_grok --all      # both

Each step prints OK or tells you exactly what to fix.
"""

import os
import sys
import time

from recallai import paths
OK, FAIL, WARN = "[ OK ]", "[FAIL]", "[WARN]"

# Same roles as agent.py: a tool-calling model, and a vision model for photos.
# Sonnet answers; Haiku reads the snapshot (claude_client used these ids).
CLAUDE_AGENT_MODEL = os.environ.get("RECALL_CLAUDE_AGENT_MODEL", "claude-sonnet-5-5")
CLAUDE_VISION_MODEL = os.environ.get("RECALL_CLAUDE_VISION_MODEL", "claude-haiku-5-5")


def fail(msg, fix):
    print(f"{FAIL} {msg}\n       Fix: {fix}")
    sys.exit(1)


def registry_has(name):
    """True when setx stored `name` for this Windows user."""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return bool(winreg.QueryValueEx(k, name)[0])
    except OSError:
        return False


def check_python():
    print(f"Python {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < (3, 9):
        fail("Python is too old.", "Install Python 3.10 or newer.")


def check_grok(env_key=None):
    # 1. openai package (installed for THIS python?)
    try:
        import openai
    except ImportError:
        fail("The 'openai' package isn't installed for this Python.",
             f'"{sys.executable}" -m pip install openai tzdata')
    upgrade = f'"{sys.executable}" -m pip install -U "openai>=1.66"'
    try:
        usable = hasattr(openai.OpenAI(api_key="x"), "responses")
    except Exception as e:  # e.g. old openai + new httpx: unexpected 'proxies'
        fail(f"openai {openai.__version__} doesn't work here ({e}).", upgrade)
    if not usable:
        fail(f"openai {openai.__version__} is too old (no Responses API).", upgrade)
    print(f"{OK} openai {openai.__version__} installed")

    try:
        import tzdata  # noqa: F401
    except ImportError:
        if os.name == "nt":
            print(f"{WARN} tzdata missing (needed for local times on Windows): "
                  f'"{sys.executable}" -m pip install tzdata')

    # 2. API key. env_key is the value from before .env was loaded.
    if env_key is None:
        env_key = os.environ.get("XAI_API_KEY", "").strip()
    from recallai.ai import llm  # loads .env (without overriding a real environment variable)
    key = os.environ.get("XAI_API_KEY", "").strip()
    env_file = paths.ENV_FILE
    if not key:
        hint = ""
        if registry_has("XAI_API_KEY"):
            hint = (" (setx saved it, but this terminal was opened before "
                    "that - close the terminal/VS Code completely and reopen)")
        fail("No XAI_API_KEY found" + hint + ".",
             f"create {env_file} containing one line:  XAI_API_KEY=xai-...")
    source = "environment variable" if env_key else f".env file ({env_file})"
    print(f"{OK} API key found in {source}: {key[:8]}...{key[-4:]}")
    if env_key and env_file.exists():
        print(f"{WARN} The environment variable overrides .env. If it holds an old key, "
              "remove it: setx XAI_API_KEY \"\" and reopen the terminal.")
    if not key.startswith("xai-"):
        print(f"{WARN} xAI keys normally start with 'xai-'; check for a copy/paste error.")

    # 3. Reach xAI + key accepted + model available
    client = llm.get_client()
    try:
        ids = set()
        for m in client.models.list():
            ids.add(m.id)
            ids.update(getattr(m, "aliases", None) or [])
    except (openai.AuthenticationError, openai.BadRequestError) as e:
        fail(f"xAI rejected the key: {getattr(e, 'body', None) or e}",
             "create a new key at https://console.x.ai and put it in .env")
    except openai.PermissionDeniedError as e:
        fail(f"xAI refused access (403): {e}",
             "check your team has credits/billing at https://console.x.ai")
    except openai.APIConnectionError as e:
        fail(f"Can't reach api.x.ai: {e.__cause__ or e}",
             "check your internet; some campus/venue Wi-Fi blocks APIs - try a "
             "phone hotspot")
    except Exception as e:
        fail(f"Unexpected error talking to xAI: {type(e).__name__}: {e}",
             "send this message to whoever is helping you debug")
    print(f"{OK} Connected to xAI, key accepted ({len(ids)} models)")
    for role, model in (("agent", llm.AGENT_MODEL), ("vision", llm.VISION_MODEL)):
        if model not in ids:
            fail(f"Model '{model}' ({role}) isn't available to this key.",
                 f"set RECALL_{role.upper()}_MODEL to one of: "
                 + ", ".join(sorted(i for i in ids if i.startswith("grok-4"))[:6]))
    print(f"{OK} Models available: agent={llm.AGENT_MODEL}, vision={llm.VISION_MODEL}")

    # 4. A real tool call, the way RecallAI uses Grok (agent.py function tools)
    t = time.time()
    try:
        args = llm.call_tool(
            client, llm.AGENT_MODEL,
            [llm.text_part("Call the ping tool with word='pong'.")],
            llm.function_tool("ping", "Health check.", {
                "type": "object", "properties": {"word": {"type": "string"}},
                "required": ["word"]}))
    except openai.RateLimitError as e:
        fail(f"Rate limited / out of credits (429): {e}",
             "add credits at https://console.x.ai or wait a minute")
    except Exception as e:
        fail(f"Grok request failed: {type(e).__name__}: {e}",
             "send this message to whoever is helping you debug")
    print(f"{OK} Grok function call works ({time.time() - t:.1f}s): {args}")

    # 5. Vision — the same kind of photo question as look_at_photo
    import numpy as np
    img = np.zeros((64, 64, 3), np.uint8)
    img[:, :] = (0, 0, 255)  # red square (BGR)
    t = time.time()
    resp = client.responses.create(model=llm.VISION_MODEL, input=[{
        "role": "user", "content": [llm.image_part(img),
                                    llm.text_part("What colour is this? One word.")]}])
    print(f"{OK} Grok vision works ({time.time() - t:.1f}s): {llm.output_text(resp)!r}")

    print("\nAll good. Start RecallAI with:\n"
          "  python -m uvicorn recallai.api.server:app --host 0.0.0.0 --port 8000\n"
          "  python -m recallai.vision.webcam\n"
          '  python -m recallai.ai.agent "where is my mouse?"')


def _claude_text(response):
    return "".join(b.text for b in response.content
                   if getattr(b, "type", None) == "text").strip()


def _claude_tool_input(response, name):
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == name:
            return block.input
    return None


def check_claude(env_key=None):
    """Same two things agent.py asks of a model: call a tool, then read a photo.

    Claude's Messages API is not xAI's Responses API. Sonnet 5.5 also rejects
    a forced tool_choice, so this asks for the ping tool and requires a strict
    schema instead of tool_choice={"type": "tool"}.
    """
    print("\n--- Claude ---")
    try:
        import anthropic
    except ImportError:
        fail("The 'anthropic' package isn't installed for this Python.",
             f'"{sys.executable}" -m pip install anthropic')
    print(f"{OK} anthropic {anthropic.__version__} installed")

    # Snapshot the shell key before .env is applied (check_grok may already
    # have loaded it when this runs as part of --all).
    if env_key is None:
        env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    from recallai.ai import llm  # loads .env (without overriding a real environment variable)
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    env_file = paths.ENV_FILE
    if not key:
        hint = ""
        if registry_has("ANTHROPIC_API_KEY"):
            hint = (" (setx saved it, but this terminal was opened before "
                    "that - close the terminal/VS Code completely and reopen)")
        fail("No ANTHROPIC_API_KEY found" + hint + ".",
             f"create {env_file} containing one line:  ANTHROPIC_API_KEY=sk-ant-...")
    source = "environment variable" if env_key else f".env file ({env_file})"
    print(f"{OK} API key found in {source}: {key[:8]}...{key[-4:]}")
    if env_key and env_file.exists():
        print(f"{WARN} The environment variable overrides .env. If it holds an old key, "
              "remove it: setx ANTHROPIC_API_KEY \"\" and reopen the terminal.")
    if not key.startswith("sk-ant-"):
        print(f"{WARN} Anthropic keys normally start with 'sk-ant-'; "
              "check for a copy/paste error.")

    client = anthropic.Anthropic(api_key=key)
    if not hasattr(client, "models"):
        fail(f"anthropic {anthropic.__version__} is too old (no Models API).",
             f'"{sys.executable}" -m pip install -U anthropic')
    try:
        ids = {m.id for m in client.models.list()}
    except anthropic.AuthenticationError as e:
        fail(f"Anthropic rejected the key: {e}",
             "create a new key at https://platform.claude.com and put it in .env")
    except anthropic.PermissionDeniedError as e:
        fail(f"Anthropic refused access (403): {e}",
             "check billing at https://platform.claude.com")
    except anthropic.APIConnectionError as e:
        fail(f"Can't reach api.anthropic.com: {e.__cause__ or e}",
             "check your internet; some campus/venue Wi-Fi blocks APIs - try a "
             "phone hotspot")
    except Exception as e:
        fail(f"Unexpected error talking to Anthropic: {type(e).__name__}: {e}",
             "send this message to whoever is helping you debug")
    print(f"{OK} Connected to Anthropic, key accepted ({len(ids)} models)")

    # Read after .env is loaded. The module defaults were captured at import.
    agent_model = os.environ.get("RECALL_CLAUDE_AGENT_MODEL", CLAUDE_AGENT_MODEL)
    vision_model = os.environ.get("RECALL_CLAUDE_VISION_MODEL", CLAUDE_VISION_MODEL)
    for role, model, var in (
            ("agent", agent_model, "RECALL_CLAUDE_AGENT_MODEL"),
            ("vision", vision_model, "RECALL_CLAUDE_VISION_MODEL")):
        if model not in ids:
            suggestions = ", ".join(sorted(i for i in ids if i.startswith("claude-"))[:6])
            fail(f"Model '{model}' ({role}) isn't available to this key.",
                 f"set {var} to one of: {suggestions}")
    print(f"{OK} Models available: agent={agent_model}, vision={vision_model}")

    # Same ping the Grok check sends. Sonnet 5.5 rejects a forced tool_choice,
    # so strict=True is what makes the call match the schema.
    ping = {
        "name": "ping",
        "description": "Health check. Call this when asked to ping.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"word": {"type": "string"}},
            "required": ["word"],
        },
    }
    t = time.time()
    try:
        resp = client.messages.create(
            model=agent_model,
            max_tokens=1024,
            tools=[ping],
            messages=[{"role": "user",
                       "content": "Call the ping tool with word='pong'."}],
        )
    except anthropic.RateLimitError as e:
        fail(f"Rate limited / out of credits (429): {e}",
             "add credits at https://platform.claude.com or wait a minute")
    except Exception as e:
        fail(f"Claude request failed: {type(e).__name__}: {e}",
             "send this message to whoever is helping you debug")
    args = _claude_tool_input(resp, "ping")
    if args is None:
        fail(f"Claude did not call ping. It said: {_claude_text(resp)!r}",
             "retry; if it keeps answering in text, the model may not support tools")
    print(f"{OK} Claude tool call works ({time.time() - t:.1f}s): {args}")

    # Vision — one solid colour, same question as the Grok photo check.
    import base64
    import cv2
    import numpy as np
    img = np.zeros((64, 64, 3), np.uint8)
    img[:, :] = (0, 0, 255)  # red square (BGR)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        fail("Could not encode the test image.", "reinstall opencv-python")
    data = base64.b64encode(buf.tobytes()).decode()
    t = time.time()
    try:
        resp = client.messages.create(
            model=vision_model,
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/jpeg", "data": data}},
                    {"type": "text", "text": "What colour is this? One word."},
                ],
            }],
        )
    except Exception as e:
        fail(f"Claude vision failed: {type(e).__name__}: {e}",
             "send this message to whoever is helping you debug")
    print(f"{OK} Claude vision works ({time.time() - t:.1f}s): {_claude_text(resp)!r}")
    print("\nClaude can do the same tool call and photo question agent.py uses with Grok.\n"
          "The running agent still talks to Grok (XAI_API_KEY). This check only\n"
          "confirms the Anthropic key, models, tools, and vision.")


def main():
    args = sys.argv[1:]
    known = {"--claude", "--all", "--grok", "-h", "--help"}
    bad = [a for a in args if a not in known]
    if bad or "-h" in args or "--help" in args:
        print(__doc__.strip())
        if bad:
            fail(f"Unknown option: {' '.join(bad)}",
                 "use --claude, --grok, or --all")
        return

    check_python()
    # Capture shell keys before either check imports llm and applies .env.
    xai_from_env = os.environ.get("XAI_API_KEY", "").strip()
    anthropic_from_env = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    want_claude = "--claude" in args or "--all" in args
    want_grok = "--grok" in args or "--all" in args or not want_claude
    if want_grok:
        check_grok(xai_from_env)
    if want_claude:
        check_claude(anthropic_from_env)
    if want_grok and not want_claude:
        print("\nTo check Claude the same way:  python -m recallai.ai.check_grok --claude")


if __name__ == "__main__":
    main()
