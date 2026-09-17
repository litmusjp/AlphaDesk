"use client";

import { FormEvent, useEffect, useState } from "react";
import { Bot, CheckCircle2, KeyRound, LockKeyhole, Trash2 } from "lucide-react";

import { deskFetch } from "@/lib/api";

type Provider = "alpaca" | "openrouter" | "anthropic";
type Status = {
  provider: string;
  configured: boolean;
  enabled: boolean;
  validation_status: string;
  fingerprint: string | null;
  configuration: Record<string, unknown>;
  validated_at: string | null;
};

export function CredentialSettings() {
  const [statuses, setStatuses] = useState<Status[]>([]);
  const [alpacaKey, setAlpacaKey] = useState("");
  const [alpacaSecret, setAlpacaSecret] = useState("");
  const [openRouterKey, setOpenRouterKey] = useState("");
  const [openRouterModel, setOpenRouterModel] = useState("openai/gpt-4.1-mini");
  const [anthropicKey, setAnthropicKey] = useState("");
  const [anthropicModel, setAnthropicModel] = useState("claude-sonnet-4-5-20250929");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    setStatuses(await deskFetch<Status[]>("/desk/credentials"));
  }

  useEffect(() => {
    void deskFetch<Status[]>("/desk/credentials")
      .then(setStatuses)
      .catch((error: Error) => setMessage(error.message));
  }, []);

  async function submit(event: FormEvent, provider: Provider, save: boolean) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const payload = provider === "alpaca"
        ? { api_key_id: alpacaKey, secret_key: alpacaSecret }
        : provider === "openrouter"
          ? { api_key: openRouterKey, model: openRouterModel }
          : { api_key: anthropicKey, model: anthropicModel };
      const endpoint = `/desk/credentials/${provider}${save ? "" : "/test"}`;
      await deskFetch(endpoint, { method: save ? "PUT" : "POST", body: JSON.stringify(payload) });
      if (save) {
        if (provider === "alpaca") {
          setAlpacaKey("");
          setAlpacaSecret("");
        } else if (provider === "openrouter") {
          setOpenRouterKey("");
        } else {
          setAnthropicKey("");
        }
        await refresh();
      }
      const label = provider === "alpaca" ? "Alpaca paper" : provider === "openrouter" ? "OpenRouter" : "Anthropic";
      setMessage(`${label} ${save ? "verified and encrypted" : "validation passed"}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Validation failed");
    } finally {
      setBusy(false);
    }
  }

  async function remove(provider: Provider) {
    setBusy(true);
    setMessage("");
    try {
      await deskFetch(`/desk/credentials/${provider}`, { method: "DELETE" });
      await refresh();
      setMessage("Credential removed and dependent access disabled.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  const alpaca = statuses.find((status) => status.provider === "ALPACA");
  const openRouter = statuses.find((status) => status.provider === "OPENROUTER");
  const anthropic = statuses.find((status) => status.provider === "ANTHROPIC");

  return (
    <>
      <div className="mode-banner blue">
        <LockKeyhole />
        <div>
          <strong>Credentials are write-only.</strong>
          <span>Secrets are encrypted with tenant-bound AES-256-GCM and are never returned to this browser after saving.</span>
        </div>
      </div>
      <p className="form-message">{message}</p>
      <div className="settings-grid">
        <form className="credential-card" onSubmit={(event) => void submit(event, "alpaca", true)}>
          <div className="provider-title"><KeyRound /><div><h2>Alpaca Markets</h2><p>Paper endpoint only</p></div><StatusPill status={alpaca} /></div>
          <StoredStatus status={alpaca} />
          <label>Paper API key ID<input required minLength={8} autoComplete="off" value={alpacaKey} onChange={(event) => setAlpacaKey(event.target.value)} /></label>
          <label>Paper API secret<input required minLength={8} type="password" autoComplete="new-password" value={alpacaSecret} onChange={(event) => setAlpacaSecret(event.target.value)} /></label>
          <div className="button-row">
            <button type="button" className="secondary-button" disabled={busy || !alpacaKey || !alpacaSecret} onClick={(event) => void submit(event as unknown as FormEvent, "alpaca", false)}>Test paper connection</button>
            <button disabled={busy}>Test &amp; save encrypted</button>
            {alpaca?.configured ? <button type="button" className="icon-button" aria-label="Delete Alpaca credentials" onClick={() => void remove("alpaca")}><Trash2 /></button> : null}
          </div>
        </form>

        <AICredentialCard
          provider="openrouter"
          title="OpenRouter"
          description="Read-only AI analysis"
          icon={<Bot />}
          status={openRouter}
          apiKey={openRouterKey}
          model={openRouterModel}
          modelLabel="OpenRouter model"
          onApiKeyChange={setOpenRouterKey}
          onModelChange={setOpenRouterModel}
          onSubmit={(event, save) => void submit(event, "openrouter", save)}
          onRemove={() => void remove("openrouter")}
          busy={busy}
        />

        <AICredentialCard
          provider="anthropic"
          title="Anthropic"
          description="Read-only AI analysis"
          icon={<Bot />}
          status={anthropic}
          apiKey={anthropicKey}
          model={anthropicModel}
          modelLabel="Anthropic model"
          onApiKeyChange={setAnthropicKey}
          onModelChange={setAnthropicModel}
          onSubmit={(event, save) => void submit(event, "anthropic", save)}
          onRemove={() => void remove("anthropic")}
          busy={busy}
        />
      </div>
    </>
  );
}

function AICredentialCard({
  title,
  description,
  icon,
  status,
  apiKey,
  model,
  modelLabel,
  onApiKeyChange,
  onModelChange,
  onSubmit,
  onRemove,
  busy,
}: {
  provider: Provider;
  title: string;
  description: string;
  icon: React.ReactNode;
  status: Status | undefined;
  apiKey: string;
  model: string;
  modelLabel: string;
  onApiKeyChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onSubmit: (event: FormEvent, save: boolean) => void;
  onRemove: () => void;
  busy: boolean;
}) {
  const active = Boolean(status?.configuration.active);
  return (
    <form className="credential-card" onSubmit={(event) => onSubmit(event, true)}>
      <div className="provider-title"><span>{icon}</span><div><h2>{title}</h2><p>{description}</p></div><StatusPill status={status} /></div>
      <StoredStatus status={status} />
      {active ? <p className="form-message">Active provider for AI analysis.</p> : null}
      <label>API key<input required minLength={8} type="password" autoComplete="new-password" value={apiKey} onChange={(event) => onApiKeyChange(event.target.value)} /></label>
      <label>{modelLabel}<input required minLength={2} maxLength={160} value={model} onChange={(event) => onModelChange(event.target.value)} /></label>
      <div className="button-row">
        <button type="button" className="secondary-button" disabled={busy || !apiKey || !model} onClick={(event) => onSubmit(event as unknown as FormEvent, false)}>Test provider</button>
        <button disabled={busy}>Test &amp; save encrypted</button>
        {status?.configured ? <button type="button" className="icon-button" aria-label={`Delete ${title} credentials`} onClick={onRemove}><Trash2 /></button> : null}
      </div>
    </form>
  );
}

function StoredStatus({ status }: { status: Status | undefined }) {
  return status?.configured ? <p className="masked-record"><CheckCircle2 /> Stored fingerprint: <code>{status.fingerprint}</code> · {String(status.configuration.model ?? status.validation_status)}</p> : null;
}

function StatusPill({ status }: { status: Status | undefined }) {
  return <span className={status?.enabled ? "status-pill good" : "status-pill"}>{status?.enabled ? "VERIFIED" : "NOT CONNECTED"}</span>;
}
