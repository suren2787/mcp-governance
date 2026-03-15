# MCP Governance — Reference Architecture

**Core premise:** MCP traffic is HTTP. Route every AI tool call through Kong Gateway — same as any internal API. The LLM never talks directly to backend systems.

---

## Topology

```
┌─────────────────────── frontend-net ────────────────────────────────────┐
│                                                                          │
│  ┌────────────┐  JWT + JSON-RPC  ┌──────────────────────────────────┐   │
│  │  AI Agent  │ ───────────────► │  Kong Gateway  (DB-less)         │   │
│  │ (OpenWebUI │                  │                                  │   │
│  │  + Ollama) │                  │  /github     → api.githubcopilot │   │
│  └────────────┘                  │  /atlassian  → atlassian-mcp:9000│   │
│                                  │  /[future]   → [sidecar]:XXXX   │   │
│  openwebui cannot reach          │                                  │   │
│  backend-net — enforced at       │  Per route: C1 C2 C3 C4 C5      │   │
│  network layer, not app code     └──────────────┬───────────────────┘   │
└─────────────────────────────────────────────────│───────────────────────┘
                                                  │ Kong spans both networks
┌─────────────────────── backend-net ─────────────│───────────────────────┐
│                                                  │                       │
│      ┌──────────────────────────┐    ┌──────────▼──────────────────┐    │
│      │  Atlassian MCP sidecar   │    │  [Future MCP] sidecar       │    │
│      │  :9000                   │    │  e.g. ServiceNow            │    │
│      │  Jira + Confluence       │    │  Add one block in kong.yml  │    │
│      └────────┬─────────────────┘    └─────────────────────────────┘    │
└───────────────────────│──────────────────────────────────────────────────┘
                        ▼
               Atlassian Cloud / on-prem
```

---

## The 5 Controls

Every MCP route in `kong.yml` carries the same 5-plugin block.

| # | Control | HTTP response | PoC | Production |
|---|---------|--------------|-----|-----------|
| C1 | **JWT auth** — reject unauthenticated requests | 401 | HS256, static consumer | RS256 from IdP (Azure AD / Okta); OIDC consumer mapping |
| C2 | **Tool ACL** — block destructive tool names at the gateway | 403 | Lua denylist in `pre-function` | Flip to allowlist; new tools denied by default until PR-approved |
| C3 | **Rate limiting** — cap requests per consumer per minute | 429 | 20/min, in-memory | Redis-backed; tiered limits by agent role |
| C4 | **Audit log** — structured JSON per call with tool name + consumer | — | `file-log` → stdout | `http-log` / OpenTelemetry → SIEM; 7-year retention for HKMA |
| C5 | **Secret injection** — strip the consumer JWT; inject the real service credential so the LLM never sees it | — | Kong `env` vault: `{vault://env/GITHUB_TOKEN}` in `kong.yml`; token lives only in Kong's container env | Replace env var with HashiCorp Vault / AWS Secrets Manager / Azure Key Vault |

### C5 — How secret injection works (no Vault server required)

```
AI Agent                    Kong                        GitHub MCP
    │                          │                              │
    │  Authorization:          │                              │
    │  Bearer <consumer JWT>   │                              │
    │ ────────────────────────►│                              │
    │                          │  post-function plugin:       │
    │                          │  1. strip consumer JWT       │
    │                          │  2. resolve token from       │
    │                          │     {vault://env/GITHUB_TOKEN}│
    │                          │     (Kong's own env var)     │
    │                          │  3. set Authorization:       │
    │                          │     Bearer <real GitHub PAT> │
    │                          │ ────────────────────────────►│
```

- **GitHub route** — Kong replaces the consumer JWT with the real GitHub PAT before forwarding. The PAT is never in `kong.yml` — only `{vault://env/GITHUB_TOKEN}`, resolved from Kong's own env var at runtime.
- **Atlassian route** — Kong strips the JWT entirely. The sidecar authenticates to Atlassian itself using its own env vars (`JIRA_API_TOKEN`). Kong has nothing to inject.
- **Production** — replace the `env` vault reference with `{vault://hcv/github/token}` or `{vault://aws/github/token}`. No other config change needed.

---

### C2 — Denylist vs. Allowlist

The PoC uses a **denylist** — named destructive tools are blocked, everything else passes. For production, flip to an **allowlist**: unknown tools are denied by default and only appear after a human adds them to `kong.yml` via a reviewed PR. The PR becomes the approval record.

| PoC (denylist) | Production (allowlist) |
|----------------|------------------------|
| Block: `delete_file`, `jira_delete_issue`, … | Allow: only explicitly named tools |
| New MCP tools pass automatically | New MCP tools blocked until PR-approved |
| Good for demo | Required for production HKMA |

---

## Network Isolation

Two Docker networks enforce a hard boundary — not application code:

| Network | Members |
|---------|---------|
| `frontend-net` | `openwebui`, `kong`, `ollama` |
| `backend-net` | `kong`, `atlassian-mcp`, *(future sidecars)* |

Kong is the only container on both networks. `atlassian-mcp` is not a resolvable hostname on `frontend-net` — Docker's embedded DNS simply won't answer, so the packet never leaves the AI agent container. Even a modified `tool_mcp.py` calling `atlassian-mcp:9000` directly will time out.

MCP sidecars use `expose` (not `ports`) — they are also unreachable from the developer's laptop directly.

In Kubernetes, the equivalent is a `NetworkPolicy` object on the sidecar pods.
```

---

## Adding a New MCP Server

```
  Git repo (kong.yml)              Docker host                      New Backend
       │                               │                                  │
  1.   │  PR: add service/route        │                                  │
       │  + 5-plugin block             │                                  │
       │  + ALLOWED tool list          │                                  │
       ▼                               │                                  │
  Code review &                        │                                  │
  risk sign-off                        │                                  │
       │                               │                                  │
  2.   │  PR merged                    │                                  │
       │                               │                                  │
  3.   │──── CI: deck validate ───────►│                                  │
       │     (catches YAML errors      │                                  │
       │      before deploy)           │                                  │
       │                               │                                  │
  4.   │                               │  docker compose up -d            │
       │                               │  new-mcp-sidecar                 │
       │                               │  (backend-net only,  ───────────►│
       │                               │   expose only)       credentials │
       │                               │                      from vault  │
       │                               │                                  │
  5.   │                               │  kong reload                     │
       │                               │  (zero-downtime,                 │
       │                               │   picks up new                   │
       │                               │   service/route)                 │
       │                               │                                  │
  6.   │                         Kong: /new-route → new-mcp:PORT ✓        │
       │                         All 5 controls active immediately        │
       │                         New tools denied until added to ALLOWED  │
       │                               │                                  │
  7.   │                         Update tool_mcp.py:                      │
       │                         - add helper (_mcp or _atlassian_mcp     │
       │                           pattern depending on transport)        │
       │                         - add one method per tool in Tools class │
       │                         Paste into OpenWebUI:                    │
       │                         Settings → Tools → edit → Save           │
```

**What changes per server** (everything else is copy-paste):

| Item | Where | What to change |
|------|-------|----------------|
| Upstream URL | `kong.yml` service block | `http://servicenow-mcp:8080` |
| ALLOWED tool list | `kong.yml` pre-function | `{ get_incident=true, list_tickets=true }` |
| `mcp_server` log tag | `kong.yml` file-log plugin | `return 'servicenow'` |
| Sidecar credentials | `docker-compose.yml` + `.env` | New env vars for the sidecar |
| Helper function | `tool_mcp.py` | Add `_servicenow_mcp()` if the transport needs a session handshake (Streamable-HTTP), or reuse `_mcp()` if it uses plain SSE |
| Tool methods | `tool_mcp.py` `Tools` class | One method per tool you want the LLM to call |
| OpenWebUI | Settings → Tools → edit | Paste updated `tool_mcp.py` and save |

**Two MCP transport patterns in `tool_mcp.py`:**

| Pattern | When to use | Helper |
|---------|-------------|--------|
| Plain SSE (`_mcp()`) | GitHub-style — single POST, SSE response | Already in `tool_mcp.py` |
| Streamable-HTTP (`_atlassian_mcp()`) | Requires `initialize` first to get `Mcp-Session-Id`, then the real call | Copy `_atlassian_mcp()`, rename it |

Check which pattern the new sidecar uses with:
```powershell
# If initialize returns Mcp-Session-Id header → Streamable-HTTP
curl -s -D - -X POST http://localhost:8000/newserver/mcp \
  -H "Authorization: Bearer <JWT>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
```

---

## HKMA Compliance

| Requirement | Control | Evidence |
|-------------|---------|---------|
| SPM MA(G)-3: Access control for AI | C1 + C2 | 401/403 from Kong; no direct agent→backend path |
| SPM MA(G)-3: Audit trail | C4 | JSON per call: consumer, tool, server, timestamp |
| BCBS 239: Data lineage | C4 `mcp_server` tag | Every access tagged with source system |
| Least privilege | C2 allowlist | Unknown tools denied by default |
| Change management for AI capabilities | GitOps `kong.yml` | PR review = approval record |
| Third-party risk (cloud MCP) | C5 | Kong holds credentials; LLM never sees them |
| Runaway automation risk | C3 | 429 stops AI loops |

> Illustrative only — formal HKMA submission requires legal and compliance input.

---

## Threat Model

| Threat | Mitigation |
|--------|-----------|
| Prompt injection → `delete_file` | C2 blocks tool at Kong before it leaves the network |
| Stolen JWT used from outside | Short expiry + rate limit per consumer + geo anomaly alert |
| AI agent infinite loop | C3: 429; Kong circuit breaker on upstream |
| Credential leak via config file | C5: only `{vault://...}` references in `kong.yml` |
| New MCP tool added silently | Allowlist: unknown tools blocked by default |
| Container escape from sidecar | No host ports; isolated `backend-net`; minimal base image |

---

## PoC → Production

| PoC | Production |
|-----|-----------|
| `docker-compose.yml` | Kubernetes Helm chart / ECS task definitions |
| `kong.yml` in Git | decK + CI pipeline (`deck validate` before merge) |
| `file-log` → stdout | `http-log` / OpenTelemetry → SIEM |
| `env` vault | HashiCorp Vault / AWS Secrets Manager / Azure Key Vault |
| Single Kong container | Kong cluster (2+ nodes) + shared Redis |
| HS256 JWT | RS256 / JWKS from corporate IdP |
| Local Ollama | Azure OpenAI / internal LLM platform |

---

## Key Design Principles

1. **Policy in the gateway, not the app** — `tool_mcp.py` is a dumb shim. All allow/deny logic lives in `kong.yml`. Policy changes don't require redeploying the AI agent.
2. **Network enforces isolation, not code** — MCP sidecars are on `backend-net` only. No route from `openwebui` exists regardless of what the application does.
3. **One plugin block, every server** — copy the 5-plugin template, change the URL and tool list. Controls are not one-offs.
4. **No secrets in config files** — `{vault://env/GITHUB_TOKEN}` is the only thing in `kong.yml`. The file is safe to commit.
5. **GitOps as the approval gate** — adding a tool to the allowlist requires a PR. The PR is the audit record.
