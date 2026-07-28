# run_demo.ps1 - start TEN websocket voice assistant container + verify stack
$ErrorActionPreference = "Stop"
$root = "D:\Documents\Projects\Chaonao\project\voice-agent-comparison\ten-demo"
$aiAgents = "$root\ten-framework\ai_agents"

docker rm -f ten-demo 2>$null | Out-Null

docker run -d --name ten-demo `
  --env-file "$aiAgents\.env" `
  -p 8080:8080 -p 3000:3000 -p 8765:8765 `
  ten-websocket-voice-assistant

Write-Host "container started, waiting for api health..."
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep 2
  try {
    $r = Invoke-RestMethod -Uri "http://localhost:8080/health" -TimeoutSec 3
    if ($r) { $ok = $true; break }
  } catch {}
}
if (-not $ok) { Write-Host "API server did not become healthy"; docker logs ten-demo --tail 50; exit 1 }
Write-Host "api healthy: $((Invoke-RestMethod http://localhost:8080/health) | ConvertTo-Json -Compress)"

Write-Host "starting agent worker (graph: voice_assistant)..."
$body = @{
  request_id   = [guid]::NewGuid().ToString()
  channel_name = "test"
  user_uid     = 12345
  graph_name   = "voice_assistant"
  timeout      = -1
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8080/start" -Method Post -ContentType "application/json" -Body $body | ConvertTo-Json -Compress

Write-Host "waiting for websocket server on 8765..."
$wsOk = $false
for ($i = 0; $i -lt 90; $i++) {
  Start-Sleep 2
  $c = Test-NetConnection -ComputerName localhost -Port 8765 -WarningAction SilentlyContinue
  if ($c.TcpTestSucceeded) { $wsOk = $true; break }
}
if ($wsOk) { Write-Host "websocket server is listening on 8765" } else { Write-Host "websocket server NOT listening (check docker logs ten-demo)" }
