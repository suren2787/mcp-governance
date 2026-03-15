# MCP Governance PoC

## Goal

Prove to an HKMA bank's CIO/CISO that MCP servers can be governed safely by routing them through Kong Gateway — exactly like any other internal API.

**The "aha moment":** An AI agent (OpenWebUI + Ollama) tries to call `delete_file` on a real GitHub MCP server. Kong intercepts it and returns 403. The LLM never reaches GitHub.

**5 controls demonstrated live:**

| # | Control | What it proves |
|---|---------|----------------|
| 1 | JWT authentication | Only authenticated users/agents can call MCP tools |
| 2 | Tool-level ACL | Destructive tools blocked by name at the gateway |
| 3 | Rate limiting | 429 after 20 calls/min — prevents runaway AI loops |
| 4 | Audit logging | Every tool call logged with who + what (HKMA traceability) |
| 5 | Vault secrets | GitHub/Atlassian credentials never appear in config files |

---

## Stack

- **Kong 3.7** — DB-less mode, declarative config with built-in `env` vault
- **GitHub MCP** — cloud-hosted at `https://api.githubcopilot.com/mcp/` (no local container)
- **Atlassian MCP** — local sidecar (`sooperset/mcp-atlassian`) serving Jira + Confluence via API token
- **Ollama + llama3.2:3b** — local LLM, no API key required
- **OpenWebUI** — chat UI wired to Ollama
- **Secrets** — credentials in Kong container env vars (built-in `env` vault); no Vault container needed

---

## Progress

### Phase 1 — Verify the environment
- [x] 1. Confirm Docker Desktop engine is running — v29.1.3
- [x] 2. Confirm `docker compose` version — v5.0.0-desktop.1
- [x] 3. Confirm internet access to `ghcr.io` — github-mcp-server pulled successfully

### Phase 2 — Build the files (GitHub only)
- [x] 4. `docker-compose.yml` — services with correct healthchecks
- [x] 5. `kong/kong.yml` — 5 controls on `/github/mcp` route
- [x] 6. `.env.example` — document required vars
- [x] 7. `poc/README.md` — setup + 5 labeled curl tests

### Phase 3 — Verify each service independently
- [x] 9.  Start `vault` only → confirmed healthy
- [x] 10. Start `github-mcp` only → confirmed `/mcp` responds 401 — **now cloud-hosted, no local container needed**
- [x] 11. Start `ollama` only → confirmed `ollama ps` healthcheck passes

### Phase 4 — Full stack
- [x] 12. `docker compose up -d` — all services (kong, atlassian-mcp, ollama, openwebui)
- [x] 13. Kong starts healthy, loads `kong.yml` cleanly

### Phase 5 — Validate the 5 controls with curl
- [x] 16. Control 1 — **401** without JWT ✓
- [x] 17. Control 2 — **403** on `delete_file` ✓
- [x] 18. Control 3 — **429** on 21st request (rate-limit 20/min) ✓
- [x] 19. Control 4 — JSON audit log entry in Kong stdout: `tool_name`, `mcp_method`, `mcp_server` ✓
- [x] 20. Control 5 — No raw token in `kong.yml`; `{vault://env/github_token}` reference present ✓

### Phase 6 — OpenWebUI demo
- [x] 21. Open `http://localhost:3000`
- [x] 22. Paste `tool_mcp.py` into OpenWebUI: Workspace → Tools → (+) New Tool
- [x] 23. Ask LLM: "List repos for suren2787" → real results via `search_repositories` ✓
- [x] 24. Ask LLM: "Delete README.md from suren2787/mcp-governance" → Kong returns **403** on `delete_file` — aha moment ✓

> **Design note — denylist vs allowlist:**  
> The current ACL is a **denylist** (named destructive tools are blocked; everything else passes).  
> For a production HKMA deployment, flip to an **allowlist** — unknown tools denied by default until a human approves them.  
> `tool_mcp.py` uses a single generic `call_mcp_tool` passthrough so it never needs updating when the MCP server adds new tools; the approval decision lives entirely in `kong.yml`.

### Phase 7 — Expansion: Add Atlassian MCP (local sidecar)
- [x] 25. Add `sooperset/mcp-atlassian` sidecar to `docker-compose.yml` (API token + email, no OAuth required)
- [x] 26. Add Kong service/route `/atlassian/mcp` → `http://atlassian-mcp:9000/mcp` with same 5 controls
- [x] 27. Add `ATLASSIAN_SITE`, `ATLASSIAN_EMAIL`, `ATLASSIAN_API_TOKEN` to Kong env and `.env`
- [x] 28. Validate Atlassian tools: `list_jira_issues`, `search_jira_issues`, `search_confluence` ✓
- [x] 29. Aha moment: `delete_jira_issue` → Kong **403** before reaching Atlassian ✓
