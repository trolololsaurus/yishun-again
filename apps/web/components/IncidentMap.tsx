'use client'

import 'maplibre-gl/dist/maplibre-gl.css'
import { useEffect, useRef } from 'react'
import { useRouter }         from 'next/navigation'
import type { FilterState, MapFeature } from '@/lib/types'
import { PIN_COLOR, CLASS_LABEL, CLASS_TOOLTIP, HYPE_TOOLTIP, severityDiamonds, severityTooltip, hypeMeter } from '@/lib/utils'

interface Props {
  features:     MapFeature[]
  activeFilter: FilterState
  selectedYear?: number
}

export function IncidentMap({ features, activeFilter, selectedYear }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef       = useRef<any>(null)
  const popupRef     = useRef<any>(null)
  const router       = useRouter()

  // Initialise MapLibre once
  useEffect(() => {
    if (!containerRef.current) return
    let destroyed = false
    let ro: ResizeObserver | null = null

    console.log('IncidentMap mount, container:', containerRef.current, 'dimensions:', containerRef.current?.offsetWidth, containerRef.current?.offsetHeight)

    ;(async () => {
      const ml = (await import('maplibre-gl')).default
      if (destroyed || !containerRef.current) return

      const map = new ml.Map({
        container: containerRef.current,
        style:     process.env.NEXT_PUBLIC_MAPLIBRE_STYLE ?? 'https://tiles.stadiamaps.com/styles/alidade_smooth_dark.json',
        center:    [103.8350, 1.4290],  // Yishun, Singapore
        zoom:      13.5,
        maxBounds: [[103.80, 1.40], [103.87, 1.46]],  // lock to Yishun — no panning to Malaysia
        attributionControl: true,
      })
      mapRef.current = map

      // Resize immediately after init — container may already have dimensions
      map.resize()

      console.log('MapLibre map created, style:', map.getStyle())

      // ResizeObserver keeps canvas in sync whenever the container resizes
      ro = new ResizeObserver(() => { if (!destroyed) map.resize() })
      ro.observe(containerRef.current!)

      map.on('load', () => {
        // Double-resize: once immediately, once after 200 ms to catch late flex/paint
        map.resize()
        setTimeout(() => { if (!destroyed) map.resize() }, 200)

        // No paint-property overrides — the Stadia Alidade Smooth Dark style
        // handles its own colours correctly. Earlier teal/amber tints turned the
        // roads green and made the map worse, so they were removed.

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
              'match', ['get', 'classification'],
              'heart',  PIN_COLOR.heart,
              'clown',  PIN_COLOR.clown,
              'dagger', PIN_COLOR.dagger,
              '#7A8BAA',
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
          const { title, classification, severity, hype_meter } = f.properties
          const icons: Record<string, string> = { heart: '❤️', clown: '🤡', dagger: '💀' }

          const classColor = PIN_COLOR[classification] ?? '#7A8BAA'

          popup
            .setLngLat(coords)
            .setHTML(
              `<div style="padding:10px;font-family:'Courier Prime',monospace;font-size:16px;color:#E8E8F0">` +
              `<div style="font-size:14px;color:#7A8BAA;margin-bottom:5px">` +
              `<span style="color:${classColor}" title="${CLASS_TOOLTIP[classification] ?? ''}">${icons[classification] ?? ''} ${CLASS_LABEL[classification] ?? ''}</span>` +
              ` <span title="${severityTooltip(severity)}">${severityDiamonds(severity)}</span>` +
              (hype_meter > 0 ? ` <span title="${HYPE_TOOLTIP}">${hypeMeter(hype_meter)}</span>` : '') +
              `</div>` +
              `<div style="font-weight:700;line-height:1.4;` +
              `display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">` +
              `${title}</div></div>`
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
      ro?.disconnect()
      mapRef.current?.remove()
      mapRef.current = null
    }
  }, [router]) // eslint-disable-line react-hooks/exhaustive-deps — intentional: init once

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
    <div
      ref={containerRef}
      className="w-full h-full"
      style={{ width: '100%', height: '100%' }}
      aria-label="Yishun incident map"
    />
  )
}
