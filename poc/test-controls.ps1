$J = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJraWQiOiJkZXZlbG9wZXIta2V5Iiwic3ViIjoiZGV2ZWxvcGVyLTAxIiwiZXhwIjo0MTAyNDQ0ODAwfQ.V7VofRreBjAlbCycX-RDacZKTjnA1ILtJn4gyN0iw3E"
$base = "http://localhost:8000/github/mcp"
$ct   = "application/json"
$auth = "Bearer $J"

function Req($body) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $base -Method POST `
            -Headers @{ Authorization=$auth; "Content-Type"=$ct } `
            -Body $body
        $r.StatusCode
    } catch {
        $_.Exception.Response.StatusCode.value__
    }
}

# C1 – no JWT → 401
$c1 = try {
    (Invoke-WebRequest -UseBasicParsing -Uri $base -Method POST `
        -Headers @{ "Content-Type"=$ct } `
        -Body '{"jsonrpc":"2.0","id":1,"method":"tools/list"}').StatusCode
} catch {
    $_.Exception.Response.StatusCode.value__
}
Write-Host "C1 (no JWT, expect 401):            $c1"

# C2 – blocked tool → 403  (delete_file is a real GitHub MCP tool, blocked by governance ACL)
$c2 = Req '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"delete_file","arguments":{"owner":"x","repo":"y","path":"README.md","message":"rm","sha":"abc"}}}'
Write-Host "C2 (delete_file, expect 403):       $c2"

# C3 – rate limit – send 7 requests, last should be 429
Write-Host "C3 (rate-limit 5/min):"
for ($i = 1; $i -le 7; $i++) {
    $code = Req '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
    Write-Host "  req $i => $code"
}

# C4 – audit log entry check (last 3 lines of kong/logs/mcp-audit.log)
Write-Host "C4 (audit log tail):"
docker logs kong 2>&1 | Select-String "tool_name" | Select-Object -Last 3 | ForEach-Object { Write-Host "  $_" }

# C5 – no raw token in config
Write-Host "C5 (no raw token in kong.yml):"
$hasPAT = Select-String -Path ".\kong\kong.yml" -Pattern "ghp_|github_pat_" -Quiet
Write-Host "  Raw PAT found: $hasPAT  (expect False)"
$hasVault = Select-String -Path ".\kong\kong.yml" -Pattern "vault://env" -Quiet
Write-Host "  Vault ref found: $hasVault  (expect True)"
