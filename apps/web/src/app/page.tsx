import { ArrowRight, LockKeyhole, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { AuthLinkHandler } from "@/components/auth-link-handler";

export default function WorkspaceLanding() {
  return <>
    <AuthLinkHandler />
    <main className="landing">
      <header className="landing-nav"><Link href="/" className="landing-brand"><span>A</span><strong>AlphaDesk</strong></Link><span className="paper-only">PAPER ONLY</span></header>
      <section className="landing-hero"><p className="kicker">OPTIONS RESEARCH · HUMAN-CONFIRMED PAPER EXECUTION</p><h1>Connected Paper Workspace</h1><p>Develop and evaluate tenant-isolated trading workflows with your own encrypted Alpaca paper and AI-provider credentials.</p></section>
      <section className="choice-card connected-entry">
        <div className="choice-icon"><LockKeyhole/></div><span className="mode-label">CONNECTED PAPER · REAL DATA</span><h2>Invite-only access</h2><p>Sign in to your connected paper workspace. Market evidence comes from your configured providers, and every paper order requires explicit human confirmation.</p><ul><li>Real Alpaca paper account projections</li><li>Real-source options discovery</li><li>Deterministic risk and Guardian controls</li></ul><Link className="choice-action" href="/login">Sign in with invitation <ArrowRight/></Link>
      </section>
      <footer className="landing-safety"><ShieldCheck/><span><strong>No live-money trading.</strong> Connected orders route only to Alpaca paper endpoints and always require confirmation.</span></footer>
    </main>
  </>;
}
