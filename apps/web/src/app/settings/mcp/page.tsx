"use client";

import Link from "next/link";
import { Bot, KeyRound, Pencil, Search } from "lucide-react";

import { CopyField } from "@/components/copy-field";
import { SettingsShell } from "@/components/settings-shell";
import { SettingRow, SettingsBody, Toggle, useWorkspaceSettings } from "@/components/settings-kit";
import { Alert, Badge, Card, EmptyState, LoadingRows, PageHeader } from "@/components/ui";
import { dateTime } from "@/lib/format";
import { useApi } from "@/lib/use-api";

type McpSettings = { enabled: boolean; allow_write: boolean; disabled_tools: string[] };

type Overview = {
  settings: McpSettings;
  endpoint: string;
  tools: { name: string; description: string; write: boolean; calls_30_days: number; enabled: boolean }[];
  calls: { at: string; user: string | null; method: string; tool: string | null; status: string; error: string | null; duration_ms: number | null; client: string | null }[];
  can_edit: boolean;
  has_api_access: boolean;
};

const STATUS_TONE: Record<string, "success" | "warning" | "danger" | "neutral"> = { ok: "success", denied: "warning", error: "danger" };

export default function McpPage() {
  return (
    <SettingsShell feature="api_access">
      <Mcp />
    </SettingsShell>
  );
}

function Mcp() {
  const overview = useApi<Overview>("/mcp/overview");
  const state = useWorkspaceSettings<McpSettings>("mcp");
  const off = !state.canEdit;
  const data = overview.data;
  const endpoint = data?.endpoint ?? "";

  function toggleTool(name: string, on: boolean) {
    if (!state.draft) return;
    const disabled = new Set(state.draft.disabled_tools);
    if (on) disabled.delete(name);
    else disabled.add(name);
    state.set("disabled_tools", [...disabled]);
  }

  return (
    <>
      <PageHeader
        title="MCP"
        description="Let Claude and other AI assistants that speak the Model Context Protocol search your CRM, read records and pipeline, and (if you allow it) create leads, tasks and notes — always with the permissions of the API key they use."
      />
      {overview.error ? <Alert>{overview.error}</Alert> : null}

      <Card title="Connect an assistant" icon={<Bot size={17} />} className="admin-gap">
        <div className="ui-stack" style={{ gap: 14 }}>
          <ol className="mcp-steps">
            <li>
              Create an API key in <Link href="/settings/api-keys">API keys</Link>. A <strong>read</strong> key can only look things up; a <strong>read &amp; write</strong>{" "}
              key can also use the write tools you allow below.
            </li>
            <li>Add AIVA as a remote MCP server (Streamable HTTP) in your assistant, with the key as a Bearer token.</li>
          </ol>
          {endpoint ? (
            <>
              <CopyField label="Copy the server URL" value={endpoint} />
              <div>
                <div className="ui-muted ui-small" style={{ marginBottom: 6 }}>
                  Claude Code
                </div>
                <CopyField multiline label="Copy the Claude Code command" value={`claude mcp add --transport http aiva ${endpoint} \\\n  --header "Authorization: Bearer YOUR_AIVA_API_KEY"`} />
              </div>
              <div>
                <div className="ui-muted ui-small" style={{ marginBottom: 6 }}>
                  Any client that reads an mcpServers config (for example a project&apos;s .mcp.json)
                </div>
                <CopyField
                  multiline
                  label="Copy the server config"
                  value={JSON.stringify({ mcpServers: { aiva: { type: "http", url: endpoint, headers: { Authorization: "Bearer YOUR_AIVA_API_KEY" } } } }, null, 2)}
                />
              </div>
            </>
          ) : (
            <LoadingRows rows={3} />
          )}
          <p className="ui-muted ui-small" style={{ margin: 0 }}>
            <KeyRound size={12} style={{ verticalAlign: -1 }} /> Treat the key like a password. Delete it in API keys to disconnect that assistant immediately.
          </p>
        </div>
      </Card>

      <SettingsBody state={state}>
        {(draft) => (
          <div className="ui-stack">
            <Card title="Access">
              <div className="settings-rows">
                <SettingRow label="Allow MCP connections" hint="When off, every assistant is refused, even with a valid API key.">
                  <Toggle label="Allow MCP connections" checked={draft.enabled} disabled={off} onChange={(value) => state.set("enabled", value)} />
                </SettingRow>
                <SettingRow label="Allow write tools" hint="Create leads and tasks, log activity and update deals. Also needs a read & write API key.">
                  <Toggle label="Allow write tools" checked={draft.allow_write} disabled={off || !draft.enabled} onChange={(value) => state.set("allow_write", value)} />
                </SettingRow>
              </div>
            </Card>

            <Card title="Tools" flush>
              {data ? (
                <div className="ui-table-wrap">
                  <table className="ui-table">
                    <thead>
                      <tr>
                        <th>Tool</th>
                        <th>Type</th>
                        <th className="ui-num">Calls (30 days)</th>
                        <th style={{ textAlign: "right" }}>On</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.tools.map((tool) => {
                        const on = !draft.disabled_tools.includes(tool.name);
                        const blocked = tool.write && !draft.allow_write;
                        return (
                          <tr key={tool.name}>
                            <td style={{ maxWidth: 460 }}>
                              <div className="ui-mono cell-title">{tool.name}</div>
                              <div className="ui-muted ui-small">{tool.description}</div>
                            </td>
                            <td>
                              {tool.write ? (
                                <Badge tone="warning">
                                  <Pencil size={11} /> Write
                                </Badge>
                              ) : (
                                <Badge>
                                  <Search size={11} /> Read
                                </Badge>
                              )}
                            </td>
                            <td className="ui-num">{tool.calls_30_days}</td>
                            <td style={{ textAlign: "right" }} title={blocked ? "Turn on write tools above to use this" : undefined}>
                              <Toggle label={`${tool.name} on`} checked={on && !blocked} disabled={off || blocked} onChange={(value) => toggleTool(tool.name, value)} />
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <LoadingRows />
              )}
            </Card>

            <Card title="Recent activity" flush>
              {data?.calls.length ? (
                <div className="ui-table-wrap">
                  <table className="ui-table">
                    <thead>
                      <tr>
                        <th>When</th>
                        <th>Key owner</th>
                        <th>Assistant</th>
                        <th>Request</th>
                        <th>Result</th>
                        <th className="ui-num">Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.calls.map((call, index) => (
                        <tr key={`${call.at}-${index}`}>
                          <td className="ui-nowrap ui-small">{dateTime(call.at)}</td>
                          <td>{call.user ?? "—"}</td>
                          <td className="ui-small">{call.client ?? "—"}</td>
                          <td className="ui-mono">{call.tool ?? call.method}</td>
                          <td>
                            <Badge tone={STATUS_TONE[call.status] ?? "neutral"}>{call.status}</Badge>
                            {call.error ? <div className="ui-muted ui-small ui-truncate" style={{ maxWidth: 260 }}>{call.error}</div> : null}
                          </td>
                          <td className="ui-num ui-small">{call.duration_ms !== null ? `${call.duration_ms} ms` : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState icon={<Bot size={20} />} title="No assistant has connected yet" message="Calls appear here as soon as an assistant uses AIVA." />
              )}
            </Card>
          </div>
        )}
      </SettingsBody>
    </>
  );
}
