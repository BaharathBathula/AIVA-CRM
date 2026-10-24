/** Shapes returned by /integration-hub (apps/api/app/api/routes/integration_hub.py). */

export type IntegrationStatus = "upgrade" | "off" | "not_connected" | "ready" | "connected" | "error" | "needs_reauth";

export type CredentialField = {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  help: string;
  placeholder: string;
};

export type IntegrationDef = {
  key: string;
  name: string;
  category: string;
  vendor: string;
  summary: string;
  capabilities: string[];
  auth: "oauth" | "credentials" | "webhook" | "api_key" | "depends";
  feature: string;
  personal: boolean;
  family: string | null;
  scopes: string[];
  credential_fields: CredentialField[];
  depends_on: string | null;
  docs_url: string;
  color: string;
  monogram: string;
  setup: string[];
};

export type CatalogItem = IntegrationDef & {
  enabled: boolean;
  switched_on: boolean;
  available: boolean;
  status: IntegrationStatus;
  account_label: string | null;
  people_connected: number;
  subscriptions: number;
};

export type Connection = {
  id: string;
  provider: string;
  status: "connected" | "error" | "needs_reauth";
  account_label: string | null;
  external_account_id: string | null;
  scopes: string[];
  config: Record<string, unknown>;
  secrets: Record<string, boolean>;
  user_id: string | null;
  user_name: string | null;
  webhook_url: string | null;
  last_sync_at: string | null;
  last_used_at: string | null;
  last_error: string | null;
  connected_at: string;
  updated_at: string;
};

export type IntegrationLog = {
  id: string;
  provider: string;
  provider_name: string;
  level: "info" | "success" | "warning" | "error";
  action: string;
  message: string;
  detail: Record<string, unknown>;
  user: string | null;
  at: string;
};

export type OAuthAppInfo = {
  family: string;
  label: string;
  configured: boolean;
  source: "server" | "workspace" | null;
  redirect_uri: string;
  console_url: string;
  workspace_client_id: string | null;
  options: Record<string, string>;
};

export type IntegrationDetail = {
  integration: IntegrationDef;
  enabled: boolean;
  switched_on: boolean;
  dependency_enabled: boolean;
  available: boolean;
  status: IntegrationStatus;
  can_manage: boolean;
  connection: Connection | null;
  people: Connection[];
  workspace_config: Record<string, unknown>;
  workspace_secrets: Record<string, boolean>;
  oauth: OAuthAppInfo | null;
  app_webhook_url: string | null;
  subscriptions: number;
  /** Provider-specific data from the provider module's detail() hook. */
  extra: Record<string, unknown> & { error?: string };
  logs: IntegrationLog[];
};

/** Props every provider panel receives on the integration's page. */
export type PanelProps = {
  detail: IntegrationDetail;
  /** Replace the page's data with a fresh detail response (most hub endpoints return one). */
  setDetail: (detail: IntegrationDetail) => void;
  reload: () => Promise<void>;
};
