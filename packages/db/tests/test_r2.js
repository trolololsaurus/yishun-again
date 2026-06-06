/**
 * R2 connection smoke test.
 * Uploads a small text object, confirms it exists, then deletes it.
 *
 * Run from the project root:
 *   node --env-file=.env packages/db/tests/test_r2.js
 */

import {
  S3Client,
  PutObjectCommand,
  HeadObjectCommand,
  DeleteObjectCommand,
} from '@aws-sdk/client-s3'

// ---------------------------------------------------------------------------
// Credentials (injected via --env-file=.env)
// ---------------------------------------------------------------------------

const ACCOUNT_ID        = process.env.CF_R2_ACCOUNT_ID
const ACCESS_KEY_ID     = process.env.CF_R2_ACCESS_KEY_ID
const SECRET_ACCESS_KEY = process.env.CF_R2_SECRET_ACCESS_KEY
const BUCKET_NAME       = process.env.CF_R2_BUCKET_NAME || 'yishun-assets'

const missing = ['CF_R2_ACCOUNT_ID', 'CF_R2_ACCESS_KEY_ID', 'CF_R2_SECRET_ACCESS_KEY']
  .filter(k => !process.env[k])

if (missing.length) {
  console.error(`FAIL — missing env vars: ${missing.join(', ')}`)
  console.error('Run with: node --env-file=.env packages/db/tests/test_r2.js')
  process.exit(1)
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

const r2 = new S3Client({
  region: 'auto',
  endpoint: `https://${ACCOUNT_ID}.r2.cloudflarestorage.com`,
  credentials: {
    accessKeyId: ACCESS_KEY_ID,
    secretAccessKey: SECRET_ACCESS_KEY,
  },
})

// ---------------------------------------------------------------------------
// Test
// ---------------------------------------------------------------------------

const TEST_KEY     = `_test/connection-smoke-${Date.now()}.txt`
const TEST_CONTENT = Buffer.from('Yishun Again R2 connection test — safe to delete.')

async function run() {
  console.log(`Bucket : ${BUCKET_NAME}`)
  console.log(`Key    : ${TEST_KEY}`)
  console.log()

  // 1. Upload
  process.stdout.write('1. Uploading test object ... ')
  await r2.send(new PutObjectCommand({
    Bucket:      BUCKET_NAME,
    Key:         TEST_KEY,
    Body:        TEST_CONTENT,
    ContentType: 'text/plain',
  }))
  console.log('OK')

  // 2. Confirm exists (HeadObject throws if missing)
  process.stdout.write('2. Confirming object exists ... ')
  const head = await r2.send(new HeadObjectCommand({ Bucket: BUCKET_NAME, Key: TEST_KEY }))
  console.log(`OK (${head.ContentLength} bytes)`)

  // 3. Delete
  process.stdout.write('3. Deleting test object    ... ')
  await r2.send(new DeleteObjectCommand({ Bucket: BUCKET_NAME, Key: TEST_KEY }))
  console.log('OK')

  console.log()
  console.log('R2 connection PASSED — yishun-assets is reachable and writable.')
}

run().catch(err => {
  console.error()
  console.error('R2 connection FAILED')
  console.error(`  ${err.name}: ${err.message}`)
  if (err.$metadata) {
    console.error(`  HTTP ${err.$metadata.httpStatusCode} — requestId: ${err.$metadata.requestId}`)
  }
  process.exit(1)
})
