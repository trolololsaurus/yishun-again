/**
 * Cloudflare R2 client and upload helpers.
 *
 * R2 is S3-compatible. The AWS SDK is configured to point at the R2 endpoint
 * instead of AWS. Region must be 'auto' — R2 does not use AWS regions.
 *
 * All asset URLs are served through the custom domain (assets.yishunagain.com),
 * not the raw R2 endpoint. Keep PUBLIC_BASE_URL as the sole source of truth for
 * building public URLs so it's easy to change the domain in one place.
 *
 * Key layout in the bucket:
 *   pixel-art/<incidentId>.png   — AI-generated scene image (also doubles as OG image)
 *
 * Usage (ESM):
 *   import { uploadPixelArt, getPublicUrl } from '@/infra/cloudflare/r2.config.js'
 *
 * Requires: @aws-sdk/client-s3
 *   npm install @aws-sdk/client-s3
 */

import { S3Client, PutObjectCommand, DeleteObjectCommand, HeadObjectCommand } from '@aws-sdk/client-s3'

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const ACCOUNT_ID       = process.env.CF_R2_ACCOUNT_ID
const ACCESS_KEY_ID    = process.env.CF_R2_ACCESS_KEY_ID
const SECRET_ACCESS_KEY = process.env.CF_R2_SECRET_ACCESS_KEY

export const BUCKET_NAME    = process.env.CF_R2_BUCKET_NAME ?? 'yishun-assets'
export const PUBLIC_BASE_URL = 'https://assets.yishunagain.com'

if (!ACCOUNT_ID || !ACCESS_KEY_ID || !SECRET_ACCESS_KEY) {
  throw new Error(
    'Missing Cloudflare R2 credentials. ' +
    'Set CF_R2_ACCOUNT_ID, CF_R2_ACCESS_KEY_ID, and CF_R2_SECRET_ACCESS_KEY.'
  )
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export const r2 = new S3Client({
  region: 'auto',
  endpoint: `https://${ACCOUNT_ID}.r2.cloudflarestorage.com`,
  credentials: {
    accessKeyId: ACCESS_KEY_ID,
    secretAccessKey: SECRET_ACCESS_KEY,
  },
})

// ---------------------------------------------------------------------------
// Key helpers
// ---------------------------------------------------------------------------

/**
 * Returns the R2 object key for a given incident's pixel art.
 * @param {string} incidentId - UUID of the incident
 */
export function pixelArtKey(incidentId) {
  return `pixel-art/${incidentId}.png`
}

/**
 * Converts an R2 object key to its full public CDN URL.
 * @param {string} key
 */
export function getPublicUrl(key) {
  return `${PUBLIC_BASE_URL}/${key}`
}

// ---------------------------------------------------------------------------
// Upload helpers
// ---------------------------------------------------------------------------

/**
 * Uploads the AI-generated pixel art image for an incident.
 * Images are immutable once generated — cache for 1 year.
 *
 * @param {Buffer|Uint8Array} imageBuffer - PNG image data
 * @param {string} incidentId - UUID of the incident
 * @returns {Promise<string>} Public CDN URL of the uploaded image
 */
export async function uploadPixelArt(imageBuffer, incidentId) {
  const key = pixelArtKey(incidentId)

  await r2.send(new PutObjectCommand({
    Bucket:       BUCKET_NAME,
    Key:          key,
    Body:         imageBuffer,
    ContentType:  'image/png',
    CacheControl: 'public, max-age=31536000, immutable',
  }))

  return getPublicUrl(key)
}

// ---------------------------------------------------------------------------
// Management helpers
// ---------------------------------------------------------------------------

/**
 * Deletes an asset from the bucket by its key.
 * Used by the War Room when an incident is deleted.
 *
 * @param {string} key - R2 object key (e.g. "pixel-art/<uuid>.png")
 */
export async function deleteAsset(key) {
  await r2.send(new DeleteObjectCommand({
    Bucket: BUCKET_NAME,
    Key:    key,
  }))
}

/**
 * Checks whether an object exists in the bucket without downloading it.
 * Returns true if it exists, false if not found.
 *
 * @param {string} key
 * @returns {Promise<boolean>}
 */
export async function assetExists(key) {
  try {
    await r2.send(new HeadObjectCommand({ Bucket: BUCKET_NAME, Key: key }))
    return true
  } catch (err) {
    if (err.name === 'NotFound' || err.$metadata?.httpStatusCode === 404) {
      return false
    }
    throw err
  }
}
