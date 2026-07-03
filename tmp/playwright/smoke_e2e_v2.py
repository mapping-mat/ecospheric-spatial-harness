"""
E2E smoke test v2: type a prompt, wait for completion, click artifacts to load on map.

Flow:
1. Load page, wait for session
2. Type prompt, click Send
3. Wait for completion (up to 300s)
4. Click the last artifact (buffer output) to load it on the map
5. Wait for the vector layer to render
6. Screenshot
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
                await page.screenshot(path=str(SCREENSHOT_DIR / "smoke-v2-timeout.png"), full_page=True)
                return

            # Wait for artifact panel to populate
            print("6. Waiting for artifacts to populate…")
            await asyncio.sleep(2)

            # Click the last artifact entry (the buffer output — most interesting)
            entries = await page.query_selector_all(".artifact-entry")
            print(f"   Found {len(entries)} artifacts in panel")

            if len(entries) >= 1:
                # Click the last one (buffer output)
                last_entry = entries[-1]
                await last_entry.click()
                print("   ✓ Clicked last artifact")
                await asyncio.sleep(3)  # wait for layer to load + render

            # Also click the first one (search output) for comparison
            if len(entries) >= 2:
                first_entry = entries[0]
                await first_entry.click()
                print("   ✓ Clicked first artifact too")
                await asyncio.sleep(3)

            # Screenshot
            screenshot_path = SCREENSHOT_DIR / "smoke-v2-with-layers.png"
            await page.screenshot(path=str(screenshot_path), full_page=True)
            print(f"7. Screenshot saved: {screenshot_path}")

            # Capture messages
            messages = await page.eval_on_selector_all(
                ".msg",
                "els => els.map(el => ({class: el.className, text: el.textContent?.trim() || ''}))"
            )
            print(f"\n=== Messages ({len(messages)}) ===")
            for m in messages:
                print(f"  [{m['class']}] {m['text'][:100]}")

            print("\n=== Console Logs ===")
            print("\n".join(console_logs[-10:]) or "(none)")

        except Exception as err:
            print(f"SMOKE TEST FAILED: {err}")
            await page.screenshot(path=str(SCREENSHOT_DIR / "smoke-v2-failure.png"), full_page=True)
            raise
        finally:
            await browser.close()

asyncio.run(main())
