/**
 * main.js — Application entry point
 */

import { initMap, getMap, addVectorLayer, addRasterLayer, removeLayer, zoomToBounds, clearAllLayers } from './map.js'

// ── State ──────────────────────────────────────────────
let sessionId = null
let isStreaming = false

// ── DOM references ─────────────────────────────────────
const chatInput = document.getElementById('chat-input')
const chatSend = document.getElementById('chat-send')
const messageLog = document.getElementById('message-log')
const artifactList = document.getElementById('artifact-list')
const artifactToggle = document.getElementById('artifact-toggle')
const artifactPanel = document.getElementById('artifact-panel')
const layerToggle = document.getElementById('layer-toggle')
const layerList = document.getElementById('layer-list')

// ── Initialization ─────────────────────────────────────
async function init() {
  initMap('map')

  await createSession()
  bindEvents()
}

/**
 * Create a new session on page load.
 */
async function createSession() {
  try {
    const resp = await fetch('/api/session', { method: 'POST' })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()
    sessionId = data.session_id
    addMessage('system', `Session ready: ${sessionId.slice(0, 8)}…`)
    console.log('Session created:', sessionId)
  } catch (err) {
    addMessage('error', `Failed to create session: ${err.message}`)
    console.error('Session creation failed:', err)
  }
}

// ── Event binding ──────────────────────────────────────
function bindEvents() {
  // Send chat on button click
  chatSend.addEventListener('click', sendChat)

  // Send chat on Enter (no shift)
  chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendChat()
    }
  })

  // Artifact panel toggle
  artifactToggle.addEventListener('click', () => {
    artifactPanel.classList.toggle('collapsed')
    artifactToggle.textContent = artifactPanel.classList.contains('collapsed') ? '▸' : '▾'
  })

  // Layer panel toggle
  layerToggle.addEventListener('click', () => {
    layerList.classList.toggle('collapsed')
    layerToggle.textContent = layerList.classList.contains('collapsed') ? '▸' : '▾'
  })
}

// ── Chat ───────────────────────────────────────────────
async function sendChat() {
  const prompt = chatInput.value.trim()
  if (!prompt || isStreaming) return
  if (!sessionId) {
    addMessage('error', 'No active session. Refreshing…')
    await createSession()
    if (!sessionId) return
  }

  // Disable input while streaming
  isStreaming = true
  chatInput.value = ''
  chatInput.disabled = true
  chatSend.disabled = true

  // Show user message
  addMessage('user', prompt)

  // Show streaming placeholder
  const streamMsg = addMessage('system', 'Thinking…')
  streamMsg.classList.add('stream-active')

  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, prompt }),
    })

    if (!response.ok) {
      const text = await response.text()
      streamMsg.textContent = `Error: HTTP ${response.status} — ${text}`
      streamMsg.classList.remove('stream-active')
      streamMsg.classList.add('msg-error')
      return
    }

    // Parse SSE stream via ReadableStream
    await parseSSE(response, streamMsg)
  } catch (err) {
    streamMsg.textContent = `Error: ${err.message}`
    streamMsg.classList.remove('stream-active')
    streamMsg.classList.add('msg-error')
    console.error('Chat failed:', err)
  } finally {
    isStreaming = false
    chatInput.disabled = false
    chatSend.disabled = false
    chatInput.focus()
  }
}

/**
 * Parse a Server-Sent Events stream from a fetch response.
 */
async function parseSSE(response, streamMsg) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const events = buffer.split('\n\n')
    buffer = events.pop() // Keep incomplete chunk for next iteration

    for (const eventBlock of events) {
      if (!eventBlock.trim()) continue

      const lines = eventBlock.split('\n')
      let eventType = ''
      let data = ''

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          data = line.slice(6)
        }
      }

      handleSSEEvent(eventType, data, streamMsg)
    }
  }

  // Process any remaining buffer
  if (buffer.trim()) {
    const lines = buffer.split('\n')
    let eventType = '', data = ''
    for (const line of lines) {
      if (line.startsWith('event: ')) eventType = line.slice(7).trim()
      if (line.startsWith('data: ')) data = line.slice(6)
    }
    handleSSEEvent(eventType, data, streamMsg)
  }
}

/**
 * Handle a single parsed SSE event.
 */
function handleSSEEvent(eventType, data, streamMsg) {
  let parsed = null
  try {
    parsed = data ? JSON.parse(data) : {}
  } catch {
    // Not JSON — use raw string
  }

  switch (eventType) {
    case 'done':
      streamMsg.textContent = 'Complete ✓'
      streamMsg.classList.remove('stream-active')
      // Refresh artifact list
      fetchArtifacts()
      break

    case 'error':
      streamMsg.textContent = `Error: ${parsed?.message || 'Unknown error'}`
      streamMsg.classList.remove('stream-active')
      streamMsg.classList.add('msg-error')
      break

    case 'turn_start':
      streamMsg.textContent = `Processing: ${parsed?.phase || 'started'}…`
      break

    case 'tool_call':
      addMessage('system', `🔧 Tool: ${parsed?.tool || 'unknown'}`, true)
      break

    case 'artifact':
      addMessage('system', `📦 New artifact: ${parsed?.artifact_id?.slice(0, 8) || 'unknown'}`, true)
      break

    case 'turn_end':
      // Don't replace streaming message — wait for done
      break

    default:
      // Unknown event — show inline if we have data
      if (eventType && parsed) {
        streamMsg.textContent = `${eventType}: ${data?.slice(0, 100) || ''}`
      } else if (eventType) {
        streamMsg.textContent = `Event: ${eventType}`
      }
      break
  }
}

// ── Artifacts ──────────────────────────────────────────
async function fetchArtifacts() {
  if (!sessionId) return

  try {
    const resp = await fetch(`/api/session/${sessionId}/artifacts`)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()
    renderArtifactList(data.artifacts || [])
  } catch (err) {
    console.error('Failed to fetch artifacts:', err)
  }
}

function renderArtifactList(artifacts) {
  if (!artifactList) return

  if (artifacts.length === 0) {
    artifactList.innerHTML = '<p class="empty-hint">No artifacts yet. Chat to generate some.</p>'
    return
  }

  artifactList.innerHTML = artifacts.map(a => {
    const shortId = (a.id || '?').slice(0, 8)
    const type = a.data_type || 'unknown'
    const format = a.format || '?'
    const icon = type === 'vector' ? '◧' : '▦'
    return `
      <div class="artifact-entry" data-id="${escapeAttr(a.id)}" data-type="${type}" data-bbox="${escapeAttr(JSON.stringify(a.bbox || []))}">
        <span class="artifact-icon">${icon}</span>
        <div class="artifact-info">
          <span class="artifact-id" title="${escapeAttr(a.id || '')}">${escapeHtml(shortId)}</span>
          <span class="artifact-meta">${escapeHtml(type)} · ${escapeHtml(format)}</span>
        </div>
      </div>
    `
  }).join('')

  // Bind click handlers
  artifactList.querySelectorAll('.artifact-entry').forEach(entry => {
    entry.addEventListener('click', () => {
      handleArtifactClick(entry.dataset.id, entry.dataset.type, entry.dataset.bbox)
    })
  })

  // Auto-expand panel if it was collapsed
  if (artifactPanel.classList.contains('collapsed')) {
    artifactPanel.classList.remove('collapsed')
    artifactToggle.textContent = '▾'
  }
}

async function handleArtifactClick(artifactId, dataType, bboxStr) {
  if (!artifactId) return

  let bbox = null
  try {
    bbox = JSON.parse(bboxStr)
  } catch { /* ignore */ }

  if (dataType === 'vector') {
    try {
      const resp = await fetch(`/api/artifact/${artifactId}/preview`)
      if (!resp.ok) {
        addMessage('error', `Failed to load vector preview: HTTP ${resp.status}`)
        return
      }
      const geojson = await resp.json()
      addVectorLayer(geojson, artifactId)
      addMessage('system', `Loaded vector layer: ${artifactId.slice(0, 8)}`, true)
    } catch (err) {
      addMessage('error', `Failed to load vector: ${err.message}`)
    }
  } else if (dataType === 'raster') {
    if (bbox && bbox.length === 4) {
      addRasterLayer(artifactId, bbox)
      addMessage('system', `Added raster layer: ${artifactId.slice(0, 8)}`, true)
    } else {
      // Try zooming to bbox from artifact data
      addRasterLayer(artifactId, bbox || [-122, 39, -121, 40])
      addMessage('system', `Added raster layer (default bounds): ${artifactId.slice(0, 8)}`, true)
    }
  }
}

// ── Message log ────────────────────────────────────────
/**
 * Add a message to the chat log.
 * @param {'user'|'system'|'error'} type
 * @param {string} text
 * @param {boolean} [transient=false] - If true, don't persist styling as a permanent entry
 * @returns {HTMLElement} The created message element
 */
function addMessage(type, text, transient) {
  if (!messageLog) return document.createElement('div')

  const el = document.createElement('div')
  el.className = `msg msg-${type}`
  if (transient) el.classList.add('msg-transient')
  el.textContent = text
  messageLog.appendChild(el)

  // Auto-scroll to bottom
  messageLog.scrollTop = messageLog.scrollHeight

  return el
}

// ── Helpers ────────────────────────────────────────────
function escapeHtml(str) {
  const div = document.createElement('div')
  div.textContent = str
  return div.innerHTML
}

function escapeAttr(str) {
  return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

// ── Boot ───────────────────────────────────────────────
init()