param(
    [int]$Port = 8794,
    [switch]$Demo,
    [switch]$Codex,
    [string]$DocsDir = ''
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Codex) {
    codex login status
    if ($LASTEXITCODE -ne 0) { throw '请先在本机登录 Codex CLI' }
    $env:DESK_AI_PROVIDER = 'codex'
}
if ($Demo) {
    $env:DESK_DEMO = '1'
    if (!$env:DESK_DATABASE) { $env:DESK_DATABASE = Join-Path $PSScriptRoot 'runtime\demo.db' }
}
if ($DocsDir) { $env:DESK_DOCS_DIR = $DocsDir }
python -m uvicorn desk.main:app --host 127.0.0.1 --port $Port
