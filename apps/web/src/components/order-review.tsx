"use client";

import { AlertTriangle, CheckCircle2, Clock3, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { deskFetch } from "@/lib/api";

type Leg = { side: string; ratio: number; contract?: { symbol?: string; strike?: string; option_type?: string } };
type Structure = { structure_type?: string; max_loss?: string; max_profit?: string; break_evens?: string[]; legs?: Leg[] };
type Candidate = { structure?: Structure };
type RiskCheck = { name: string; passed: boolean };
type RiskDecision = { checks?: RiskCheck[] };
type OrderIntent = { client_order_id: string; quantity: number; limit_price: string };
type OptionDiagnostics = { total_contracts: number; requested_type_contracts: number; strict_eligible_contracts: number; selected_contracts: number; rejection_counts: Record<string, number> };
type Analysis = { opportunity_id: string; symbol: string; disposition: string; source: string; observed_at: string; expires_at: string; signal: Record<string, unknown>; candidate: Candidate | null; risk_decision: RiskDecision | null; order_intent: OrderIntent | null; option_diagnostics: OptionDiagnostics | null; reason_codes: string[] };
type ConditionalApproval = { approval_id: string; opportunity_id: string; state: string; session_date: string; approved_at: string; expires_at: string; max_limit_price: string; max_loss: string; max_quantity: number; max_quote_age_seconds: number; failure_reason: string | null };

export function OrderReview({ id }: { id: string }) {
  const [opportunity, setOpportunity] = useState<Analysis | null>(null);
  const [approval, setApproval] = useState<ConditionalApproval | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const [nextOpportunity, approvals] = await Promise.all([
      deskFetch<Analysis>(`/desk/opportunities/${id}`),
      deskFetch<ConditionalApproval[]>("/desk/approvals"),
    ]);
    setOpportunity(nextOpportunity);
    setApproval(approvals.find((item) => item.opportunity_id === id) ?? null);
  }, [id]);

  useEffect(() => {
    const kickoff = setTimeout(() => {
      void refresh().catch((error: Error) => setMessage(error.message));
    }, 0);
    return () => clearTimeout(kickoff);
  }, [refresh]);

  if (!opportunity) return <section className="loading-panel">{message || "Loading immutable order review…"}</section>;

  const structure = opportunity.candidate?.structure;
  const intent = opportunity.order_intent;
  const checks = opportunity.risk_decision?.checks ?? [];
  const canApprove = opportunity.source === "ALPACA_REAL" && opportunity.disposition === "TRADE" && Boolean(intent) && Boolean(structure);
  const canRenewApproval = !approval || ["EXPIRED", "CONDITION_FAILED", "REJECTED"].includes(approval.state);
  const approvalCanReject = approval?.state === "APPROVED_FOR_SESSION";

  async function approveForSession() {
    if (!intent || !structure) return;
    setBusy(true);
    setMessage("");
    try {
      const nextApproval = await deskFetch<ConditionalApproval>(`/desk/opportunities/${id}/approve-session`, {
        method: "POST",
        body: JSON.stringify({
          max_limit_price: intent.limit_price,
          max_loss: structure.max_loss,
          max_quantity: intent.quantity,
          max_quote_age_seconds: 30,
        }),
      });
      setApproval(nextApproval);
      setMessage(`Approved for the next U.S. session (${nextApproval.session_date}). The worker will revalidate before submitting.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Conditional approval failed");
    } finally {
      setBusy(false);
    }
  }

  async function rejectApproval() {
    if (!approval) return;
    setBusy(true);
    try {
      await deskFetch(`/desk/approvals/${approval.approval_id}/reject`, { method: "POST" });
      await refresh();
      setMessage("Conditional approval rejected.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Approval rejection failed");
    } finally {
      setBusy(false);
    }
  }

  return <>
    <div className={`mode-banner ${canApprove ? "blue" : "danger"}`}>
      {canApprove ? <ShieldCheck /> : <AlertTriangle />}
      <div><strong>{canApprove ? "Real-source intent ready for conditional approval" : "This opportunity cannot be approved"}</strong><span>{canApprove ? "Approve for the next U.S. session; the worker revalidates every gate before paper submission." : opportunity.reason_codes.join(", ") || "No approved immutable intent is present."}</span></div>
    </div>
    <div className="review-grid">
      <section className="control-panel">
        <div className="panel-title"><div><small>IMMUTABLE ORDER REVIEW</small><h2>{opportunity.symbol} · {String(structure?.structure_type ?? "No structure").replaceAll("_", " ")}</h2></div><span className="status-pill good">{opportunity.source}</span></div>
        <div className="review-stats"><div><small>Quantity</small><strong>{intent?.quantity ?? "—"}</strong></div><div><small>Limit</small><strong>${intent?.limit_price ?? "—"}</strong></div><div><small>Maximum loss</small><strong>${structure?.max_loss ?? "—"}</strong></div><div><small>Maximum profit</small><strong>${structure?.max_profit ?? "—"}</strong></div><div><small>Break-even</small><strong>{structure?.break_evens?.join(", ") ?? "—"}</strong></div><div><small>Quote expiry</small><strong>{new Date(opportunity.expires_at).toLocaleTimeString()}</strong></div></div>
        <div className="review-stats"><div><small>Signal score</small><strong>{String(opportunity.signal.score ?? "—")} / 100</strong></div><div><small>Disposition</small><strong>{opportunity.disposition.replaceAll("_", " ")}</strong></div></div>
        <h3>Decision reasons</h3><p>{opportunity.reason_codes.join(", ") || "All deterministic gates passed."}</p>
        {opportunity.option_diagnostics ? <><h3>Option-chain diagnostics</h3><p>{opportunity.option_diagnostics.selected_contracts} reviewable of {opportunity.option_diagnostics.requested_type_contracts} requested-side contracts; {opportunity.option_diagnostics.strict_eligible_contracts} pass current-session execution filters.</p><p>{Object.entries(opportunity.option_diagnostics.rejection_counts).map(([reason, count]) => `${reason.replaceAll("_", " ")}: ${count}`).join(" · ") || "No execution-filter rejections."}</p></> : null}
        <h3>Strategy legs</h3><div className="leg-list">{(structure?.legs ?? []).map((leg, index) => <div key={`${leg.contract?.symbol}-${index}`}><span>{leg.side} × {leg.ratio}</span><strong>{leg.contract?.symbol ?? `${leg.contract?.strike} ${leg.contract?.option_type}`}</strong></div>)}</div>
        <h3>Deterministic risk checks</h3><div className="check-grid">{checks.map((check) => <div key={check.name}><CheckCircle2 /><span>{check.name}</span><strong>{check.passed ? "PASS" : "FAIL"}</strong></div>)}</div>
      </section>
      <aside className="control-panel confirmation-panel">
        <Clock3 /><small>CONDITIONAL NEXT-SESSION APPROVAL</small>
        {approval ? <><strong>{approval.state.replaceAll("_", " ")}</strong><p>Session: {approval.session_date}<br />Maximum price: ${approval.max_limit_price}<br />Maximum loss: ${approval.max_loss}<br />Quote age: {approval.max_quote_age_seconds}s</p>{approval.failure_reason ? <p className="form-message">Reason: {approval.failure_reason}</p> : null}</> : <p>Approve once before sleep. At the next U.S. session open, the worker checks the live structure, quote, risk, and broker state before submitting.</p>}
        {approvalCanReject ? <button className="secondary-button" disabled={busy} onClick={() => void rejectApproval()}>Reject approval</button> : canRenewApproval ? <button disabled={!canApprove || busy} onClick={() => void approveForSession()}>Approve for next U.S. session</button> : null}
      </aside>
    </div>
    {message ? <p className="form-message">{message}</p> : null}
  </>;
}
