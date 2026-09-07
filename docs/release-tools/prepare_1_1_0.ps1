param(
    [string]$Source = (Join-Path $PSScriptRoot "..\.."),
    [string]$Keys = (Get-Location).Path,
    [string]$Output = (Join-Path (Get-Location) "publicacao-1.1.0"),
    [string]$Repository = "TrevizamNetwork001/backup-manager-updates"
)

$ErrorActionPreference = "Stop"
$script = Join-Path $Source "docs\release-tools\prepare_1_1_0.py"
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
    throw "Codigo-fonte invalido: nao encontrei $script"
}
if (-not (Test-Path -LiteralPath (Join-Path $Keys "update-private-key.pem") -PathType Leaf)) {
    throw "Nao encontrei update-private-key.pem em $Keys"
}
if (-not (Test-Path -LiteralPath (Join-Path $Keys "update-public-key.pem") -PathType Leaf)) {
    throw "Nao encontrei update-public-key.pem em $Keys"
}

& python $script --source $Source --keys $Keys --output $Output --repository $Repository
if ($LASTEXITCODE -ne 0) {
    throw "A preparacao falhou (codigo $LASTEXITCODE)."
}
