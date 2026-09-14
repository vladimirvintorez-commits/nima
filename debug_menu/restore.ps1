param(
    [ValidateSet('latest','with-myenv','without-myenv','UN')]
    [string]$Mode = 'latest',
    [switch]$DryRun,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = 'B:\Neyronya'
$BackupRoot = 'B:\'
$ProjectName = 'Neyronya'
# Launch/debug menu preservation marker: restore must keep every launcher and debug-menu file below.
# Preserved explicitly: debug_menu/, archive_gui.py, debug menu.bat, restore_gui.py, restore.bat, restore.ps1.
$KeepNames = @(
    'debug_menu',
    'debug menu.bat'
)

function Fail($Message) {
    Write-Error $Message
    exit 1
}

function Get-Extractor {
    $candidates = @(
        'rar.exe', 'WinRAR.exe', '7z.exe',
        'B:\Winrar\rar.exe', 'B:\Winrar\WinRAR.exe', 'B:\Winrar\7z.exe',
        "$env:ProgramFiles\WinRAR\rar.exe", "$env:ProgramFiles\WinRAR\WinRAR.exe",
        "$env:ProgramFiles\7-Zip\7z.exe",
        "${env:ProgramFiles(x86)}\WinRAR\rar.exe", "${env:ProgramFiles(x86)}\WinRAR\WinRAR.exe",
        "${env:ProgramFiles(x86)}\7-Zip\7z.exe"
    )
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    return $null
}

function Is-UnArchive([System.IO.FileInfo]$File) {
    return $File.BaseName -match '(?i)(^|\s)(UN|UM)(\s|$)'
}

function Get-BackupArchive([string]$SelectedMode) {
    $archives = Get-ChildItem -LiteralPath $BackupRoot -File |
        Where-Object { ($_.Name -like 'Нимфея V*.rar' -or $_.Name -like 'Vita V*.rar') -and $_.FullName -notlike "$ProjectRoot\*" }
    if (-not $archives) { Fail "RAR-бэкапы Нимфея/Vita не найдены в $BackupRoot" }

    if ($SelectedMode -eq 'with-myenv') {
        $archives = $archives | Where-Object { -not (Is-UnArchive $_) }
    } elseif ($SelectedMode -eq 'without-myenv' -or $SelectedMode -eq 'UN') {
        $archives = $archives | Where-Object { Is-UnArchive $_ }
    }

    $archive = $archives | Sort-Object LastWriteTime, Name -Descending | Select-Object -First 1
    if (-not $archive) { Fail "Не найден архив для режима $SelectedMode" }
    return $archive
}

function Invoke-Extractor([string]$Extractor, [string[]]$Arguments, [switch]$AllowInDryRun) {
    $displayArgs = ($Arguments | ForEach-Object { if ($_ -match '\s') { '`"' + $_ + '`"' } else { $_ } }) -join ' '
    Write-Host ("RUN: `"{0}`" {1}" -f $Extractor, $displayArgs)
    if ($DryRun -and -not $AllowInDryRun) { return }
    $output = & $Extractor @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { Fail "Распаковщик завершился с кодом $LASTEXITCODE. $output" }
}

function Test-Archive([string]$Extractor, [string]$ArchivePath) {
    $name = [IO.Path]::GetFileName($Extractor).ToLowerInvariant()
    if ($name -eq '7z.exe') { $args = @('t', $ArchivePath) }
    else { $args = @('t', '-idq', $ArchivePath) }
    Invoke-Extractor $Extractor $args -AllowInDryRun
}

function Get-ArchiveList([string]$Extractor, [string]$ArchivePath) {
    $name = [IO.Path]::GetFileName($Extractor).ToLowerInvariant()
    if ($name -eq '7z.exe') { $args = @('l', '-slt', $ArchivePath) }
    else { $args = @('lb', $ArchivePath) }
    if ($DryRun) {
        $output = & $Extractor @args 2>&1
        if ($LASTEXITCODE -ne 0) { Fail "Не удалось прочитать список архива: $output" }
        return $output
    }
    $output = & $Extractor @args 2>&1
    if ($LASTEXITCODE -ne 0) { Fail "Не удалось прочитать список архива: $output" }
    return $output
}

function Clear-Project([string]$Root) {
    $items = Get-ChildItem -LiteralPath $Root -Force
    foreach ($item in $items) {
        if ($KeepNames -contains $item.Name) {
            Write-Host "KEEP: $($item.FullName)"
            continue
        }
        Write-Host "DELETE: $($item.FullName)"
        if (-not $DryRun) {
            Remove-Item -LiteralPath $item.FullName -Recurse -Force
        }
    }
}

function Expand-ArchiveToProject([string]$Extractor, [string]$ArchivePath, [bool]$HasRootFolder) {
    $tempRoot = Join-Path $env:TEMP ("Neyronya_restore_" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
    try {
        $name = [IO.Path]::GetFileName($Extractor).ToLowerInvariant()
        if ($name -eq '7z.exe') { $args = @('x', '-y', "-o$tempRoot", $ArchivePath) }
        else { $args = @('x', '-o+', $ArchivePath, $tempRoot + '\') }
        Invoke-Extractor $Extractor $args
        if ($DryRun) { return }

        $source = if ($HasRootFolder -and (Test-Path -LiteralPath (Join-Path $tempRoot $ProjectName))) {
            Join-Path $tempRoot $ProjectName
        } else {
            $tempRoot
        }
        Copy-Item -LiteralPath (Join-Path $source '*') -Destination $ProjectRoot -Recurse -Force
    }
    finally {
        if (Test-Path -LiteralPath $tempRoot) {
            Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

$resolved = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd('\')
if ($resolved -ne 'B:\Neyronya') { Fail "Защита: ProjectRoot должен быть B:\Neyronya, сейчас $resolved" }
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'save.bat'))) { Fail 'Защита: в проекте не найден save.bat' }

$archive = Get-BackupArchive $Mode
$extractor = Get-Extractor
if (-not $extractor) { Fail 'Не найден распаковщик rar.exe/WinRAR.exe/7z.exe' }

Write-Host "Project: $ProjectRoot"
Write-Host "Mode: $Mode"
Write-Host "Archive: $($archive.FullName)"
Write-Host "Archive kind: $(if (Is-UnArchive $archive) { 'without-myenv / UN(UM)' } else { 'with-myenv' })"
Write-Host "Extractor: $extractor"
Write-Host "DryRun: $DryRun"

Test-Archive $extractor $archive.FullName
$list = Get-ArchiveList $extractor $archive.FullName
$hasRoot = @($list | Where-Object { $_ -match '^(Path = )?Neyronya(\\|/)' -or $_ -match '^Neyronya(\\|/)' }).Count -gt 0
Write-Host "Archive contains root folder Neyronya: $hasRoot"

if ($DryRun) {
    Write-Host 'DRY-RUN: удаление и распаковка не выполнялись.'
    Clear-Project $ProjectRoot
    exit 0
}

if (-not $Yes) {
    $answer = Read-Host "Это удалит содержимое $ProjectRoot и восстановит архив. Введите RESTORE для продолжения"
    if ($answer -ne 'RESTORE') { Fail 'Операция отменена пользователем' }
}

Clear-Project $ProjectRoot
Expand-ArchiveToProject $extractor $archive.FullName $hasRoot
Write-Host 'Восстановление завершено.'
