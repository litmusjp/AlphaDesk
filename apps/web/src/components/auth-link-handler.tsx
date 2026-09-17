"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";

import { supabaseBrowser } from "@/lib/api";

type LinkState = "invite" | "recovery" | "error" | "complete" | null;

export function AuthLinkHandler() {
  const [state, setState] = useState<LinkState>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    const fail = (text: string) => {
      queueMicrotask(() => {
        if (!active) return;
        setState("error");
        setMessage(text);
      });
    };
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const query = new URLSearchParams(window.location.search);
    const errorCode = hash.get("error_code") ?? query.get("error_code");
    const accessToken = hash.get("access_token");
    const refreshToken = hash.get("refresh_token");
    const authorizationCode = query.get("code");
    const linkType = hash.get("type") ?? query.get("type");

    if (errorCode || hash.get("error") || query.get("error")) {
      fail("This Supabase link is invalid or expired. Request a new invitation or recovery email.");
      return () => { active = false; };
    }
    if (!accessToken && !authorizationCode && linkType !== "invite" && linkType !== "recovery") return;

    const client = supabaseBrowser();
    if (!client) {
      fail("Supabase is not configured for this deployment.");
      return () => { active = false; };
    }

    (async () => {
      const result = accessToken && refreshToken
        ? await client.auth.setSession({ access_token: accessToken, refresh_token: refreshToken })
        : authorizationCode
          ? await client.auth.exchangeCodeForSession(authorizationCode)
          : await client.auth.getSession();
      if (!active) return;
      if (result.error || !result.data.session) {
        setState("error");
        setMessage("The authentication link could not be accepted. Request a new link and try again.");
        return;
      }
      setEmail(result.data.session.user.email ?? "");
      setState(linkType === "recovery" ? "recovery" : "invite");
      window.history.replaceState({}, document.title, `${window.location.pathname}${window.location.search}`);
    })().catch(() => {
      if (active) {
        setState("error");
        setMessage("The authentication link could not be accepted. Request a new link and try again.");
      }
    });

    return () => { active = false; };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password.length < 8) {
      setMessage("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirmation) {
      setMessage("Passwords do not match.");
      return;
    }
    const client = supabaseBrowser();
    if (!client) {
      setMessage("Supabase is not configured for this deployment.");
      return;
    }
    setBusy(true);
    setMessage("");
    const { error } = await client.auth.updateUser({ password });
    setBusy(false);
    if (error) {
      setMessage("Password setup failed. Request a new recovery link and try again.");
      return;
    }
    setPassword("");
    setConfirmation("");
    setState("complete");
    setMessage("Password set. You can now sign in to AlphaDesk.");
  }

  if (!state) return null;
  if (state === "error" || state === "complete") {
    return <section className="auth-card auth-link-card" aria-live="polite"><span className="mode-label">CONNECTED PAPER · SUPABASE AUTHENTICATION</span><h2>{state === "complete" ? "Account ready" : "Authentication link problem"}</h2><p className="form-message">{message}</p><Link className="choice-action" href="/login">Continue to sign in</Link></section>;
  }
  return <section className="auth-card auth-link-card"><span className="mode-label">CONNECTED PAPER · SUPABASE AUTHENTICATION</span><h2>Finish setting up your account</h2><p>Set a password for {email || "your AlphaDesk account"} before signing in.</p><form onSubmit={submit}><label>Password<input type="password" required minLength={8} maxLength={128} autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} /></label><label>Confirm password<input type="password" required minLength={8} maxLength={128} autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label><button disabled={busy}>{busy ? "Saving…" : "Set password"}</button></form>{message ? <p className="form-message" role="status">{message}</p> : null}</section>;
}
