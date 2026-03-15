"""
GitHub + Atlassian MCP Tools for OpenWebUI
============================================
Paste the entire contents of this file into OpenWebUI:
  Settings → Tools → (+) New Tool

The LLM calls these functions; every call is routed through Kong Gateway
which enforces all 5 governance controls before touching GitHub or Atlassian.

Kong endpoints (internal Docker network):
  GitHub:    http://kong:8000/github/mcp
  Atlassian: http://kong:8000/atlassian/mcp
JWT:  developer consumer key (HS256, expires 2099)
"""

import json
import urllib.request


KONG_GITHUB_URL    = "http://kong:8000/github/mcp"
KONG_ATLASSIAN_URL = "http://kong:8000/atlassian/mcp"
DEVELOPER_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJraWQiOiJkZXZlbG9wZXIta2V5Iiwic3ViIjoiZGV2ZWxvcGVyLTAxIiwiZXhwIjo0MTAyNDQ0ODAwfQ"
    ".V7VofRreBjAlbCycX-RDacZKTjnA1ILtJn4gyN0iw3E"
)


def _parse_sse(raw: bytes) -> dict:
    """Extract the first JSON-RPC payload from an SSE stream.

    GitHub's MCP endpoint always responds in Streamable-HTTP / SSE format:
        event: message\ndata: {"jsonrpc":...}\n\n
    We find the first 'data: ' line and parse the JSON from it.
    If the body looks like plain JSON (fallback), parse it directly.
    """
    text = raw.decode(errors="replace")
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    # Fallback: plain JSON response
    return json.loads(text)


def _mcp(url: str, method: str, params: dict | None = None) -> dict:
    """Send a JSON-RPC 2.0 request to an MCP server via Kong."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        payload["params"] = params

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEVELOPER_JWT}",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return _parse_sse(resp.read())


def _atlassian_mcp(method: str, params: dict | None = None) -> dict:
    """Send a JSON-RPC 2.0 request to the Atlassian MCP sidecar via Kong.

    The Streamable-HTTP transport requires a session: we initialize first,
    capture the Mcp-Session-Id header, then send the real request.
    """
    base_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEVELOPER_JWT}",
        "Accept": "application/json, text/event-stream",
    }

    # Step 1 — initialize, obtain session ID
    init_payload = json.dumps({
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "openwebui", "version": "1.0"},
        },
    }).encode()
    req0 = urllib.request.Request(
        KONG_ATLASSIAN_URL, data=init_payload, headers=base_headers, method="POST"
    )
    with urllib.request.urlopen(req0, timeout=15) as r0:
        session_id = r0.headers.get("Mcp-Session-Id")
        r0.read()  # consume response

    # Step 2 — real call, include session ID
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        payload["params"] = params
    headers = dict(base_headers)
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req1 = urllib.request.Request(
        KONG_ATLASSIAN_URL, data=json.dumps(payload).encode(),
        headers=headers, method="POST"
    )
    with urllib.request.urlopen(req1, timeout=15) as r1:
        return _parse_sse(r1.read())


class Tools:
    def list_github_repos(self, owner: str) -> str:
        """
        List GitHub repositories for a user or organisation.
        :param owner: GitHub username or organisation name (e.g. 'octocat')
        :return: JSON array of repository names
        """
        result = _mcp(KONG_GITHUB_URL, "tools/call", {
            "name": "search_repositories",
            "arguments": {"query": f"user:{owner}"},
        })
        return json.dumps(result.get("result", result), indent=2)

    def get_github_file(self, owner: str, repo: str, path: str) -> str:
        """
        Read a file from a GitHub repository.
        :param owner: Repository owner (user or org)
        :param repo: Repository name
        :param path: File path inside the repo (e.g. 'README.md')
        :return: File contents as a string
        """
        result = _mcp(KONG_GITHUB_URL, "tools/call", {
            "name": "get_file_contents",
            "arguments": {"owner": owner, "repo": repo, "path": path},
        })
        return json.dumps(result.get("result", result), indent=2)

    def search_github_repos(self, query: str) -> str:
        """
        Search GitHub repositories by keyword.
        :param query: Search query (e.g. 'kong gateway')
        :return: JSON list of matching repositories
        """
        result = _mcp(KONG_GITHUB_URL, "tools/call", {
            "name": "search_repositories",
            "arguments": {"query": query},
        })
        return json.dumps(result.get("result", result), indent=2)

    def list_github_issues(self, owner: str, repo: str) -> str:
        """
        List open issues for a GitHub repository.
        :param owner: Repository owner
        :param repo: Repository name
        :return: JSON array of issues
        """
        result = _mcp(KONG_GITHUB_URL, "tools/call", {
            "name": "list_issues",
            "arguments": {"owner": owner, "repo": repo, "state": "open"},
        })
        return json.dumps(result.get("result", result), indent=2)

    def delete_github_file(self, owner: str, repo: str, path: str) -> str:
        """
        Delete a file from a GitHub repository.
        NOTE: This will be BLOCKED by Kong (Control 2 — governance policy forbids write operations).
        :param owner: Repository owner
        :param repo: Repository name
        :param path: Path of the file to delete (e.g. 'README.md')
        :return: Error message from Kong gateway showing governance block
        """
        try:
            result = _mcp(KONG_GITHUB_URL, "tools/call", {
                "name": "delete_file",
                "arguments": {"owner": owner, "repo": repo, "path": path,
                               "message": "delete file", "sha": "dummy"},
            })
            return json.dumps(result, indent=2)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"BLOCKED by Kong Gateway — HTTP {e.code}: {body}"

    # ------------------------------------------------------------------
    # Atlassian (Jira + Confluence) methods — routed via Kong /atlassian
    # ------------------------------------------------------------------

    def list_jira_issues(self, project_key: str) -> str:
        """
        List open Jira issues for a project.
        :param project_key: Jira project key (e.g. 'BANK', 'OPS')
        :return: JSON array of Jira issues
        """
        result = _atlassian_mcp("tools/call", {
            "name": "jira_get_project_issues",
            "arguments": {"project_key": project_key},
        })
        return json.dumps(result.get("result", result), indent=2)

    def search_jira_issues(self, jql: str) -> str:
        """
        Search Jira issues using JQL (Jira Query Language).
        :param jql: JQL query string (e.g. 'project=BANK AND status=Open')
        :return: JSON array of matching Jira issues
        """
        result = _atlassian_mcp("tools/call", {
            "name": "jira_search",
            "arguments": {"jql": jql},
        })
        return json.dumps(result.get("result", result), indent=2)

    def get_confluence_page(self, page_id: str) -> str:
        """
        Get a Confluence page by its ID.
        :param page_id: Numeric Confluence page ID
        :return: Page title and body content
        """
        result = _atlassian_mcp("tools/call", {
            "name": "confluence_get_page",
            "arguments": {"page_id": page_id},
        })
        return json.dumps(result.get("result", result), indent=2)

    def search_confluence(self, query: str) -> str:
        """
        Search Confluence pages and spaces by keyword.
        :param query: CQL search query (e.g. 'API governance')
        :return: JSON array of matching Confluence pages
        """
        result = _atlassian_mcp("tools/call", {
            "name": "confluence_search",
            "arguments": {"query": query},
        })
        return json.dumps(result.get("result", result), indent=2)

    def delete_jira_issue(self, issue_key: str) -> str:
        """
        Delete a Jira issue.
        NOTE: This will be BLOCKED by Kong (Control 2 — governance policy forbids destructive operations).
        :param issue_key: Jira issue key (e.g. 'BANK-42')
        :return: Error message from Kong gateway showing governance block
        """
        try:
            result = _atlassian_mcp("tools/call", {
                "name": "jira_delete_issue",
                "arguments": {"issue_key": issue_key},
            })
            return json.dumps(result, indent=2)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"BLOCKED by Kong Gateway — HTTP {e.code}: {body}"
