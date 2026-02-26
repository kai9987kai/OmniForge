param(
  [string]$Host = "127.0.0.1",
  [int]$Port = 8787,
  [switch]$GenerateWindhawkMod,
  [switch]$RestartWindhawk
)

$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot\..
try {
  if ($GenerateWindhawkMod -or $RestartWindhawk) {
    $serveArgs = @("serve-windhawk", "--host", $Host, "--port", $Port)
    if (-not $RestartWindhawk) {
      $serveArgs += "--no-restart"
    }
    python -m omniforge_ai @serveArgs
    return
  }

  python -m omniforge_ai serve --host $Host --port $Port
}
finally {
  Pop-Location
}
