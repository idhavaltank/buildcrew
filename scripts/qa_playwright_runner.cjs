#!/usr/bin/env node

/*
 * Narrow Playwright host bridge for AE QA workflows.
 *
 * The bridge accepts JSON over stdin, executes only the allowlisted actions,
 * and returns sanitized JSON over stdout. It never evaluates workflow text as
 * JavaScript and never prints page content, form values, headers, or cookies.
 */

const fs = require('fs');
const path = require('path');

function redact(value) {
  let text = String(value ?? '');
  text = text.replace(/(authorization\s*:\s*bearer\s+)[^\s]+/ig, '$1[REDACTED]');
  text = text.replace(/(password|token|secret|api[_-]?key)\s*[=:]\s*[^\s,;]+/ig, '$1=[REDACTED]');
  text = text.replace(/(?:[A-Za-z]:[\\/]|\/(?:home|tmp|var|workspace)\/)[^\s,;'\"]+/g, '[HOST_PATH]');
  return text.slice(0, 2000);
}

function safePart(value, label) {
  const text = String(value ?? '').trim();
  if (!text || text.length > 160 || !/^[A-Za-z0-9._:-]+$/.test(text)) {
    throw new Error(`${label} contains unsafe characters`);
  }
  return text;
}

function logicalRef(runId, stepId, suffix) {
  return `browser/${safePart(runId, 'runId')}/${safePart(stepId, 'stepId')}.${suffix}`;
}

function artifactPath(root, runId, stepId, suffix) {
  return path.join(root, safePart(runId, 'runId'), `${safePart(stepId, 'stepId')}.${suffix}`);
}

function originAllowed(value, allowedOrigins) {
  if (String(value).startsWith('data:')) return allowedOrigins.includes('data:');
  let parsed;
  try { parsed = new URL(value); } catch { return false; }
  return ['http:', 'https:'].includes(parsed.protocol) && allowedOrigins.includes(`${parsed.protocol}//${parsed.host}`);
}

function parseFillTarget(value) {
  let parsed;
  try { parsed = JSON.parse(value); } catch { throw new Error('fill entryPoint must be JSON {selector,value}'); }
  if (!parsed || typeof parsed.selector !== 'string' || typeof parsed.value !== 'string' || !parsed.selector.trim()) {
    throw new Error('fill entryPoint must contain selector and value');
  }
  return parsed;
}

function sanitizeUrl(value) {
  if (String(value).startsWith('data:')) return 'data:';
  try {
    const parsed = new URL(value);
    return `${parsed.protocol}//${parsed.host}${parsed.pathname}`.slice(0, 500);
  } catch {
    return '[INVALID_URL]';
  }
}

function writeJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(value, null, 2));
}

function resolvePlaywright(root) {
  const candidate = path.basename(root) === 'playwright' ? root : path.join(root, 'playwright');
  return require(candidate);
}

async function main(input) {
  const request = input.request || {};
  const steps = Array.isArray(input.steps) ? input.steps : [];
  const allowedOrigins = Array.isArray(input.allowedOrigins) ? input.allowedOrigins : [];
  const playwright = resolvePlaywright(input.playwrightRoot);
  const browserType = playwright[input.browserName];
  if (!browserType) throw new Error(`unsupported Playwright browser ${input.browserName}`);
  const launchOptions = { headless: input.headless !== false };
  if (input.executablePath) launchOptions.executablePath = input.executablePath;
  const browser = await browserType.launch(launchOptions);

  const runtimeIdentity = {
    name: String(request.targetIdentity?.name || 'browser-target'),
    version: String(await browser.version() || 'unknown'),
    build: `playwright:${String(await browser.version() || 'unknown')}`,
    environment: `browser:${input.browserName}`,
  };
  if (input.mode === 'probe') {
    await browser.close();
    return { status: 'AVAILABLE', runtimeIdentity };
  }

  const artifactsDir = path.resolve(input.artifactsDir);
  fs.mkdirSync(path.join(artifactsDir, safePart(request.runId, 'runId')), { recursive: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  const consoleLog = [];
  const networkLog = [];
  page.on('console', message => consoleLog.push({ type: message.type(), text: redact(message.text()) }));
  page.on('request', requestEvent => networkLog.push({ method: requestEvent.method(), url: sanitizeUrl(requestEvent.url()) }));
  page.on('requestfailed', requestEvent => networkLog.push({ method: requestEvent.method(), url: sanitizeUrl(requestEvent.url()), failed: true }));
  const wantsTrace = steps.some(step => (step.evidenceRequired || []).map(String).map(value => value.toLowerCase()).includes('trace'));
  let traceStopped = false;
  if (wantsTrace) await context.tracing.start({ screenshots: true, snapshots: true, sources: false });

  const stepResults = [];
  const artifactRefs = [];
  const blockers = [];
  try {
    for (const step of steps) {
      const stepId = String(step.stepId);
      const action = String(step.action || '').trim().toLowerCase();
      const required = new Set((Array.isArray(step.evidenceRequired) ? step.evidenceRequired : []).map(value => String(value).toLowerCase()));
      const evidenceRefs = [];
      let status = 'PASS';
      try {
        if (['open', 'navigate'].includes(action)) {
          if (!originAllowed(String(step.entryPoint), allowedOrigins)) throw new Error('browser URL is outside the approved origin allowlist');
          await page.goto(String(step.entryPoint), { waitUntil: 'domcontentloaded', timeout: Number(request.timeoutSeconds) * 1000 });
        } else if (action === 'click') {
          await page.locator(String(step.entryPoint)).click({ timeout: Number(request.timeoutSeconds) * 1000 });
        } else if (action === 'fill') {
          const target = parseFillTarget(String(step.entryPoint));
          await page.locator(target.selector).fill(target.value, { timeout: Number(request.timeoutSeconds) * 1000 });
        } else if (action === 'assert_visible') {
          await page.locator(String(step.entryPoint)).waitFor({ state: 'visible', timeout: Number(request.timeoutSeconds) * 1000 });
        } else if (action === 'assert_text') {
          const text = await page.locator(String(step.entryPoint)).textContent({ timeout: Number(request.timeoutSeconds) * 1000 });
          if (!String(text || '').includes(String(step.expected))) throw new Error('expected browser text was not found');
        } else if (action === 'assert_url') {
          if (!page.url().includes(String(step.expected))) throw new Error('expected browser URL was not found');
        } else if (action === 'reload') {
          await page.reload({ waitUntil: 'domcontentloaded', timeout: Number(request.timeoutSeconds) * 1000 });
        } else if (action === 'wait') {
          const milliseconds = Number(step.entryPoint);
          if (!Number.isFinite(milliseconds) || milliseconds < 0 || milliseconds > 30000) throw new Error('browser wait must be 0-30000 milliseconds');
          await page.waitForTimeout(milliseconds);
        } else if (action === 'screenshot') {
          // Evidence capture below writes the canonical step artifact.
        } else {
          throw new Error(`unsupported browser QA action: ${action}`);
        }

        if (required.has('screenshot') || action === 'screenshot') {
          const filePath = artifactPath(artifactsDir, request.runId, stepId, 'png');
          await page.screenshot({ path: filePath, fullPage: true });
          const ref = logicalRef(request.runId, stepId, 'png');
          evidenceRefs.push(ref);
          artifactRefs.push(ref);
        }
        const unsupported = [...required].filter(kind => !['screenshot', 'log', 'trace', 'network'].includes(kind));
        if (unsupported.length) throw new Error(`unsupported browser evidence kinds: ${unsupported.sort().join(', ')}`);
      } catch (error) {
        status = 'FAILED';
        const reason = redact(error?.message || error);
        blockers.push(`${stepId}: ${reason}`);
      }
      stepResults.push({ stepId, status, evidenceRefs: [...new Set(evidenceRefs)] });
    }

    const runId = safePart(request.runId, 'runId');
    if (steps.some(step => (step.evidenceRequired || []).map(String).map(value => value.toLowerCase()).includes('log'))) {
      const ref = `browser/${runId}/run.console.json`;
      writeJson(path.join(artifactsDir, runId, 'run.console.json'), consoleLog);
      artifactRefs.push(ref);
      stepResults.forEach((result, index) => {
        if ((steps[index].evidenceRequired || []).map(String).map(value => value.toLowerCase()).includes('log')) result.evidenceRefs.push(ref);
      });
    }
    if (steps.some(step => (step.evidenceRequired || []).map(String).map(value => value.toLowerCase()).includes('network'))) {
      const ref = `browser/${runId}/run.network.json`;
      writeJson(path.join(artifactsDir, runId, 'run.network.json'), networkLog);
      artifactRefs.push(ref);
      stepResults.forEach((result, index) => {
        if ((steps[index].evidenceRequired || []).map(String).map(value => value.toLowerCase()).includes('network')) result.evidenceRefs.push(ref);
      });
    }
    if (wantsTrace) {
      const ref = `browser/${runId}/run.trace.zip`;
      await context.tracing.stop({ path: path.join(artifactsDir, runId, 'run.trace.zip') });
      traceStopped = true;
      artifactRefs.push(ref);
      stepResults.forEach((result, index) => {
        if ((steps[index].evidenceRequired || []).map(String).map(value => value.toLowerCase()).includes('trace')) result.evidenceRefs.push(ref);
      });
    }
    const status = stepResults.every(result => result.status === 'PASS') && blockers.length === 0 ? 'PASS' : 'FAILED';
    return { status, targetIdentity: runtimeIdentity, stepResults, artifactRefs: [...new Set(artifactRefs)], blockers: [...new Set(blockers)] };
  } finally {
    if (wantsTrace && !traceStopped) {
      try { await context.tracing.stop(); } catch {}
    }
    await context.close();
    await browser.close();
  }
}

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { input += chunk; });
process.stdin.on('end', async () => {
  try {
    const parsed = JSON.parse(input);
    const result = await main(parsed);
    process.stdout.write(JSON.stringify(result));
  } catch (error) {
    process.stderr.write(redact(error?.stack || error));
    process.exitCode = 1;
  }
});
