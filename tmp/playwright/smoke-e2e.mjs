/**
 * E2E smoke test: type a prompt into the ESP frontend, verify SSE flow.
 *
 * Flow:
 * 1. Load page at http://127.0.0.1:5173
 * 2. Wait for session creation message
 * 3. Type a spatial prompt into the chat input
 * 4. Click Send
 * 5. Wait for SSE events — tool_call, artifact, done/error
 * 6. Screenshot the final state
 * 7. Assert that we got either a "Complete ✓" or an error message (not a hang)
 */

import { chromium } from 'playwright';
import { writeFileSync } from 'fs';

const BASE = 'http://127.0.0.1:5173';
const SCREENSHOT_DIR = '/home/emma/.openclaw/workspace/projects/ecospheric-spatial-harness/tmp/playwright';

const PROMPT = 'Search for buildings near Chico, CA and buffer them by 500 meters';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });

// Collect console messages
const consoleLogs = [];
page.on('console', (msg) => {
  consoleLogs.push(`[${msg.type()}] ${msg.text()}`);
});

// Collect network errors
const networkErrors = [];
page.on('requestfailed', (req) => {
  networkErrors.push(`${req.url()} — ${req.failure()?.errorText}`);
});

let result = { status: 'unknown', details: '', screenshot: '' };

try {
  console.log('1. Loading page…');
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 15000 });

  // 2. Wait for session creation — the system message "Session ready: …"
  console.log('2. Waiting for session creation…');
  const sessionMsg = await page.waitForSelector('text=/Session ready:/i', { timeout: 10000 });
  console.log('   ✓ Session created');

  // 3. Type prompt
  console.log('3. Typing prompt…');
  const input = page.locator('#chat-input');
  await input.fill(PROMPT);

  // 4. Click Send
  console.log('4. Clicking Send…');
  await page.click('#chat-send');

  // 5. Wait for either "Complete ✓" or error message
  // The SSE stream can take a while (model + tool execution), give it 120s
  console.log('5. Waiting for completion (up to 120s)…');

  const startTime = Date.now();
  let completed = false;

  while (Date.now() - startTime < 120000) {
    // Check for completion
    const completeEl = await page.$('.msg-system:has-text("Complete")');
    if (completeEl) {
      result.status = 'success';
      result.details = 'Got "Complete ✓" message';
      completed = true;
      break;
    }

    // Check for error
    const errorEl = await page.$('.msg-error');
    if (errorEl) {
      const text = await errorEl.textContent();
      result.status = 'error';
      result.details = `Error message: ${text}`;
      completed = true;
      break;
    }

    // Check for tool_call events (progress indicator)
    const toolMsg = await page.$('.msg-system:has-text("Tool:")');
    if (toolMsg) {
      const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
      console.log(`   Progress: tool call detected at ${elapsed}s`);
    }

    await page.waitForTimeout(1000);
  }

  if (!completed) {
    result.status = 'timeout';
    result.details = 'No completion or error after 120s';
  }

  // 6. Screenshot
  const screenshotPath = `${SCREENSHOT_DIR}/smoke-result.png`;
  await page.screenshot({ path: screenshotPath, fullPage: true });
  result.screenshot = screenshotPath;
  console.log(`6. Screenshot saved: ${screenshotPath}`);

  // 7. Capture the message log contents
  const messages = await page.$$eval('.msg', (els) =>
    els.map((el) => ({ class: el.className, text: el.textContent?.trim() || '' }))
  );
  result.messages = messages;

  console.log('\n=== RESULT ===');
  console.log(JSON.stringify(result, null, 2));

  console.log('\n=== Console Logs ===');
  console.log(consoleLogs.join('\n') || '(none)');

  console.log('\n=== Network Errors ===');
  console.log(networkErrors.join('\n') || '(none)');

} catch (err) {
  console.error('SMOKE TEST FAILED:', err.message);
  await page.screenshot({ path: `${SCREENSHOT_DIR}/smoke-failure.png`, fullPage: true });
  console.log('Console logs:', consoleLogs.join('\n'));
  console.log('Network errors:', networkErrors.join('\n'));
  process.exitCode = 1;
} finally {
  await browser.close();
}
