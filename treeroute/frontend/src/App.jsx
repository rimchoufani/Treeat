import { useEffect, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import MapboxDraw from '@mapbox/mapbox-gl-draw'
import './App.css'

// Backend base URL. Empty in dev (Vite proxies /api → localhost:8000);
// in prod set VITE_API_URL on Vercel to your Railway backend URL.
const API_BASE = import.meta.env.VITE_API_URL || ''

const MAP_STYLE = {
  version: 8,
  glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
  sources: {
    'carto-light': {
      type: 'raster',
      tiles: [
        'https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
        'https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
        'https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
      ],
      tileSize: 256,
      attribution: '© OpenStreetMap © CARTO',
    },
  },
  layers: [{ id: 'carto-light', type: 'raster', source: 'carto-light', paint: { 'raster-opacity': 1 } }],
}

function polygonAreaKm2(coords) {
  const lats = coords.map(c => c[1])
  const lons = coords.map(c => c[0])
  const dlat = (Math.max(...lats) - Math.min(...lats)) * 111
  const dlon = (Math.max(...lons) - Math.min(...lons)) * 111 * 0.7
  return (dlat * dlon).toFixed(2)
}

function midpoint(coords) {
  const lngs = coords.map(c => c[0])
  const lats = coords.map(c => c[1])
  return [(Math.min(...lngs) + Math.max(...lngs)) / 2, (Math.min(...lats) + Math.max(...lats)) / 2]
}

function AllergyBadge({ risk }) {
  const color = risk === 'Low' ? '#2ca02c' : risk === 'High' || risk === 'HIGH' ? '#d62728' : '#ff7f0e'
  const label = String(risk).split(' ')[0]
  return (
    <span style={{ background: color, color: '#fff', borderRadius: 12, padding: '1px 8px', fontSize: 11, fontWeight: 600 }}>
      {label}
    </span>
  )
}

function CoolingDots({ score }) {
  const filled = Math.round(score / 2)
  return (
    <span className="cooling-dots">
      {Array.from({ length: 5 }, (_, i) => (
        <span key={i} className={`cdot ${i < filled ? 'filled' : ''}`} />
      ))}
    </span>
  )
}

function MapSearch({ onNavigate }) {
  const [query,   setQuery]   = useState('')
  const [results, setResults] = useState([])
  const debounce = useRef(null)

  const search = (val) => {
    setQuery(val)
    if (val.length < 3) { setResults([]); return }
    clearTimeout(debounce.current)
    debounce.current = setTimeout(async () => {
      try {
        const r = await fetch(`${API_BASE}/api/geocode?q=${encodeURIComponent(val)}`)
        setResults(await r.json())
      } catch (e) {}
    }, 350)
  }

  return (
    <div className="map-search-wrap">
      <span className="map-search-icon">🔍</span>
      <input
        className="map-search-input"
        placeholder="Search Vienna..."
        value={query}
        onChange={e => search(e.target.value)}
      />
      {results.length > 0 && (
        <div className="map-search-results">
          {results.map((r, i) => (
            <div
              key={i}
              className="map-search-result"
              onClick={() => {
                onNavigate(r.lat, r.lon)
                setQuery(r.display_name)
                setResults([])
              }}
            >
              {r.display_name}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function RouteAddressInput({ placeholder, dotColor, onSelect, onPickOnMap, isPickingFor }) {
  const [query,   setQuery]   = useState('')
  const [results, setResults] = useState([])
  const debounce = useRef(null)

  const search = (val) => {
    setQuery(val)
    if (val.length < 3) { setResults([]); return }
    clearTimeout(debounce.current)
    debounce.current = setTimeout(async () => {
      try {
        const r = await fetch(`${API_BASE}/api/geocode?q=${encodeURIComponent(val)}`)
        setResults(await r.json())
      } catch (e) {}
    }, 350)
  }

  return (
    <div className="route-input-wrap">
      <div className="route-input-dot" style={{ background: dotColor }} />
      <input
        className="route-input"
        style={{ borderColor: isPickingFor ? dotColor : undefined }}
        placeholder={isPickingFor ? '📍 Click on map...' : placeholder}
        value={query}
        onChange={e => search(e.target.value)}
        readOnly={isPickingFor}
      />
      {results.length > 0 && (
        <div className="route-dropdown">
          {results.map((r, i) => (
            <div
              key={i}
              className="route-dropdown-item"
              onClick={() => {
                setQuery(r.display_name)
                setResults([])
                onSelect(r)
              }}
            >
              {r.display_name}
            </div>
          ))}
          <div
            className="route-dropdown-item"
            style={{ color: dotColor, fontWeight: 500 }}
            onClick={() => { setResults([]); onPickOnMap() }}
          >
            📍 Pick on map instead
          </div>
        </div>
      )}
      {!isPickingFor && query.length === 0 && (
        <div
          onClick={onPickOnMap}
          style={{
            position: 'absolute', right: 8, top: '50%',
            transform: 'translateY(-50%)', fontSize: 10, color: '#bbb', cursor: 'pointer',
          }}
        >
          📍
        </div>
      )}
    </div>
  )
}

export default function App() {
  const mapContainer  = useRef(null)
  const mapRef        = useRef(null)
  const drawRef       = useRef(null)
  const plantingPopup = useRef(null)

  // Analysis
  const [view,             setView]             = useState('Top')
  const [layers,           setLayers]           = useState({ wind: true, planting: true, route: true })
  const [isDrawing,        setIsDrawing]        = useState(false)
  const [drawnPolygon,     setDrawnPolygon]     = useState(null)
  const [jobStatus,        setJobStatus]        = useState(null)
  const [progress,         setProgress]         = useState(0)
  const [progressStep,     setProgressStep]     = useState('')
  const [toast,            setToast]            = useState(null)
  const [stats,            setStats]            = useState(null)
  const [analysisComplete, setAnalysisComplete] = useState(false)
  const [currentJobId,     setCurrentJobId]     = useState(null)
  const [openSection,      setOpenSection]      = useState(null)

  // Budget optimizer
  const [budgetEur,  setBudgetEur]  = useState(50000)
  const [budgetMeta, setBudgetMeta] = useState(null)

  // Before / after toggle
  const [showAfter,      setShowAfter]      = useState(false)
  const [utciAfterMean,  setUtciAfterMean]  = useState(null)

  // Street panel
  const [selectedStreet,  setSelectedStreet]  = useState(null)
  const [showStreetPanel, setShowStreetPanel] = useState(false)
  const [streetSpecies,   setStreetSpecies]   = useState([])
  const [suppliers,       setSuppliers]       = useState([])
  const [selectedSpecies, setSelectedSpecies] = useState(null)
  const [treeCount,       setTreeCount]       = useState(1)

  // Cool route
  const [routeMode,     setRouteMode]     = useState(null)
  const [routeOrigin,   setRouteOrigin]   = useState(null)
  const [routeDest,     setRouteDest]     = useState(null)
  const [routeResult,   setRouteResult]   = useState(null)
  const [routeProgress, setRouteProgress] = useState(0)
  const [routeStep,     setRouteStep]     = useState('')
  const [pickingFor,    setPickingFor]    = useState(null)
  const originMarkerRef = useRef(null)
  const destMarkerRef   = useRef(null)
  const pickingForRef   = useRef(null)
  const routeOriginRef  = useRef(null)
  const routeDestRef    = useRef(null)

  const showToast = (msg) => { setToast(msg); setTimeout(() => setToast(null), 3200) }

  useEffect(() => { pickingForRef.current = pickingFor }, [pickingFor])
  useEffect(() => { routeOriginRef.current = routeOrigin }, [routeOrigin])
  useEffect(() => { routeDestRef.current = routeDest }, [routeDest])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    map.getCanvas().style.cursor = pickingFor ? 'crosshair' : ''
  }, [pickingFor])

  // ── Init map ───────────────────────────────────────────────────────────────
  useEffect(() => {
    if (mapRef.current) return

    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: MAP_STYLE,
      center: [16.385, 48.219],
      zoom: 14,
      pitch: 0,
      bearing: 0,
      antialias: true,
    })
    mapRef.current = map

    map.addControl(new maplibregl.NavigationControl(), 'bottom-right')
    map.addControl(new maplibregl.ScaleControl(), 'bottom-left')

    try {
      const draw = new MapboxDraw({ displayControlsDefault: false, controls: { polygon: true, trash: true } })
      map.addControl(draw, 'top-right')
      drawRef.current = draw
      map.on('draw.modechange', e => setIsDrawing(e.mode === 'draw_polygon'))
      map.on('draw.create', e => { setDrawnPolygon(e.features[0].geometry); setIsDrawing(false) })
    } catch (err) { console.warn('MapboxDraw init failed:', err) }

    map.on('load', () => {
      try {
        map.addSource('openmaptiles', {
          type: 'vector',
          url: 'https://api.maptiler.com/tiles/v3/tiles.json?key=get_your_own_OpIi9ZULNHzrESv6T2vL',
        })
        map.addLayer({
          id: 'buildings-3d', source: 'openmaptiles', 'source-layer': 'building',
          type: 'fill-extrusion', minzoom: 12,
          paint: {
            'fill-extrusion-color': '#c8c8c4',
            'fill-extrusion-height': ['interpolate', ['linear'], ['zoom'], 12, 0, 13, ['get', 'render_height']],
            'fill-extrusion-base':   ['interpolate', ['linear'], ['zoom'], 12, 0, 13, ['get', 'render_min_height']],
            'fill-extrusion-opacity': 0.7,
          },
        })
        // Start in Top view — hide 3D buildings initially
        map.setLayoutProperty('buildings-3d', 'visibility', 'none')
      } catch (err) { console.warn('Buildings layer:', err) }

      // Planting streets
      map.addSource('planting-source', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'planting-layer-bg', type: 'line', source: 'planting-source', paint: { 'line-color': '#2ca02c', 'line-width': 10, 'line-opacity': 0.25 } })
      map.addLayer({ id: 'planting-layer',    type: 'line', source: 'planting-source', paint: { 'line-color': '#2ca02c', 'line-width': 5,  'line-opacity': 1.0  } })

      // Cool route — single thick green line
      map.addSource('route-coolest-source', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({
        id: 'route-coolest-layer', type: 'line', source: 'route-coolest-source',
        paint: { 'line-color': '#1a7a4a', 'line-width': 6, 'line-opacity': 1.0 },
      })

      // Planting hover popup
      plantingPopup.current = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: 'planting-popup', offset: 6 })

      map.on('mouseenter', 'planting-layer', (e) => {
        if (pickingForRef.current) return
        map.getCanvas().style.cursor = 'pointer'
        map.setPaintProperty('planting-layer', 'line-width', 7)
        const p = e.features[0].properties
        plantingPopup.current
          .setLngLat(e.lngLat)
          .setHTML(`<div class="pp-inner">
            <strong>Priority planting street</strong><br/>
            UTCI: ${parseFloat(p.avg_utci ?? 32).toFixed(1)}°C &nbsp;·&nbsp; ${p.recommended_trees ?? 1} trees recommended<br/>
            <span class="pp-hint">Click to explore</span>
          </div>`)
          .addTo(map)
      })
      map.on('mouseleave', 'planting-layer', () => {
        map.getCanvas().style.cursor = pickingForRef.current ? 'crosshair' : ''
        map.setPaintProperty('planting-layer', 'line-width', 5)
        plantingPopup.current.remove()
      })

      map.on('click', 'planting-layer', (e) => {
        if (pickingForRef.current) return
        plantingPopup.current.remove()
        const feature = e.features[0]
        if (!feature) return
        const props = feature.properties
        const coords = feature.geometry.coordinates
        const mid = midpoint(coords)
        map.flyTo({ center: mid, zoom: 17, pitch: 0, duration: 800 })
        window.__plantingClick = { props, mid }
        window.__onPlantingClick && window.__onPlantingClick()
      })

      // Global click: cool route picking + close panel
      map.on('click', (e) => {
        const picking = pickingForRef.current

        if (picking) {
          const { lng, lat } = e.lngLat
          const coords = [lng, lat]
          const color = picking === 'origin' ? '#1a7a4a' : '#e05c5c'
          const label = picking === 'origin' ? 'Start' : 'End'

          if (picking === 'origin') {
            originMarkerRef.current?.remove()
            originMarkerRef.current = new maplibregl.Marker({ color })
              .setLngLat([lng, lat])
              .setPopup(new maplibregl.Popup({ offset: 25 }).setText(label))
              .addTo(map)
            routeOriginRef.current = coords
            setRouteOrigin(coords)
          } else {
            destMarkerRef.current?.remove()
            destMarkerRef.current = new maplibregl.Marker({ color })
              .setLngLat([lng, lat])
              .setPopup(new maplibregl.Popup({ offset: 25 }).setText(label))
              .addTo(map)
            routeDestRef.current = coords
            setRouteDest(coords)
          }

          pickingForRef.current = null
          setPickingFor(null)

          const origin = picking === 'origin' ? coords : routeOriginRef.current
          const dest   = picking === 'dest'   ? coords : routeDestRef.current
          if (origin && dest) {
            window.__runRouteCalculation && window.__runRouteCalculation(origin, dest)
          }
          return
        }

        const hits = map.queryRenderedFeatures(e.point, { layers: ['planting-layer', 'planting-layer-bg'] })
        if (hits.length === 0) {
          window.__closeStreetPanel && window.__closeStreetPanel()
        }
      })

      map.fitBounds([[16.375, 48.210], [16.396, 48.228]], { padding: 40, duration: 1200 })
    })

    return () => { map.remove(); mapRef.current = null }
  }, [])

  // Wire planting click handler
  useEffect(() => {
    window.__onPlantingClick = () => {
      const d = window.__plantingClick
      if (!d) return
      setSelectedStreet(d.props)
      setTreeCount(d.props.recommended_trees || 1)
      setSelectedSpecies(null)
      setStreetSpecies([])
      setSuppliers([])
      setShowStreetPanel(true)
      fetch(`${API_BASE}/api/street-species?utci=${d.props.avg_utci ?? 32.0}`)
        .then(r => r.json()).then(setStreetSpecies).catch(() => {})
    }
    window.__closeStreetPanel = () => { setShowStreetPanel(false); setSelectedStreet(null) }
    return () => { window.__onPlantingClick = null; window.__closeStreetPanel = null }
  }, [])

  // Wire runRouteCalculation for map click handler
  useEffect(() => {
    window.__runRouteCalculation = (origin, dest) => runRouteCalculation(origin, dest)
    return () => { window.__runRouteCalculation = null }
  })

  // Load suppliers when species selected
  useEffect(() => {
    if (!selectedSpecies) return
    fetch(`${API_BASE}/api/suppliers`).then(r => r.json()).then(data => {
      const n = selectedSpecies.name.split(' ')[0].toLowerCase()
      setSuppliers(data.filter(s => s.species.some(sp => sp.toLowerCase().includes(n) || n.includes(sp.toLowerCase()))))
    }).catch(() => {})
  }, [selectedSpecies])

  // ── Cool route ─────────────────────────────────────────────────────────────
  async function runRouteCalculation(origin, dest) {
    setRouteMode('calculating')
    setRouteProgress(0)
    setRouteStep('Starting...')
    mapRef.current?.getSource('route-fastest-source')?.setData({ type: 'FeatureCollection', features: [] })
    mapRef.current?.getSource('route-coolest-source')?.setData({ type: 'FeatureCollection', features: [] })
    try {
      const res = await fetch(`${API_BASE}/api/cool-route`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ origin, destination: dest, analysis_job_id: currentJobId }),
      })
      const { job_id } = await res.json()
      pollRouteJob(job_id)
    } catch (e) {
      setRouteMode(null)
      showToast('Route failed — try again')
    }
  }

  function pollRouteJob(id) {
    const interval = setInterval(async () => {
      const data = await fetch(`${API_BASE}/api/cool-route-job/${id}`).then(r => r.json())
      setRouteProgress(data.progress || 0)
      setRouteStep(data.step || '')

      if (data.status === 'complete') {
        clearInterval(interval)
        const { coolest } = data.result
        const map = mapRef.current
        map.getSource('route-coolest-source')?.setData({ type: 'FeatureCollection', features: [coolest] })
        const coords = coolest.geometry.coordinates
        const lngs = coords.map(c => c[0])
        const lats = coords.map(c => c[1])
        map.fitBounds([[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]], { padding: 100, duration: 1000 })
        setRouteResult({ distance_m: data.result.distance_m, avg_utci: data.result.avg_utci })
        setRouteMode('showing')
      } else if (data.status === 'error') {
        clearInterval(interval)
        setRouteMode(null)
        showToast(`Route error: ${data.step}`)
      }
    }, 2000)
  }

  function clearRoute() {
    originMarkerRef.current?.remove(); originMarkerRef.current = null
    destMarkerRef.current?.remove();   destMarkerRef.current   = null
    routeOriginRef.current = null;     routeDestRef.current    = null
    setRouteMode(null); setRouteResult(null); setRouteOrigin(null); setRouteDest(null)
    mapRef.current?.getSource('route-coolest-source')?.setData({ type: 'FeatureCollection', features: [] })
  }

  // ── View toggle ────────────────────────────────────────────────────────────
  function setViewMode(mode) {
    setView(mode)
    const map = mapRef.current
    if (!map) return
    if (mode === 'Top') {
      map.easeTo({ pitch: 0, bearing: 0, duration: 700 })
      if (map.getLayer('buildings-3d')) map.setLayoutProperty('buildings-3d', 'visibility', 'none')
    } else {
      map.easeTo({ pitch: 50, bearing: -15, duration: 700 })
      if (map.getLayer('buildings-3d')) map.setLayoutProperty('buildings-3d', 'visibility', 'visible')
    }
  }

  // ── Layer toggle ───────────────────────────────────────────────────────────
  function toggleLayer(key) {
    const map = mapRef.current; if (!map) return
    const ids = {
      wind:     ['wind-layer', 'wind-after-layer'],
      planting: ['planting-layer', 'planting-layer-bg'],
      route:    ['route-coolest-layer'],
    }[key] ?? []
    const existingId = ids.find(id => map.getLayer(id))
    const vis  = existingId ? (map.getLayoutProperty(existingId, 'visibility') ?? 'visible') : 'visible'
    const next = vis === 'none' ? 'visible' : 'none'
    ids.forEach(id => { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', next) })
    setLayers(prev => ({ ...prev, [key]: next === 'visible' }))
  }

  // ── Draw ───────────────────────────────────────────────────────────────────
  function activateDraw() {
    drawRef.current?.deleteAll()
    drawRef.current?.changeMode('draw_polygon')
    setDrawnPolygon(null); setIsDrawing(true)
  }
  function cancelDraw() {
    drawRef.current?.deleteAll()
    try { drawRef.current?.changeMode('simple_select') } catch (_) {}
    setDrawnPolygon(null); setIsDrawing(false)
  }

  // ── Run analysis ───────────────────────────────────────────────────────────
  async function runAnalysis() {
    if (!drawnPolygon) return
    const polygon = drawnPolygon
    setDrawnPolygon(null); drawRef.current?.deleteAll()
    setJobStatus('running'); setProgress(0); setProgressStep('Starting...')
    try {
      const { job_id } = await fetch(`${API_BASE}/api/analyze`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ polygon }),
      }).then(r => r.json())

      const interval = setInterval(async () => {
        const job = await fetch(`${API_BASE}/api/job/${job_id}`).then(r => r.json())
        setProgress(job.progress ?? 0)
        setProgressStep(job.step ?? '')
        if (job.status === 'complete') {
          clearInterval(interval)
          setCurrentJobId(job_id)
          const results = await fetch(`${API_BASE}/api/job/${job_id}/results`).then(r => r.json())
          applyResults(results)
          setJobStatus(null)
          setAnalysisComplete(true)
          setOpenSection('trees')
          showToast('Analysis complete!')
          applyBudget(budgetEur, job_id)
        } else if (job.status === 'error') {
          clearInterval(interval); setJobStatus(null)
          showToast(`Error: ${job.step}`)
        }
      }, 2000)
    } catch (_) { setJobStatus(null); showToast('Analysis failed — check backend') }
  }

  function applyResults(results) {
    const map = mapRef.current
    if (!map) return
    const { bounds, utci_after_bounds, utci_image, utci_after_image, planting_locations, cool_route, stats: s } = results
    if (!bounds) return
    const { west: w, south: s2, east: e, north: n } = bounds
    const coords = [[w, n], [e, n], [e, s2], [w, s2]]
    const beforeInsert = map.getLayer('planting-layer-bg') ? 'planting-layer-bg' : undefined

    // Before layer (visible by default)
    try {
      const imgUrl = `data:image/png;base64,${utci_image}`
      if (map.getSource('wind-source')) { map.removeLayer('wind-layer'); map.removeSource('wind-source') }
      map.addSource('wind-source', { type: 'image', url: imgUrl, coordinates: coords })
      map.addLayer({ id: 'wind-layer', type: 'raster', source: 'wind-source', paint: { 'raster-opacity': 0.65 } }, beforeInsert)
    } catch (err) { console.warn('wind layer error:', err) }

    // After layer — uses its own bounds (SDK may return different tile coverage)
    if (utci_after_image) {
      try {
        const ab = utci_after_bounds || bounds
        const afterCoords = [[ab.west, ab.north], [ab.east, ab.north], [ab.east, ab.south], [ab.west, ab.south]]
        const imgAfterUrl = `data:image/png;base64,${utci_after_image}`
        if (map.getSource('wind-after-source')) { map.removeLayer('wind-after-layer'); map.removeSource('wind-after-source') }
        map.addSource('wind-after-source', { type: 'image', url: imgAfterUrl, coordinates: afterCoords })
        map.addLayer({ id: 'wind-after-layer', type: 'raster', source: 'wind-after-source', paint: { 'raster-opacity': 0.65 }, layout: { visibility: 'none' } }, beforeInsert)
      } catch (err) { console.warn('wind-after layer error:', err) }
    }

    setShowAfter(false)
    if (s?.utci_after_mean != null) setUtciAfterMean(s.utci_after_mean)

    const plantSrc = map.getSource('planting-source')
    if (plantSrc && planting_locations) plantSrc.setData(planting_locations)

    const routeSrc = map.getSource('route-source')
    if (routeSrc && cool_route) routeSrc.setData(cool_route)

    ;['planting-layer-bg', 'planting-layer', 'route-layer'].forEach(id => {
      if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'visible')
    })

    map.fitBounds([[w, s2], [e, n]], { padding: 60, duration: 1000 })
    if (s) setStats(s)
  }

  // ── Budget optimizer ───────────────────────────────────────────────────────
  const budgetDebounce = useRef(null)

  async function applyBudget(budget, jobId) {
    const id = jobId ?? currentJobId
    if (!id) return
    try {
      const data = await fetch(`${API_BASE}/api/budget?job_id=${id}&budget_eur=${budget}`).then(r => r.json())
      mapRef.current?.getSource('planting-source')?.setData(data.geojson)
      setBudgetMeta(data.meta)
    } catch (_) {}
  }

  function onBudgetChange(val) {
    setBudgetEur(val)
    clearTimeout(budgetDebounce.current)
    budgetDebounce.current = setTimeout(() => applyBudget(val), 300)
  }

  // ── Before / after toggle ──────────────────────────────────────────────────
  function toggleBeforeAfter(after) {
    setShowAfter(after)
    const map = mapRef.current
    if (!map) return
    if (map.getLayer('wind-layer'))       map.setLayoutProperty('wind-layer',       'visibility', after ? 'none'    : 'visible')
    if (map.getLayer('wind-after-layer')) map.setLayoutProperty('wind-after-layer', 'visibility', after ? 'visible' : 'none')
  }

  // ── Derived ────────────────────────────────────────────────────────────────
  const drawnCoords  = drawnPolygon?.coordinates?.[0] ?? null
  const areaKm2      = drawnCoords ? parseFloat(polygonAreaKm2(drawnCoords)) : 0
  const areaTooLarge = areaKm2 > 2.25

  function toggleSection(key) {
    setOpenSection(prev => prev === key ? null : key)
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="app">

      {/* ── SIDEBAR ── */}
      <aside className="sidebar">

        {/* Header */}
        <div className="sb-header">
          <div className="sb-logo">Treeat</div>
          <div className="sb-tagline">Where trees meet. Where you should plant.</div>
          <MapSearch onNavigate={(lat, lon) => {
            mapRef.current?.flyTo({ center: [lon, lat], zoom: 15, duration: 1000 })
          }} />
        </div>

        {/* Accordion */}
        <div className="accordion">

          {/* ── WIND ANALYSIS ── */}
          <div className="accordion-item">
            <div className="accordion-header" onClick={() => toggleSection('wind')}>
              <div className="accordion-icon" style={{ background: '#fff0f0' }}>🌬️</div>
              <div className="accordion-title">
                <h3>Wind Analysis</h3>
                <p>Draw area to simulate wind comfort</p>
              </div>
              <span className={`accordion-badge ${
                analysisComplete ? 'badge-active'
                : jobStatus === 'running' ? 'badge-running'
                : 'badge-idle'
              }`}>
                {analysisComplete ? 'Done' : jobStatus === 'running' ? 'Running' : 'Ready'}
              </span>
              <span className={`accordion-chevron ${openSection === 'wind' ? 'open' : ''}`}>▼</span>
            </div>
            <div className={`accordion-body ${openSection === 'wind' ? 'open' : ''}`}>
              <div className="accordion-content">
                <p style={{ fontSize: 11, color: '#999', marginBottom: 12, lineHeight: 1.5 }}>
                  Draw a zone on the map to run a real-time wind comfort simulation powered by the Infrared SDK.
                </p>

                <button
                  className="draw-btn"
                  disabled={jobStatus === 'running'}
                  onClick={() => { activateDraw(); setOpenSection(null) }}
                >
                  ✏ Draw analysis area
                </button>

                {jobStatus === 'running' && (
                  <div className="progress-wrap">
                    <div className="progress-label">{progressStep}</div>
                    <div className="progress-track">
                      <div className="progress-fill" style={{ width: `${progress}%` }} />
                    </div>
                    <div className="progress-note">~2 min · Infrared SDK UTCI</div>
                  </div>
                )}

                {analysisComplete && (
                  <>
                    <div style={{ fontSize: 11, color: '#1a7a4a', fontWeight: 500, marginBottom: 8 }}>
                      ✓ Analysis complete
                    </div>
                    <div className="layer-toggle-row">
                      <span className="layer-toggle-label">
                        <span className="layer-dot" style={{ background: '#e05c5c' }} />
                        Wind comfort map
                      </span>
                      <button
                        className={`toggle-switch ${layers.wind ? 'on' : ''}`}
                        onClick={() => toggleLayer('wind')}
                      />
                    </div>
                    <div className="stats-grid">
                      <div className="stat-card">
                        <div className="stat-value">
                          {stats?.utci_mean ? `${parseFloat(stats.utci_mean).toFixed(1)}` : '--'}°C
                        </div>
                        <div className="stat-label">avg UTCI (July peak)</div>
                      </div>
                      <div className="stat-card">
                        <div className="stat-value">{stats?.n_planting_streets ?? '--'}</div>
                        <div className="stat-label">streets analysed</div>
                      </div>
                    </div>

                    {/* Before / After toggle */}
                    <div style={{ marginTop: 12 }}>
                      <div style={{ fontSize: 11, fontWeight: 600, color: '#444', marginBottom: 6 }}>
                        Heatmap view
                      </div>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button
                          onClick={() => toggleBeforeAfter(false)}
                          style={{
                            flex: 1, padding: '6px 0', fontSize: 11, fontWeight: 600,
                            borderRadius: 6, cursor: 'pointer', border: '1.5px solid',
                            borderColor: !showAfter ? '#1a7a4a' : '#ddd',
                            background: !showAfter ? '#1a7a4a' : '#fff',
                            color: !showAfter ? '#fff' : '#888',
                          }}
                        >
                          Before
                        </button>
                        <button
                          onClick={() => toggleBeforeAfter(true)}
                          style={{
                            flex: 1, padding: '6px 0', fontSize: 11, fontWeight: 600,
                            borderRadius: 6, cursor: 'pointer', border: '1.5px solid',
                            borderColor: showAfter ? '#1a7a4a' : '#ddd',
                            background: showAfter ? '#1a7a4a' : '#fff',
                            color: showAfter ? '#fff' : '#888',
                          }}
                        >
                          After trees
                        </button>
                      </div>
                      {showAfter && (
                        <div style={{
                          marginTop: 8, padding: '6px 10px',
                          background: '#f0f9f4', borderRadius: 6,
                          fontSize: 11, color: '#555', lineHeight: 1.4,
                        }}>
                          <span style={{ color: '#1a7a4a', fontWeight: 600 }}>
                            −3°C UTCI under each canopy
                          </span>
                          <span style={{ color: '#999', marginLeft: 4 }}>
                            · shading model · zoom in to see individual trees
                          </span>
                        </div>
                      )}
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* ── TREE PLANTING ── */}
          <div className="accordion-item">
            <div className="accordion-header" onClick={() => toggleSection('trees')}>
              <div className="accordion-icon" style={{ background: '#f0f9f4' }}>🌳</div>
              <div className="accordion-title">
                <h3>Tree Planting</h3>
                <p>Priority streets + species selection</p>
              </div>
              <span className={`accordion-badge ${analysisComplete ? 'badge-active' : 'badge-idle'}`}>
                {analysisComplete ? `${stats?.n_planting_streets ?? 0} streets` : 'Run wind first'}
              </span>
              <span className={`accordion-chevron ${openSection === 'trees' ? 'open' : ''}`}>▼</span>
            </div>
            <div className={`accordion-body ${openSection === 'trees' ? 'open' : ''}`}>
              <div className="accordion-content">
                {!analysisComplete ? (
                  <p style={{ fontSize: 11, color: '#aaa', lineHeight: 1.5 }}>
                    Run a wind analysis first to identify priority planting streets.
                  </p>
                ) : (
                  <>
                    <div className="layer-toggle-row">
                      <span className="layer-toggle-label">
                        <span className="layer-dot" style={{ background: '#2ca02c' }} />
                        Planting streets
                      </span>
                      <button
                        className={`toggle-switch ${layers.planting ? 'on' : ''}`}
                        onClick={() => toggleLayer('planting')}
                      />
                    </div>

                    <div className="stats-grid" style={{ marginBottom: 12 }}>
                      <div className="stat-card">
                        <div className="stat-value">{stats?.n_planting_streets ?? 0}</div>
                        <div className="stat-label">priority streets</div>
                      </div>
                      <div className="stat-card">
                        <div className="stat-value">{stats?.total_trees ?? '--'}</div>
                        <div className="stat-label">trees needed</div>
                      </div>
                    </div>

                    {/* Budget slider */}
                    <div style={{ margin: '12px 0' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                        <span style={{ fontSize: 11, fontWeight: 600, color: '#444' }}>Tree budget</span>
                        <span style={{ fontSize: 13, fontWeight: 700, color: '#1a7a4a' }}>
                          €{budgetEur.toLocaleString()}
                        </span>
                      </div>
                      <input
                        type="range"
                        min={5000}
                        max={100000}
                        step={2500}
                        value={budgetEur}
                        onChange={e => onBudgetChange(parseInt(e.target.value))}
                        style={{ width: '100%', accentColor: '#1a7a4a' }}
                      />
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#bbb', marginTop: 2 }}>
                        <span>€5k</span><span>€100k</span>
                      </div>
                      {budgetMeta && (
                        <div style={{
                          marginTop: 8, padding: '6px 10px',
                          background: '#f0f9f4', borderRadius: 6,
                          fontSize: 11, color: '#444',
                          display: 'flex', justifyContent: 'space-between',
                        }}>
                          <span>🌳 {budgetMeta.total_trees} trees · {budgetMeta.streets_funded} streets</span>
                          <span style={{ color: '#1a7a4a', fontWeight: 600 }}>€{budgetMeta.total_cost?.toLocaleString()} used</span>
                        </div>
                      )}
                    </div>

                    <p style={{ fontSize: 11, color: '#888', marginBottom: 8, lineHeight: 1.5 }}>
                      Click any green street on the map to explore species and costs.
                    </p>

                    <button
                      className="download-btn"
                      onClick={async () => {
                        if (!currentJobId) return
                        showToast('Generating planting plan...')
                        try {
                          const res = await fetch(`${API_BASE}/api/planting-plan-pdf/${currentJobId}`)
                          const blob = await res.blob()
                          const url = URL.createObjectURL(blob)
                          const a = document.createElement('a')
                          a.href = url
                          a.download = 'treeat-planting-plan.pdf'
                          a.click()
                          URL.revokeObjectURL(url)
                          showToast('✓ Planting plan downloaded')
                        } catch (e) {
                          showToast('PDF generation failed')
                        }
                      }}
                    >
                      ⬇ Download planting plan PDF
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* ── COOL ROUTE ── */}
          <div className="accordion-item">
            <div className="accordion-header" onClick={() => toggleSection('route')}>
              <div className="accordion-icon" style={{ background: '#f0f4ff' }}>🧭</div>
              <div className="accordion-title">
                <h3>Cool Route</h3>
                <p>Find the shadiest path between two points</p>
              </div>
              <span className={`accordion-badge ${
                routeMode === 'showing' ? 'badge-active'
                : routeMode === 'calculating' ? 'badge-running'
                : 'badge-idle'
              }`}>
                {routeMode === 'showing' ? 'Ready'
                 : routeMode === 'calculating' ? 'Calculating'
                 : 'Ready'}
              </span>
              <span className={`accordion-chevron ${openSection === 'route' ? 'open' : ''}`}>▼</span>
            </div>
            <div className={`accordion-body ${openSection === 'route' ? 'open' : ''}`}>
              <div className="accordion-content">
                <p style={{ fontSize: 11, color: '#999', marginBottom: 12, lineHeight: 1.5 }}>
                  Type addresses or click the map. Compares fastest vs coolest route using real Infrared wind data.
                </p>

                <div className="route-inputs">
                  <RouteAddressInput
                    placeholder="From — type or click map"
                    dotColor="#1a7a4a"
                    isPickingFor={pickingFor === 'origin'}
                    onPickOnMap={() => { setPickingFor('origin'); setOpenSection(null) }}
                    onSelect={(item) => {
                      const coords = [item.lon, item.lat]
                      setRouteOrigin(coords)
                      routeOriginRef.current = coords
                      originMarkerRef.current?.remove()
                      originMarkerRef.current = new maplibregl.Marker({ color: '#1a7a4a' })
                        .setLngLat([item.lon, item.lat]).addTo(mapRef.current)
                      mapRef.current?.flyTo({ center: [item.lon, item.lat], zoom: 15, duration: 800 })
                      if (routeDestRef.current) runRouteCalculation(coords, routeDestRef.current)
                    }}
                  />
                  <RouteAddressInput
                    placeholder="To — type or click map"
                    dotColor="#e05c5c"
                    isPickingFor={pickingFor === 'dest'}
                    onPickOnMap={() => { setPickingFor('dest'); setOpenSection(null) }}
                    onSelect={(item) => {
                      const coords = [item.lon, item.lat]
                      setRouteDest(coords)
                      routeDestRef.current = coords
                      destMarkerRef.current?.remove()
                      destMarkerRef.current = new maplibregl.Marker({ color: '#e05c5c' })
                        .setLngLat([item.lon, item.lat]).addTo(mapRef.current)
                      mapRef.current?.flyTo({ center: [item.lon, item.lat], zoom: 15, duration: 800 })
                      if (routeOriginRef.current) runRouteCalculation(routeOriginRef.current, coords)
                    }}
                  />
                </div>

                {routeMode === 'calculating' && (
                  <div className="progress-wrap">
                    <div className="progress-label">{routeStep}</div>
                    <div className="progress-track">
                      <div className="progress-fill" style={{ width: `${routeProgress}%` }} />
                    </div>
                    <div className="progress-note">~2 min · Infrared SDK UTCI</div>
                  </div>
                )}

                {routeMode === 'showing' && routeResult && (
                  <div className="route-result">
                    <div className="route-row">
                      <div className="route-line-preview" style={{ background: '#1a7a4a' }} />
                      <span style={{ fontWeight: 600 }}>Coolest path: {routeResult.distance_m}m</span>
                    </div>
                    <div style={{ fontSize: 11, color: '#888', marginTop: 4 }}>
                      Avg UTCI {routeResult.avg_utci}°C · routes via planned tree streets
                    </div>
                    <button
                      onClick={clearRoute}
                      style={{
                        width: '100%', padding: '7px', marginTop: 10,
                        background: 'transparent', border: '1px solid #ddd',
                        borderRadius: 6, fontSize: 11, color: '#999', cursor: 'pointer',
                      }}
                    >
                      Clear route
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>

        </div>{/* end accordion */}

        <div className="sb-footer">
          Powered by <span>Infrared SDK</span> &nbsp;·&nbsp; infrared.city
        </div>
      </aside>

      {/* ── MAP ── */}
      <div className="map-wrap">
        <div id="map" ref={mapContainer} />

        <div className="view-controls">
          {['Top', '3D'].map(v => (
            <button key={v} className={`view-btn ${view === v ? 'active' : ''}`} onClick={() => setViewMode(v)}>{v}</button>
          ))}
        </div>

        {jobStatus !== 'running' && (
          <button className="analyse-floating-btn" onClick={activateDraw}>
            {isDrawing ? '✏ Drawing…' : '✏ Analyse area'}
          </button>
        )}

        {isDrawing && <div className="draw-hint">Click to add points · Double-click to finish</div>}

        {/* Route pick hint */}
        {pickingFor && (
          <div className="route-pick-hint">
            {pickingFor === 'origin' ? '🟢 Click your starting point' : '🔴 Click your destination'}
            <button className="route-pick-cancel" onClick={() => setPickingFor(null)}>Cancel</button>
          </div>
        )}

        {/* Draw confirm panel */}
        {drawnPolygon && !jobStatus && (
          <div className="draw-panel-float">
            <h4>Area selected</h4>
            <p style={{ margin: '4px 0 8px', fontSize: 13, color: '#555' }}>
              Selected area: <strong>{areaKm2} km²</strong>
            </p>
            {areaTooLarge && (
              <div className="area-warning">⚠ Area too large — draw a smaller zone for faster results</div>
            )}
            {!areaTooLarge && (
              <p style={{ margin: '0 0 8px', fontSize: 13, color: '#555' }}>
                Run wind + tree planting analysis. Takes ~20 seconds.
              </p>
            )}
            <button className="run-btn" onClick={runAnalysis} disabled={areaTooLarge}>Run analysis</button>
            <button className="cancel-btn" style={{ marginBottom: 4 }} onClick={activateDraw}>Draw again</button>
            <button className="cancel-btn" onClick={cancelDraw}>Cancel</button>
          </div>
        )}

        {/* Progress float */}
        {jobStatus === 'running' && (
          <div className="progress-float">
            <div className="progress-float-title">Running analysis</div>
            <div className="progress-label">{progressStep}</div>
            <div className="progress-track" style={{ margin: '6px 0' }}>
              <div className="progress-fill" style={{ width: `${progress}%` }} />
            </div>
            <div className="progress-note">~2 min · Infrared SDK UTCI · infrared.city</div>
          </div>
        )}

        {toast && <div className="toast">{toast}</div>}
      </div>

      {/* ── STREET PANEL ── */}
      <div className={`street-panel ${showStreetPanel && selectedStreet ? 'open' : ''}`}>
        {selectedStreet && (
          <>
            <div className="sp-topbar" />
            <div className="sp-header">
              <div>
                <div className="sp-title">Planting Analysis</div>
                <div className="sp-meta-row">
                  <span>📏 {selectedStreet.length_m}m street</span>
                  <span>🌡️ {parseFloat(selectedStreet.avg_utci ?? 32).toFixed(1)}°C UTCI</span>
                  <span>🌳 {selectedStreet.recommended_trees ?? 1} trees recommended</span>
                </div>
              </div>
              <button className="sp-close" onClick={() => { setShowStreetPanel(false); setSelectedStreet(null) }}>×</button>
            </div>

            <div className="sp-section">
              <div className="sp-section-label">How many trees?</div>
              <div className="tree-counter">
                <button className="tc-btn" onClick={() => setTreeCount(c => Math.max(1, c - 1))}>−</button>
                <span className="tc-val">{treeCount}</span>
                <button className="tc-btn" onClick={() => setTreeCount(c => c + 1)}>+</button>
              </div>
              <div className="tc-hint">
                Every 8m &nbsp;·&nbsp; estimated €{((selectedSpecies?.cost_eur ?? 480) * treeCount).toLocaleString()} total
              </div>
            </div>

            <div className="sp-section">
              <div className="sp-section-label">Recommended species</div>
              {streetSpecies.length === 0 && <div className="sp-loading">Loading species…</div>}
              {streetSpecies.map((sp, i) => {
                const isRec = i === 0
                const isSel = selectedSpecies?.name === sp.name
                return (
                  <div key={sp.name} className={`species-card ${isRec ? 'recommended' : ''} ${isSel ? 'selected' : ''}`}>
                    {isRec && <span className="best-match-badge">BEST MATCH</span>}
                    <div className="sc-header-row">
                      <span className="sc-name">{sp.name}</span>
                      <AllergyBadge risk={sp.allergy_risk} />
                    </div>
                    <div className="sc-row">
                      <CoolingDots score={sp.cooling_score} />
                      <span className="sc-cooling-label">cooling {sp.cooling_score}/10</span>
                    </div>
                    <div className="sc-best-for">Best for: {sp.best_for}</div>
                    <div className="sc-cost-row">
                      <span className="sc-cost-per">€{sp.cost_eur} per tree</span>
                      <span className="sc-cost-total">Total: €{(sp.cost_eur * treeCount).toLocaleString()}</span>
                    </div>
                    <button className={`sc-select-btn ${isSel ? 'active' : ''}`}
                      onClick={() => setSelectedSpecies(isSel ? null : sp)}>
                      {isSel ? 'Selected ✓' : 'Select'}
                    </button>
                  </div>
                )
              })}
            </div>

            {selectedSpecies && (
              <div className="sp-section">
                <div className="sp-section-label">Suppliers in Austria</div>
                {suppliers.length === 0 && <div className="sp-loading">No matching suppliers found</div>}
                {suppliers.map(s => (
                  <div key={s.name} className="supplier-card">
                    <div className="supp-name">{s.name}</div>
                    <div className="supp-meta">{s.location}</div>
                    <div className="supp-meta">Lead time: {s.lead_time} &nbsp;·&nbsp; Min order: {s.min_order} trees</div>
                    <a href={`mailto:${s.contact}`} className="supp-contact">{s.contact}</a>
                  </div>
                ))}
              </div>
            )}

            {selectedSpecies ? (
              <div className="panel-footer">
                <div className="pf-label">TOTAL ESTIMATE</div>
                <div className="pf-amount">€{(treeCount * selectedSpecies.cost_eur).toLocaleString()}</div>
                <div className="pf-breakdown">{treeCount} trees × €{selectedSpecies.cost_eur} per tree</div>
                <button className="sp-add-btn" onClick={() => { showToast('Added to plan ✓'); setShowStreetPanel(false); setSelectedStreet(null) }}>
                  Add to planting plan
                </button>
              </div>
            ) : (
              <div style={{ height: 20 }} />
            )}
          </>
        )}
      </div>

    </div>
  )
}
