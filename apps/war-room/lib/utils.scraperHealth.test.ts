/**
 * Guard: discovery-vs-primary classification for the queue health panel.
 *
 * A discovery adapter (sitemap/search) erroring while its outlet's primary
 * scraper is healthy must NOT read as an outlet outage — that is the bug the
 * operator hit ("Straits Times ERROR" while ST was publishing). `primaryIdOf`
 * must map every discovery id onto a real primary id, or the panel can never
 * find the healthy primary and demote the alarm.
 *
 * Run: node --test apps/war-room/lib/utils.scraperHealth.test.ts
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { isDiscoverySource, primaryIdOf } from './utils.ts'

test('sitemap and search adapters are discovery; primary scrapers are not', () => {
  assert.equal(isDiscoverySource('straits_times_sitemap'), true)
  assert.equal(isDiscoverySource('mustsharenews_search'), true)
  assert.equal(isDiscoverySource('straits_times'), false)
  assert.equal(isDiscoverySource('cna'), false)
  assert.equal(isDiscoverySource('edmw'), false)
})

test('primaryIdOf strips the suffix to the outlet id', () => {
  assert.equal(primaryIdOf('straits_times_sitemap'), 'straits_times')
  assert.equal(primaryIdOf('the_independent_search'), 'the_independent')
  assert.equal(primaryIdOf('cna_sitemap'), 'cna')
  // A primary id is returned unchanged, so passing one through is harmless.
  assert.equal(primaryIdOf('straits_times'), 'straits_times')
})
