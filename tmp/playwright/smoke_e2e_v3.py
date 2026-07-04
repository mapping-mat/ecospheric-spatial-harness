"""
E2E smoke test v3: type a prompt, wait for completion, click artifacts, capture network.
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

        # Capture ALL network responses
        responses_log = []
        async def on_response(response):
            url = response.url
            if '/api/' in url:
                status = response.status
                try:
                    body = await response.text()
                    body_snippet = body[:300] if body else "(empty)"
                except:
                    body_snippet = "(could not read body)"
                responses_log.append(f"[{status}] {url}\n  body: {body_snippet}")
        page.on("response", on_response)

        console_logs = []
        page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))

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

            print("5. Waiting for completion (up to 300s)…")
            start = time.time()
            completed = False

            while time.time() - start < 300:
                complete = await page.query_selector('.msg-system:has-text("Complete")')
                if complete:
                    print(f"   ✓ Complete in {time.time()-start:.1f}s")
                    completed = True
                    break
                error_el = await page.query_selector(".msg-error")
                if error_el:
                    text = await error_el.text_content()
                    print(f"   ✗ Error: {text}")
                    break
                await asyncio.sleep(2)

            if not completed:
                print("   ✗ Timed out")
                return

            # Wait for artifact panel
            print("6. Waiting for artifacts…")
            await asyncio.sleep(2)

            # Get artifact entries
            entries = await page.query_selector_all(".artifact-entry")
            print(f"   Found {len(entries)} artifacts")

            # Clear network log to focus on preview requests
            responses_log.clear()

            # Click each artifact and capture what happens
            for i, entry in enumerate(entries):
                # Get the artifact info
                info = await entry.evaluate("el => ({id: el.dataset.id, type: el.dataset.type, bbox: el.dataset.bbox})")
                print(f"   Artifact {i}: {info}")

                await entry.click()
                await asyncio.sleep(3)

                # Check for error messages
                error_msgs = await page.query_selector_all(".msg-error")
                for em in error_msgs:
                    text = await em.text_content()
                    if "preview" in text.lower() or "load" in text.lower():
                        print(f"   ✗ Error after click: {text}")

            # Final screenshot
            screenshot_path = SCREENSHOT_DIR / "smoke-v3-with-layers.png"
            await page.screenshot(path=str(screenshot_path), full_page=True)
            print(f"\n7. Screenshot saved: {screenshot_path}")

            # Print API responses
            print(f"\n=== API Responses ({len(responses_log)}) ===")
            for r in responses_log:
                print(r)

            print(f"\n=== Console Logs ===")
            for c in console_logs[-15:]:
                print(c)

        except Exception as err:
            print(f"FAILED: {err}")
            await page.screenshot(path=str(SCREENSHOT_DIR / "smoke-v3-failure.png"), full_page=True)
            raise
        finally:
            await browser.close()

asyncio.run(main())
