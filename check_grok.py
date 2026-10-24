"""Check that RecallAI can talk to Grok, step by step.

    python check_grok.py

Each step prints OK or tells you exactly what to fix.
"""

import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OK, FAIL, WARN = "[ OK ]", "[FAIL]", "[WARN]"


def fail(msg, fix):
    print(f"{FAIL} {msg}\n       Fix: {fix}")
    sys.exit(1)


def main():
    print(f"Python {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < (3, 9):
        fail("Python is too old.", "Install Python 3.10 or newer.")

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

    # 2. API key
    env_key = os.environ.get("XAI_API_KEY", "").strip()
    import llm  # loads .env (without overriding a real environment variable)
    key = os.environ.get("XAI_API_KEY", "").strip()
    env_file = BASE_DIR / ".env"
    if not key:
        hint = ""
        if os.name == "nt":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                    if winreg.QueryValueEx(k, "XAI_API_KEY")[0]:
                        hint = (" (setx saved it, but this terminal was opened before "
                                "that - close the terminal/VS Code completely and reopen)")
            except OSError:
                pass
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

    # 4. A real tool call, the way RecallAI uses Grok
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

    # 5. Vision
    import numpy as np
    img = np.zeros((64, 64, 3), np.uint8)
    img[:, :] = (0, 0, 255)  # red square (BGR)
    t = time.time()
    resp = client.responses.create(model=llm.VISION_MODEL, input=[{
        "role": "user", "content": [llm.image_part(img),
                                    llm.text_part("What colour is this? One word.")]}])
    print(f"{OK} Grok vision works ({time.time() - t:.1f}s): {llm.output_text(resp)!r}")

    print("\nAll good. Start RecallAI with:\n"
          "  uvicorn server:app --host 0.0.0.0 --port 8000\n"
          "  python webcam.py\n"
          '  python agent.py "where is my mouse?"')


if __name__ == "__main__":
    main()
