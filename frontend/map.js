/**
 * map.js — Leaflet map setup and layer management
 */

// Import Leaflet
import L from 'leaflet'

// Fix default marker icon paths (Leaflet + bundler issue)
delete L.Icon.Default.prototype._getIconUrl
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
})

/** @type {L.Map} */
let map = null

/** @type {Map<string, L.Layer>} */
const artifactLayers = new Map()

/** @type {Map<string, {layer: L.Layer, name: string}>} */
const layerRegistry = new Map()

/**
 * Initialize the Leaflet map.
 * @param {string} containerId - DOM element id
 */
export function initMap(containerId) {
  map = L.map(containerId, {
    center: [39.5, -121.5], // Butte County area
    zoom: 8,
    zoomControl: true,
  })

  // Base layer: OpenStreetMap tiles (dark theme compatible)
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(map)

  // Force a resize once tiles start loading
  setTimeout(() => map.invalidateSize(), 100)

  return map
}

/**
 * Get the current map instance.
 * @returns {L.Map|null}
 */
export function getMap() {
  return map
}

/**
 * Add a GeoJSON layer to the map and fit bounds.
 * @param {object} geojson - GeoJSON FeatureCollection or Feature
 * @param {string} artifactId - Unique artifact identifier
 * @returns {L.Layer}
 */
export function addVectorLayer(geojson, artifactId) {
  removeLayer(artifactId)

  const layer = L.geoJSON(geojson, {
    style: {
      color: '#e94560',
      weight: 2,
      fillOpacity: 0.15,
      fillColor: '#e94560',
    },
    pointToLayer: (feature, latlng) => {
      return L.circleMarker(latlng, {
        radius: 5,
        fillColor: '#e94560',
        color: '#ff6b81',
        weight: 2,
        fillOpacity: 0.8,
      })
    },
  }).addTo(map)

  const bounds = layer.getBounds()
  if (bounds.isValid()) {
    map.fitBounds(bounds, { padding: [60, 60], maxZoom: 16 })
  }

  artifactLayers.set(artifactId, layer)

  // Register in layer control
  layerRegistry.set(artifactId, { layer, name: `Vector: ${artifactId.slice(0, 8)}` })
  refreshLayerList()

  return layer
}

/**
 * Add a raster tile layer to the map.
 * @param {string} artifactId - Unique artifact identifier
 * @param {number[]} bbox - [west, south, east, north]
 * @returns {L.TileLayer}
 */
export function addRasterLayer(artifactId, bbox) {
  removeLayer(artifactId)

  const tileOptions = {
    opacity: 0.85,
    errorTileUrl: '',
    attribution: `Artifact: ${artifactId.slice(0, 8)}`,
  }

  // Set bounds constraint only if bbox is provided
  if (bbox && bbox.length === 4) {
    tileOptions.bounds = [[bbox[1], bbox[0]], [bbox[3], bbox[2]]]
  }

  const tileLayer = L.tileLayer(
    `/api/artifact/${artifactId}/tiles/{z}/{x}/{y}.png`,
    tileOptions
  ).addTo(map)

  // Zoom to bbox if available: [west, south, east, north]
  if (bbox && bbox.length === 4) {
    const southWest = L.latLng(bbox[1], bbox[0])
    const northEast = L.latLng(bbox[3], bbox[2])
    const bounds = L.latLngBounds(southWest, northEast)
    if (bounds.isValid()) {
      map.fitBounds(bounds, { padding: [60, 60], maxZoom: 16 })
    }
  }

  artifactLayers.set(artifactId, tileLayer)

  // Register in layer control
  layerRegistry.set(artifactId, { layer: tileLayer, name: `Raster: ${artifactId.slice(0, 8)}` })
  refreshLayerList()

  return tileLayer
}

/**
 * Remove a layer by artifact ID.
 * @param {string} artifactId
 */
export function removeLayer(artifactId) {
  const layer = artifactLayers.get(artifactId)
  if (layer) {
    map.removeLayer(layer)
    artifactLayers.delete(artifactId)
  }
  layerRegistry.delete(artifactId)
  refreshLayerList()
}

/**
 * Zoom the map to a bounding box.
 * @param {number[]} bbox - [west, south, east, north]
 */
export function zoomToBounds(bbox) {
  const southWest = L.latLng(bbox[1], bbox[0])
  const northEast = L.latLng(bbox[3], bbox[2])
  const bounds = L.latLngBounds(southWest, northEast)
  if (bounds.isValid()) {
    map.fitBounds(bounds, { padding: [60, 60], maxZoom: 16 })
  }
}

/**
 * Remove all artifact layers from the map.
 */
export function clearAllLayers() {
  for (const [id, layer] of artifactLayers) {
    map.removeLayer(layer)
  }
  artifactLayers.clear()
  layerRegistry.clear()
  refreshLayerList()
}

/**
 * Get map of all active layers.
 * @returns {Map<string, L.Layer>}
 */
export function getArtifactLayers() {
  return artifactLayers
}

/**
 * Regenerate the layer list UI in the layer-control panel.
 */
function refreshLayerList() {
  const container = document.getElementById('layer-list')
  if (!container) return

  if (layerRegistry.size === 0) {
    container.innerHTML = '<p class="empty-hint">No layers added yet.</p>'
    return
  }

  const entries = []
  for (const [id, { layer, name }] of layerRegistry) {
    const visible = map.hasLayer(layer)
    entries.push(`
      <div class="layer-entry">
        <label class="layer-label">
          <input type="checkbox" class="layer-checkbox" data-artifact-id="${id}" ${visible ? 'checked' : ''} />
          <span>${escapeHtml(name)}</span>
        </label>
        <button class="layer-remove" data-artifact-id="${id}" title="Remove layer">×</button>
      </div>
    `)
  }
  container.innerHTML = entries.join('')

  // Toggle visibility
  container.querySelectorAll('.layer-checkbox').forEach(cb => {
    cb.addEventListener('change', () => {
      const id = cb.dataset.artifactId
      const entry = layerRegistry.get(id)
      if (!entry) return
      if (cb.checked) {
        map.addLayer(entry.layer)
      } else {
        map.removeLayer(entry.layer)
      }
    })
  })

  // Remove layer
  container.querySelectorAll('.layer-remove').forEach(btn => {
    btn.addEventListener('click', () => {
      removeLayer(btn.dataset.artifactId)
    })
  })
}

/**
 * Escape HTML to prevent XSS in layer names.
 * @param {string} str
 * @returns {string}
 */
function escapeHtml(str) {
  const div = document.createElement('div')
  div.textContent = str
  return div.innerHTML
}