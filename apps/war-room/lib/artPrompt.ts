// Mirror of the SDXL prompt builder in
// `packages/agents/art/generate_pixel_art.py` (`_build_prompt` +
// `NEGATIVE_PROMPT`).
//
// The generator runs on Modal and the War Room cannot call into it, so this is
// a deliberate second copy rather than a shared module. It exists to answer one
// operator question — "what would the art agent actually send for this
// incident?" — and it is read-only: nothing here feeds the pipeline.
//
// ⚠️ If you change the Python builder, change this too. `test_art_prompt.py`
// (packages/agents/) asserts the two produce identical strings for a fixed set
// of inputs, so a drift shows up as a test failure rather than as a wrong
// prompt on screen.

export const NEGATIVE_PROMPT =
  'photorealistic, 3d render, photograph, blurry, people faces, ' +
  'text, watermark, low quality, deformed, ugly, out of frame'

export const CLASSIFICATION_MOOD: Record<string, string> = {
  heart:  'warm amber lighting, community gathering, cheerful atmosphere, hopeful tones',
  clown:  'chaotic void deck, bright garish colours, absurd props, comedic mayhem',
  dagger: 'dark night scene, harsh shadows, police tape, deep red and blue tones, ominous',
  custom: 'dramatic Yishun HDB environment, cinematic lighting',
}

/**
 * The positive prompt the art agent would send to SDXL for this incident.
 *
 * Note it is derived from classification + area_name only — the incident title
 * is NOT part of the prompt (the Python builder takes `title` and ignores it).
 * That is why two incidents in the same area with the same classification
 * produce byte-identical prompts.
 */
export function buildArtPrompt(classification: string, areaName: string | null | undefined): string {
  // `||` not `??` — mirrors Python's `area_name or "Yishun"`, where an empty
  // string falls back too.
  const location = areaName || 'Yishun'
  const mood     = CLASSIFICATION_MOOD[classification] ?? CLASSIFICATION_MOOD.custom
  return (
    `HD-2D pixel art, HDB void deck Singapore, ${location}, ` +
    `${mood}, isometric view, JRPG style, ` +
    `detailed pixel art scene, masterpiece`
  )
}
