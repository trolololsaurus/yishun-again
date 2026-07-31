/**
 * The columns of `incidents` the public site is allowed to emit.
 *
 * Both public readers of a single incident — the SSR page and the cached JSON
 * API at /api/incidents/[slug] — used to `select('*')`. RLS already restricts
 * the anon key to `is_published = TRUE` rows, so that was never a *row* leak;
 * it was a **column** leak waiting for a column. `select('*')` publishes
 * whatever the table happens to hold, so any future internal field lands on the
 * public internet the moment it is added, with no code change to review.
 *
 * That is not hypothetical here. Art generation is dormant, not deleted, and
 * the natural next step when it is re-enabled is persisting the prompt that
 * produced each image. Prompt cues are internal craft and must not be public
 * (see the War Room's art-prompt viewer, which is behind Cloudflare Access for
 * exactly this reason). An allowlist means such a column is private by default
 * and only becomes public if someone adds its name to this list on purpose.
 *
 * Deliberately excluded today: `agent_confidence` and `chaos_contribution`
 * (internal model scores), `hype_meter` (legacy, superseded by
 * `corroboration_count`), and the operator/pipeline bookkeeping columns.
 *
 * Anything added here is public forever the moment it deploys. Add nothing you
 * would not publish on purpose.
 */
export const PUBLIC_INCIDENT_COLUMNS =
  'id,slug,title,summary,classification,custom_label,severity,corroboration_count,' +
  'incident_date,published_at,first_reported_at,' +
  'area_name,block_number,latitude,longitude,' +
  'pixel_art_url,source_urls,source_timeline,deaths,injuries,' +
  'is_developing,update_count,latest_source_role,' +
  'is_milestone,milestone_type,milestone_value,' +
  'seo_title,seo_description,tags'
