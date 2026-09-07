param(
    [string]$Source = (Join-Path $PSScriptRoot "..\.."),
    [string]$Keys = (Get-Location).Path,
    [string]$UpdatesRepository = "https://github.com/TrevizamNetwork001/backup-manager-updates.git",
    [string]$RepositoryName = "TrevizamNetwork001/backup-manager-updates",
    [string]$Output = (Join-Path (Get-Location) "publicacao-1.1.0"),
    [switch]$SkipRelease
)

$ErrorActionPreference = "Stop"
$prepare = Join-Path $Source "docs\release-tools\prepare_1_1_0.ps1"
if (-not (Test-Path -LiteralPath $prepare -PathType Leaf)) {
    throw "Nao encontrei o assistente em $prepare"
}

& $prepare -Source $Source -Keys $Keys -Output $Output -Repository $RepositoryName
if ($LASTEXITCODE -ne 0) { throw "A assinatura falhou." }

$pages = Join-Path $Output "pages"
$releaseDir = Join-Path $Output "releases"
$stage = Join-Path ([System.IO.Path]::GetTempPath()) ("backup-manager-updates-" + [guid]::NewGuid().ToString("N"))
try {
    git clone $UpdatesRepository $stage
    if ($LASTEXITCODE -ne 0) { throw "Nao foi possivel clonar o repositorio de updates." }
    Copy-Item -Path (Join-Path $pages "*") -Destination $stage -Recurse -Force
    Push-Location $stage
    try {
        git add .
        git diff --cached --quiet
        if ($LASTEXITCODE -ne 0) {
            git commit -m "publish: updates index for v1.1.0"
            if ($LASTEXITCODE -ne 0) { throw "Falha ao criar commit do indice." }
            git push origin HEAD
            if ($LASTEXITCODE -ne 0) { throw "Falha ao enviar o indice para o GitHub." }
        }
    } finally { Pop-Location }

    if (-not $SkipRelease) {
        $gh = Get-Command gh -ErrorAction SilentlyContinue
        if ($null -eq $gh) {
            Write-Warning "GitHub CLI nao encontrado; crie a Release v1.1.0 pelo navegador e anexe os dois arquivos de releases."
        } else {
            & $gh.Source release view v1.1.0 --repo $RepositoryName *> $null
            if ($LASTEXITCODE -ne 0) {
                & $gh.Source release create v1.1.0 --repo $RepositoryName --title "Backup Manager Local 1.1.0" --notes "Versao estavel 1.1.0. Pacote assinado e validado localmente." --latest (Join-Path $releaseDir "backup-manager-local-1.1.0.bmu") (Join-Path $releaseDir "backup-manager-local-1.1.0.bmu.sig")
            } else {
                & $gh.Source release upload v1.1.0 --repo $RepositoryName (Join-Path $releaseDir "backup-manager-local-1.1.0.bmu") (Join-Path $releaseDir "backup-manager-local-1.1.0.bmu.sig") --clobber
            }
            if ($LASTEXITCODE -ne 0) { throw "Falha ao publicar a Release no GitHub." }
        }
    }
    Write-Host "OK: indice publicado e pacote preparado em $Output"
} finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
}
