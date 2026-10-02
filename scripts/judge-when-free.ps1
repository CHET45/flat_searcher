param(
    [string]$Day,
    [int]$QuietMinutes = 3
)
Set-Location (Join-Path $PSScriptRoot "..")
$env:PYTHONIOENCODING = "utf-8"
$log = "data\judge-when-free.log"
function Log($message) { "$(Get-Date -Format s) $message" | Out-File $log -Append -Encoding utf8 }

function OtherOllamaClients {
    $ollama = @(Get-Process -Name ollama, llama-server -ErrorAction SilentlyContinue | ForEach-Object Id)
    @(Get-NetTCPConnection -RemotePort 11434 -State Established -ErrorAction SilentlyContinue |
        Where-Object { $ollama -notcontains $_.OwningProcess } |
        ForEach-Object OwningProcess | Sort-Object -Unique)
}

$dayArgs = if ($Day) { @("--day", $Day) } else { @() }
Log "waiting until no other process has used Ollama for $QuietMinutes minutes"
$quietPolls = 0
$lastBusy = ""
while ($quietPolls -lt ($QuietMinutes * 6)) {
    $clients = OtherOllamaClients
    if ($clients.Count -gt 0) {
        $quietPolls = 0
        $now = $clients -join ","
        if ($now -ne $lastBusy) { Log "busy: other client pid(s) $now"; $lastBusy = $now }
    } else {
        $quietPolls++
    }
    Start-Sleep 10
}
Log "free; running judge $($dayArgs -join ' ')"
& .\.venv\Scripts\python.exe -m flat_searcher judge @dayArgs 2>&1 | Out-File data\judge-run.log -Append -Encoding utf8
Log "judge exit=$LASTEXITCODE : $(Get-Content data\judge-run.log -Tail 1)"
