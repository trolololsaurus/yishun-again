'use client'

import 'maplibre-gl/dist/maplibre-gl.css'
import { useEffect, useRef, useState } from 'react'
import { useRouter }         from 'next/navigation'
import type { FilterState, MapFeature } from '@/lib/types'
import { PIN_COLOR, classIcon, classLabel, classTooltip, pinColor, HYPE_TOOLTIP, severityDiamonds, severityTooltip, hypeMeter, hypeFromSources, escapeHtml } from '@/lib/utils'

// OpenFreeMap Liberty — keyless, served via Cloudflare CDN. The env var lets us
// override per-environment, but the hardcoded fallback guarantees the map still
// loads if the var is ever unset or blank, so it can never be a single point of
// failure. `||` (not `??`) so an empty-string env var also falls back.
const FALLBACK_MAP_STYLE = 'https://tiles.openfreemap.org/styles/liberty'
const MAP_STYLE = process.env.NEXT_PUBLIC_MAPLIBRE_STYLE || FALLBACK_MAP_STYLE

interface Props {
  features:     MapFeature[]
  activeFilter: FilterState
  selectedYear?: number
}

export function IncidentMap({ features, activeFilter, selectedYear }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef       = useRef<any>(null)
  const popupRef     = useRef<any>(null)
  const loadedRef    = useRef(false)
  const router       = useRouter()
  const [mapStatus, setMapStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [errorMsg,  setErrorMsg]  = useState('')

  // Initialise MapLibre once
  useEffect(() => {
    if (!containerRef.current) return
    let destroyed = false
    let ro: ResizeObserver | null = null
    let loadTimeout: ReturnType<typeof setTimeout>

    ;(async () => {
      const ml = (await import('maplibre-gl')).default
      if (destroyed || !containerRef.current) return

      const map = new ml.Map({
        container: containerRef.current,
        style:     MAP_STYLE,
        center:    [103.8350, 1.4290],  // Yishun, Singapore
        zoom:      13.5,
        maxBounds: [[103.80, 1.40], [103.87, 1.46]],  // lock to Yishun — no panning to Malaysia
        attributionControl: true,
      })
      mapRef.current = map

      // Resize immediately after init — container may already have dimensions
      map.resize()

      // ResizeObserver keeps canvas in sync whenever the container resizes
      ro = new ResizeObserver(() => { if (!destroyed) map.resize() })
      ro.observe(containerRef.current!)

      // Safety net: if neither 'load' nor 'error' fires within 12s, surface the error.
      // MapLibre silently swallows some style-fetch failures (404, certain CORS errors)
      // without emitting 'error', which would leave the loading overlay up forever.
      loadTimeout = setTimeout(() => {
        if (destroyed || loadedRef.current) return
        // Don't assert "unreachable" — the style, tiles, CSP and WebGL can all be
        // healthy yet 'load' still not fire (e.g. a render/rAF stall in a
        // backgrounded or headless tab). The timeout says "didn't finish", not "network down".
        console.error('[IncidentMap] map load did not complete within 12s (tiles may be reachable). style URL:', MAP_STYLE)
        setErrorMsg('Map failed to load — reload to retry')
        setMapStatus('error')
      }, 12_000)

      map.on('error', (e: any) => {
        clearTimeout(loadTimeout)
        console.error('[IncidentMap] tile/style error:', e.error ?? e)
        if (destroyed || loadedRef.current) return
        setErrorMsg(e.error?.message ?? 'Tile source unavailable')
        setMapStatus('error')
      })

      map.on('load', () => {
        clearTimeout(loadTimeout)
        loadedRef.current = true
        setMapStatus('ready')
        // Double-resize: once immediately, once after 200 ms to catch late flex/paint
        map.resize()
        setTimeout(() => { if (!destroyed) map.resize() }, 200)

        // No paint-property overrides — the OpenFreeMap Liberty style handles its
        // own basemap colours. Only the incident-pin circle layer is added below.

        // GeoJSON source — all incident markers
        map.addSource('incidents', {
          type: 'geojson',
          data: { type: 'FeatureCollection', features },
        })

        // Circle layer — colour by classification
        // heart=teal-cyan (GOOD VIBES), clown=bright yellow (ABSURDITIES), dagger=coral red (DARK EVENTS)
        map.addLayer({
          id:     'incidents',
          type:   'circle',
          source: 'incidents',
          paint:  {
            'circle-radius': [
              'interpolate', ['linear'], ['zoom'],
              12, 7,
              15, 11,
            ],
            'circle-color': [
              'case',
              ['all', ['==', ['get', 'classification'], 'custom'], ['==', ['get', 'custom_label'], 'CULTURE']],
              PIN_COLOR.culture,
              ['match', ['get', 'classification'],
                'heart',  PIN_COLOR.heart,
                'clown',  PIN_COLOR.clown,
                'dagger', PIN_COLOR.dagger,
                '#7A8BAA',
              ],
            ],
            'circle-opacity':       0.92,
            'circle-stroke-width':  1.5,
            'circle-stroke-color': '#070B14',
          },
        })

        // Hover popup
        const popup = new ml.Popup({ closeButton: false, closeOnClick: false, maxWidth: '260px' })
        popupRef.current = popup

        map.on('mouseenter', 'incidents', (e: any) => {
          if (!e.features?.length) return
          map.getCanvas().style.cursor = 'pointer'
          const f = e.features[0]
          const coords = [...f.geometry.coordinates] as [number, number]
          const { title, classification, custom_label, severity, corroboration_count } = f.properties
          const lightning = hypeFromSources(corroboration_count)

          const markerColor = pinColor(classification, custom_label)

          // setHTML builds raw markup — title and custom_label are LLM/scrape
          // derived and can reach production without review via auto-publish,
          // so every interpolated field is escaped (stored-XSS sink otherwise).
          popup
            .setLngLat(coords)
            .setHTML(
              `<div style="padding:10px;font-family:'Courier Prime',monospace;font-size:16px;color:#E8E8F0">` +
              `<div style="font-size:14px;color:#7A8BAA;margin-bottom:5px">` +
              `<span style="color:${escapeHtml(markerColor)}" title="${escapeHtml(classTooltip(classification, custom_label))}">${escapeHtml(classIcon(classification, custom_label))} ${escapeHtml(classLabel(classification, custom_label))}</span>` +
              ` <span title="${escapeHtml(severityTooltip(severity))}">${severityDiamonds(severity)}</span>` +
              (lightning > 0 ? ` <span title="${escapeHtml(HYPE_TOOLTIP)}">${hypeMeter(lightning)}</span>` : '') +
              `</div>` +
              `<div style="font-weight:700;line-height:1.4;` +
              `display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">` +
              `${escapeHtml(title)}</div></div>`
            )
            .addTo(map)
        })

        map.on('mouseleave', 'incidents', () => {
          map.getCanvas().style.cursor = ''
          popup.remove()
        })

        // Click → navigate to incident page
        map.on('click', 'incidents', (e: any) => {
          const slug = e.features?.[0]?.properties?.slug
          if (slug) router.push(`/incidents/${slug}`)
        })
      })
    })()

    return () => {
      destroyed = true
      clearTimeout(loadTimeout)
      ro?.disconnect()
      mapRef.current?.remove()
      mapRef.current = null
    }
  }, [router]) // eslint-disable-line react-hooks/exhaustive-deps -- intentional: init once

  // Re-fetch markers when the selected year changes and swap them into the
  // 'incidents' source. setData replaces the whole FeatureCollection, so old
  // pins are removed and the filtered year's pins added in one step.
  useEffect(() => {
    if (selectedYear == null) return
    let cancelled = false

    fetch(`/api/map?year=${selectedYear}`)
      .then(r => r.json())
      .then((geojson) => {
        if (cancelled || !geojson || !Array.isArray(geojson.features)) return
        const map = mapRef.current
        if (!map) return
        const apply = () => {
          const src = map.getSource('incidents')
          if (src) src.setData(geojson)
        }
        if (map.getSource('incidents')) apply()
        else map.once('load', apply)
      })
      .catch(() => { /* keep current pins on network error */ })

    return () => { cancelled = true }
  }, [selectedYear])

  // Sync classification filter with MapLibre layer filter
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    const applyFilter = () => {
      if (!map.getLayer('incidents')) return
      map.setFilter(
        'incidents',
        activeFilter === 'all' ? null : ['==', ['get', 'classification'], activeFilter]
      )
    }

    if (map.isStyleLoaded()) {
      applyFilter()
    } else {
      map.once('load', applyFilter)
    }
  }, [activeFilter])

  return (
    <div className="relative w-full h-full" style={{ width: '100%', height: '100%' }}>
      <div
        ref={containerRef}
        className="w-full h-full"
        aria-label="Yishun incident map"
      />
      {mapStatus === 'loading' && (
        <div
          className="absolute inset-0 flex items-center justify-center"
          style={{ background: 'var(--color-map-bg)', fontFamily: 'var(--font-body)', fontSize: '13px', color: 'var(--color-text-secondary)' }}
        >
          Loading map…
        </div>
      )}
      {mapStatus === 'error' && (
        <div
          className="absolute inset-0 flex flex-col items-center justify-center gap-2"
          style={{ background: 'var(--color-map-bg)' }}
        >
          <span style={{ fontFamily: 'var(--font-display)', fontSize: '11px', color: 'var(--color-dark-events)', letterSpacing: '0.1em' }}>
            MAP UNAVAILABLE
          </span>
          <span style={{ fontFamily: 'var(--font-body)', fontSize: '12px', color: 'var(--color-text-secondary)' }}>
            {errorMsg}
          </span>
        </div>
      )}
    </div>
  )
}
