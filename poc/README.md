# MCP Governance PoC — GitHub + Atlassian MCP via Kong

## Setup (2 steps)

**1. Fill in your credentials**
```powershell
copy .env.example .env
# Edit .env — add GITHUB_TOKEN, ATLASSIAN_EMAIL, ATLASSIAN_API_TOKEN, ATLASSIAN_SITE
```

**2. Start the stack**
```bash
docker compose up -d
```

OpenWebUI is ready at **http://localhost:3000**

> On first run Ollama downloads `llama3.2:3b` (~2 GB) in the background.
> Check progress with `docker compose logs -f ollama`.

---

## Test the 5 governance controls

```bash
DEVELOPER_JWT="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJraWQiOiJkZXZlbG9wZXIta2V5Iiwic3ViIjoiZGV2ZWxvcGVyLTAxIiwiZXhwIjo0MTAyNDQ0ODAwfQ.V7VofRreBjAlbCycX-RDacZKTjnA1ILtJn4gyN0iw3E"
```

### Control 1 — Authentication
```bash
# 401 — no token
curl -i -X POST http://localhost:8000/github/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# 200 — valid JWT accepted
curl -i -X POST http://localhost:8000/github/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $DEVELOPER_JWT" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

### Control 2 — Tool ACL (destructive tool blocked)
```bash
# 403 — delete_file is a real GitHub MCP tool that is blocked at the gateway
# (delete_repository, create_or_update_file, push_files are also blocked)
curl -i -X POST http://localhost:8000/github/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $DEVELOPER_JWT" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"delete_file","arguments":{"owner":"my-org","repo":"my-repo","path":"README.md","message":"rm","sha":"abc"}}}'
```

### Control 3 — Rate Limiting
```bash
# 429 on the 21st request within a minute
for i in $(seq 1 22); do
  echo -n "Request $i: "
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST http://localhost:8000/github/mcp \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "Authorization: Bearer $DEVELOPER_JWT" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
done
```

### Control 4 — Audit Logging
```bash
# Every call is logged — check for tool_name and consumer fields
docker logs kong 2>&1 | grep tool_name | tail -5
```

### Control 5 — Secrets Management
```bash
# No raw token in kong.yml — only a vault:// reference
grep vault kong/kong.yml
# Expected: {vault://env/GITHUB_TOKEN}

# Kong resolves the token at request time from its own env var.
# Confirm the token is present in the running container (length only — never print it):
docker exec kong sh -c 'echo ${#GITHUB_TOKEN} chars'
```

> Kong uses its built-in `env` vault — no Vault server container required.  
> In production, swap `{vault://env/GITHUB_TOKEN}` for `{vault://hcv/github/token}` to point at HashiCorp Vault or Azure Key Vault.

---

## Atlassian route (`/atlassian/mcp`)

The same 5 controls apply to Jira + Confluence via the `sooperset/mcp-atlassian` sidecar.

### Control 1 — Authentication
```bash
# 401 — no token
curl -i -X POST http://localhost:8000/atlassian/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}'
```

### Control 2 — Tool ACL (destructive Jira tool blocked)
```bash
# First initialize to get a session ID
SESSION=$(curl -si -X POST http://localhost:8000/atlassian/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $DEVELOPER_JWT" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}' \
  | grep -i mcp-session-id | awk '{print $2}' | tr -d '\r')

# 403 — jira_delete_issue is blocked at the gateway
curl -i -X POST http://localhost:8000/atlassian/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $DEVELOPER_JWT" \
  -H "Mcp-Session-Id: $SESSION" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"jira_delete_issue","arguments":{"issue_key":"TEST-1"}}}'
```

### Controls 3–5 — same as GitHub route above, substitute `/atlassian/mcp`.
