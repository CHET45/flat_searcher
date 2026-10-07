param([switch]$Remove)
# Starts the daily run with Windows and puts two shortcuts on the desktop:
# the progress window and the on/off switch. -Remove takes all three away.
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not $Remove -and -not (Test-Path $python)) { throw "No virtual environment: $python" }
$startup = [Environment]::GetFolderPath("Startup")
$desktop = [Environment]::GetFolderPath("Desktop")
$system = Join-Path $env:SystemRoot "System32"
$links = @(
    @{ Path = Join-Path $startup "Flat Searcher.lnk"; Arguments = "-m flat_searcher daily --startup"
       Icon = "$system\imageres.dll,144"; Description = "Runs the day's flat search when Windows starts" },
    @{ Path = Join-Path $desktop "Flat Searcher progress.lnk"; Arguments = "-m flat_searcher monitor"
       Icon = "$system\imageres.dll,144"; Description = "Shows how the day's flat search is going" },
    @{ Path = Join-Path $desktop "Flat Searcher on-off.lnk"; Arguments = "-m flat_searcher switch"
       Icon = "$system\shell32.dll,27"; Description = "Turns the daily flat search off, or back on" }
)
$shell = New-Object -ComObject WScript.Shell
foreach ($link in $links) {
    if ($Remove) {
        Remove-Item -LiteralPath $link.Path -ErrorAction SilentlyContinue
        continue
    }
    $shortcut = $shell.CreateShortcut($link.Path)
    $shortcut.TargetPath = $python
    $shortcut.Arguments = $link.Arguments
    $shortcut.WorkingDirectory = $root
    $shortcut.IconLocation = $link.Icon
    $shortcut.Description = $link.Description
    $shortcut.Save()
    $link.Path
}
