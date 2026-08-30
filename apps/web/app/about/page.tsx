import type { Metadata } from 'next'
import { SITE_URL } from '@/lib/site'

export const metadata: Metadata = {
  title:       'About',
  description: "What is Yishun Again? A satirical incident archive for Yishun, Nee Soon — Singapore's most eventful estate.",
  alternates:  { canonical: `${SITE_URL}/about` },
  openGraph: {
    title:       'About — Yishun Again',
    description: "What is Yishun Again? A satirical incident archive for Yishun, Nee Soon — Singapore's most eventful estate.",
    url:         `${SITE_URL}/about`,
    images:      [{ url: `${SITE_URL}/og-default.jpg`, width: 1200, height: 630 }],
    type:        'website',
  },
  twitter: { card: 'summary_large_image' },
}

export default function AboutPage() {
  return (
    <article className="max-w-2xl mx-auto px-4 py-10 w-full font-body">
      <h1 className="font-display text-amber mb-8 leading-relaxed" style={{ fontSize: '32px' }}>
        WHAT IS<br />YISHUN AGAIN?
      </h1>

      <section className="space-y-4 text-text-primary leading-relaxed" style={{ fontSize: '16px' }}>
        <p>
          Yishun has a reputation as the Hive of Scum and Villainy. You&apos;ve heard it.
          We&apos;re the ones documenting it.
        </p>
        <p>
          Yishun Again is a satirical incident archive covering Nee Soon, Singapore. Nobody
          agrees on why the reputation exists. Everybody agrees it&apos;s earned.
        </p>
        <p>
          <strong className="text-amber">The Devil&apos;s Ring.</strong> One circular stretch of
          tarmac that shows up in this archive more than anywhere else in the estate. We didn&apos;t
          name it that to be dramatic. We named it that because the data kept forcing our hand.
        </p>
        <p>
          <strong className="text-amber">How this works.</strong> Our AI pipeline scrapes public
          news, drafts each incident, and hands it to a human for review. Nothing goes live
          unreviewed. No rumours. Every entry is backed by at least one verifiable public source.
          We&apos;re satirical about Yishun&apos;s reputation, and we back it with verifiable facts.
        </p>
        <p>
          <strong className="text-amber">The Chaos Index.</strong> Every incident is scored:
          daggers count triple, clowns count 1×, hearts subtract. It is not peer-reviewed, and
          we will not be defending it in an academic journal.
        </p>
      </section>

      <hr className="border-border my-8" />

      {/* Classification Guide — placed before the legal disclaimer */}
      <section>
        <h2 className="font-display text-amber mb-4" style={{ fontSize: '18px' }}>
          Classification Guide
        </h2>
        <div className="space-y-3" style={{ fontSize: '16px' }}>
          {[
            { icon: '❤️', label: 'GOOD VIBES',  desc: 'Good news, community wins, feel-good moments.' },
            { icon: '🤡', label: 'ABSURDITIES', desc: 'Absurd, baffling, or inexplicably stupid behaviour. No serious harm.' },
            { icon: '💀', label: 'DARK EVENTS', desc: 'Crime, violence, serious incidents. Reported facts only.' },
          ].map(({ icon, label, desc }) => (
            <div key={label} className="flex gap-3">
              <span className="text-xl flex-none">{icon}</span>
              <div>
                <span className="font-bold text-text-primary">{label} — </span>
                <span className="text-text-secondary">{desc}</span>
              </div>
            </div>
          ))}
        </div>
      </section>

      <hr className="border-border my-8" />

      {/* Severity & Hype Guide — explains the ◆ and ⚡ meters */}
      <section>
        <h2 className="font-display text-amber mb-4" style={{ fontSize: '18px' }}>
          Severity &amp; Hype
        </h2>
        <div className="space-y-5" style={{ fontSize: '16px' }}>
          <div>
            <div className="font-bold text-text-primary mb-1">Severity — ◆◆◆◆◇</div>
            <p className="text-text-secondary leading-relaxed">
              Every incident is rated from <strong className="text-text-primary">1 to 5</strong> for how
              serious it is. A filled diamond (◆) counts toward the rating, an empty one (◇) does not —
              so ◆◆◆◇◇ is a 3 out of 5. One diamond is a minor curiosity; five is as grave as it gets.
            </p>
            <p className="text-text-secondary leading-relaxed mt-2">
              Severity is what powers the <strong className="text-text-primary">Chaos Index</strong>. Each
              incident contributes its severity multiplied by its type — Dark Events ×3, Absurdities ×1,
              Good Vibes ×−1 — and the year&apos;s running total is scaled to a 0–100 score.
            </p>
          </div>
          <div>
            <div className="font-bold text-text-primary mb-1">Hype — ⚡⚡⚡</div>
            <p className="text-text-secondary leading-relaxed">
              Lightning bolts show how widely <strong className="text-text-primary">independent mainstream
              media outlets</strong> corroborated the incident — one ⚡ per source beyond the first
              (2 sources = ⚡, 3 = ⚡⚡, …). Forum chatter is never counted as a source.
            </p>
          </div>
        </div>
      </section>

      <hr className="border-border my-8" />

      {/* Legal Disclaimer — kept at the bottom of the page */}
      <section>
        <h2 className="font-display text-amber mb-4" style={{ fontSize: '18px' }}>
          Legal Disclaimer
        </h2>
        <div className="space-y-3 text-text-secondary leading-relaxed" style={{ fontSize: '10px' }}>
          <p>
            This is a <strong className="text-text-primary">satirical archive</strong>. All incidents
            are sourced from mainstream media, Reddit, and other publicly available sources.
            Content is presented with dry humour and does not intend to mock victims of serious incidents.
          </p>
          <p>
            No private individuals are named unless they appear in mainstream media reportage.
            No political content is published. Ever.
          </p>
          <p>
            The operator reviews every item before publication. If you believe any content is
            inaccurate, defamatory, or should be removed, please contact us. We will review
            within 48 hours.
          </p>
          <p>
            All source links open the original article. We claim no copyright over third-party content.
          </p>
          <p className="text-text-secondary/60">
            Yishun Again is a fan project and is not affiliated with the Yishun Town Council,
            the Singapore government, or any mainstream media outlet.
          </p>
        </div>
      </section>
    </article>
  )
}
