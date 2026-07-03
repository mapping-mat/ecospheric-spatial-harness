"""
E2E smoke test: type a prompt through the ESP frontend, verify SSE flow.

Flow:
1. Load page at http://127.0.0.1:5173
2. Wait for session creation message
3. Type a spatial prompt into the chat input
4. Click Send
5. Wait for SSE events — tool_call, artifact, done/error
6. Screenshot the final state
7. Assert that we got either "Complete" or an error message (not a hang)
"""

import asyncio
import json
import time
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:5173"
SCREENSHOT_DIR = Path("/home/emma/.openclaw/workspace/projects/ecospheric-spatial-harness/tmp/playwright")
PROMPT = "Search for buildings near Chico, CA and buffer them by 500 meters"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        console_logs = []
        page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))

        network_errors = []
        page.on("requestfailed", lambda req: network_errors.append(f"{req.url} — {req.failure}"))

        try:
            print("1. Loading page…")
            await page.goto(BASE, wait_until="networkidle", timeout=15000)

            print("2. Waiting for session creation…")
            await page.wait_for_selector("text=/Session ready:/i", timeout=10000)
            print("   ✓ Session created")

            print("3. Typing prompt…")
            await page.fill("#chat-input", PROMPT)

            print("4. Clicking Send…")
            await page.click("#chat-send")

            print("5. Waiting for completion (up to 120s)…")
            start = time.time()
            completed = False
            result = {"status": "unknown", "details": ""}

            while time.time() - start < 300:
                # Check for completion
                complete = await page.query_selector('.msg-system:has-text("Complete")')
                if complete:
                    result = {"status": "success", "details": 'Got "Complete ✓" message'}
                    completed = True
                    break

                # Check for error
                error_el = await page.query_selector(".msg-error")
                if error_el:
                    text = await error_el.text_content()
                    result = {"status": "error", "details": f"Error: {text}"}
                    completed = True
                    break

                # Progress check
                tool_msg = await page.query_selector('.msg-system:has-text("Tool:")')
                if tool_msg:
                    elapsed = time.time() - start
                    if int(elapsed) % 10 == 0:
                        print(f"   Progress: tool call detected at {elapsed:.1f}s")

                await asyncio.sleep(1)

            if not completed:
                result = {"status": "timeout", "details": "No completion or error after 120s"}

            # Screenshot
            screenshot_path = SCREENSHOT_DIR / "smoke-result.png"
            await page.screenshot(path=str(screenshot_path), full_page=True)
            print(f"6. Screenshot saved: {screenshot_path}")

            # Capture message log
            messages = await page.eval_on_selector_all(
                ".msg",
                "els => els.map(el => ({class: el.className, text: el.textContent?.trim() || ''}))"
            )
            result["messages"] = messages

            print("\n=== RESULT ===")
            print(json.dumps(result, indent=2))

            print("\n=== Console Logs ===")
            print("\n".join(console_logs) or "(none)")

            print("\n=== Network Errors ===")
            print("\n".join(network_errors) or "(none)")

        except Exception as err:
            print(f"SMOKE TEST FAILED: {err}")
            screenshot_path = SCREENSHOT_DIR / "smoke-failure.png"
            await page.screenshot(path=str(screenshot_path), full_page=True)
            print("Console logs:", "\n".join(console_logs))
            print("Network errors:", "\n".join(network_errors))
            raise
        finally:
            await browser.close()

asyncio.run(main())
