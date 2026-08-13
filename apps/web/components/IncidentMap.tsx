'use client'

import 'maplibre-gl/dist/maplibre-gl.css'
import { useEffect, useRef, useState, useCallback } from 'react'
import { useRouter }         from 'next/navigation'
import type { FilterState, MapFeature } from '@/lib/types'
import { pinColor, classIcon, classLabel, classTooltip, HYPE_TOOLTIP, severityDiamonds, severityTooltip, hypeMeter, hypeFromSources, escapeHtml, matchesClassFilter, spreadOverlappingPins } from '@/lib/utils'

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

interface MarkerRec { marker: any; el: HTMLElement; classification: string }

export function IncidentMap({ features, activeFilter, selectedYear }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef       = useRef<any>(null)
  const mlRef        = useRef<any>(null)          // the maplibregl module
  const popupRef     = useRef<any>(null)
  const markersRef   = useRef<MarkerRec[]>([])
  const openIdRef    = useRef<string | null>(null)  // feature id of the open preview
  const canHoverRef  = useRef(true)
  const loadedRef    = useRef(false)
  const router       = useRouter()
  const [mapStatus, setMapStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [errorMsg,  setErrorMsg]  = useState('')

  // Latest activeFilter, readable from the imperative marker builders (which run
  // on load / year change) without making them depend on it. Seeded from the
  // initial filter and kept in sync by the filter effect below — never written
  // during render.
  const filterRef = useRef(activeFilter)

  // ── Preview popup — image thumb + escaped teaser ───────────────────────────
  // Every interpolated field is escaped: title/summary/custom_label are
  // LLM/scrape-derived and can reach production without review via auto-publish,
  // so this setHTML is a stored-XSS sink otherwise (the same rule the old circle
  // popup carried). severityDiamonds/hypeMeter are first-party glyphs, not input.
  const showPopup = useCallback((f: MapFeature) => {
    const map = mapRef.current, popup = popupRef.current
    if (!map || !popup) return
    const p = f.properties
    const lightning = hypeFromSources(p.corroboration_count)
    const color     = pinColor(p.classification, p.custom_label)

    const thumb = p.pixel_art_url
      ? `<img src="${escapeHtml(p.pixel_art_url)}" alt="" style="width:100%;height:120px;object-fit:cover;display:block;border-bottom:1px solid #1E2D4A" />`
      : ''
    const teaser = p.summary
      ? `<div style="font-size:13px;color:#7A8BAA;margin-top:6px;line-height:1.4;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">${escapeHtml(p.summary)}</div>`
      : ''

    popup
      .setLngLat(f.geometry.coordinates)
      .setHTML(
        `<div style="width:240px;font-family:'Courier Prime',monospace;color:#E8E8F0">` +
        thumb +
        `<div style="padding:10px">` +
        `<div style="font-size:14px;color:#7A8BAA;margin-bottom:5px">` +
        `<span style="color:${escapeHtml(color)}" title="${escapeHtml(classTooltip(p.classification, p.custom_label))}">${escapeHtml(classIcon(p.classification, p.custom_label))} ${escapeHtml(classLabel(p.classification, p.custom_label))}</span>` +
        ` <span title="${escapeHtml(severityTooltip(p.severity))}">${severityDiamonds(p.severity)}</span>` +
        (lightning > 0 ? ` <span title="${escapeHtml(HYPE_TOOLTIP)}">${hypeMeter(lightning)}</span>` : '') +
        `</div>` +
        `<div style="font-weight:700;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${escapeHtml(p.title)}</div>` +
        teaser +
        `</div></div>`
      )
      .addTo(map)
    openIdRef.current = p.id
  }, [])

  const hidePopup = useCallback(() => {
    popupRef.current?.remove()
    openIdRef.current = null
  }, [])

  const applyFilterToMarkers = useCallback((filter: FilterState) => {
    for (const { el, classification } of markersRef.current) {
      // Custom (CULTURE) pins ride under GOOD VIBES (heart) — matchesClassFilter.
      el.style.display = matchesClassFilter(filter, classification) ? '' : 'none'
    }
  }, [])

  // Rebuild every marker from a feature list. Called on load (SSR `features`) and
  // on each year change (fetched GeoJSON) — the HTML-marker equivalent of the old
  // source.setData. Emoji pins (Decision B): a MapLibre symbol layer renders the
  // style's glyph set, which has no emoji, so ❤️🤡💀 would be tofu. A DOM element
  // renders them natively.
  const renderMarkers = useCallback((list: MapFeature[]) => {
    const map = mapRef.current, ml = mlRef.current
    if (!map || !ml) return

    for (const { marker } of markersRef.current) marker.remove()
    markersRef.current = []

    // Fan out co-located pins so each one stays clickable. Done here rather
    // than in the map API or page.tsx because both feed markers through this
    // one function, and because the stored coordinate must stay the true
    // address — the spread is presentation, not data.
    for (const f of spreadOverlappingPins(list)) {
      const { classification, custom_label, title, slug, id } = f.properties

      // Emoji-only pin: no circle badge. Centering an emoji inside a circle is
      // unreliable across platforms (colour-emoji glyph baselines differ, so it
      // reads as misaligned), so the emoji IS the marker — anchored at its centre
      // with a drop-shadow for legibility on the light basemap. Classification
      // reads from the glyph itself (❤️🤡💀).
      const el = document.createElement('div')
      el.style.cssText =
        'cursor:pointer;font-size:22px;line-height:1;' +
        'filter:drop-shadow(0 1px 1.5px rgba(0,0,0,0.55))'
      el.textContent = classIcon(classification, custom_label)
      el.setAttribute('role', 'button')
      el.setAttribute('aria-label', title)

      if (canHoverRef.current) {
        el.addEventListener('mouseenter', () => showPopup(f))
        el.addEventListener('mouseleave', hidePopup)
        el.addEventListener('click', e => { e.stopPropagation(); router.push(`/incidents/${slug}`) })
      } else {
        // Touch: first tap previews, second tap on the same pin navigates.
        el.addEventListener('click', e => {
          e.stopPropagation()
          if (openIdRef.current === id) router.push(`/incidents/${slug}`)
          else showPopup(f)
        })
      }

      const marker = new ml.Marker({ element: el }).setLngLat(f.geometry.coordinates).addTo(map)
      markersRef.current.push({ marker, el, classification })
    }

    applyFilterToMarkers(filterRef.current)
  }, [router, showPopup, hidePopup, applyFilterToMarkers])

  // ── Initialise MapLibre once ───────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return
    let destroyed = false
    let ro: ResizeObserver | null = null
    let loadTimeout: ReturnType<typeof setTimeout>

    canHoverRef.current = typeof window !== 'undefined'
      && window.matchMedia('(hover: hover)').matches

    ;(async () => {
      const ml = (await import('maplibre-gl')).default
      if (destroyed || !containerRef.current) return
      mlRef.current = ml

      const map = new ml.Map({
        container: containerRef.current,
        style:     MAP_STYLE,
        center:    [103.8386, 1.4275],  // Yishun town centre (incident cluster)
        zoom:      14,
        minZoom:   13.3,                 // keep the view focused on Yishun — no zooming out to the island
        maxBounds: [[103.815, 1.404], [103.862, 1.451]],  // tight box around the Yishun planning area
        attributionControl: true,
      })
      mapRef.current = map
      map.resize()

      ro = new ResizeObserver(() => { if (!destroyed) map.resize() })
      ro.observe(containerRef.current!)

      // Safety net: if neither 'load' nor 'error' fires within 12s, surface it.
      // MapLibre silently swallows some style-fetch failures without emitting
      // 'error', which would leave the loading overlay up forever.
      loadTimeout = setTimeout(() => {
        if (destroyed || loadedRef.current) return
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
        map.resize()
        setTimeout(() => { if (!destroyed) map.resize() }, 200)

        // Shared preview popup. offset clears the 28px pin.
        popupRef.current = new ml.Popup({
          closeButton: false, closeOnClick: false, maxWidth: '260px', offset: 18,
        })

        // Build the initial markers from the SSR `features` prop.
        renderMarkers(features)

        // Tap on the map background dismisses an open preview (the touch
        // two-stage flow, and a convenience on desktop).
        map.on('click', hidePopup)
      })
    })()

    return () => {
      destroyed = true
      clearTimeout(loadTimeout)
      ro?.disconnect()
      for (const { marker } of markersRef.current) marker.remove()
      markersRef.current = []
      mapRef.current?.remove()
      mapRef.current = null
    }
    // Init once — renderMarkers/hidePopup are stable and `features` is only the
    // initial seed (year changes come through the effect below).
  }, [renderMarkers, hidePopup]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Re-fetch + rebuild markers when the year changes ───────────────────────
  useEffect(() => {
    if (selectedYear == null) return
    let cancelled = false

    fetch(`/api/map?year=${selectedYear}`)
      .then(r => r.json())
      .then((geojson) => {
        if (cancelled || !geojson || !Array.isArray(geojson.features)) return
        const map = mapRef.current
        if (!map) return
        const apply = () => { if (!cancelled) renderMarkers(geojson.features) }
        if (loadedRef.current) apply()
        else map.once('load', apply)
      })
      .catch(() => { /* keep current pins on network error */ })

    return () => { cancelled = true }
  }, [selectedYear, renderMarkers])

  // ── Show/hide markers by classification (replaces the old setFilter) ────────
  // Also keeps filterRef current for the marker builders, which apply the filter
  // to freshly-built pins after a load / year-change rebuild.
  useEffect(() => {
    filterRef.current = activeFilter
    applyFilterToMarkers(activeFilter)
  }, [activeFilter, applyFilterToMarkers])

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
