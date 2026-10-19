# Strict UTF-8 JSON transactions, shared by native install and uninstall only.
$script:CCBConfigBytes = @{}

function Read-CCBSettings {
  param([Parameter(Mandatory)][string]$Path)
  $full = [IO.Path]::GetFullPath($Path)
  if (-not [IO.File]::Exists($full)) {
    $script:CCBConfigBytes[$full] = $null
    return [pscustomobject]@{}
  }
  $bytes = [IO.File]::ReadAllBytes($full)
  try {
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    $text = $utf8.GetString($bytes).TrimStart([char]0xfeff)
    if (-not $text.TrimStart().StartsWith('{')) { throw 'Expected a JSON object' }
    $value = ConvertFrom-Json -InputObject $text -ErrorAction Stop
    if ($null -eq $value -or $value -isnot [pscustomobject]) { throw 'Expected a JSON object' }
    if ($null -ne $value.permissions) {
      if ($value.permissions -isnot [pscustomobject]) { throw 'permissions must be an object' }
      if ($null -ne $value.permissions.allow) {
        if ($value.permissions.allow -isnot [array]) { throw 'permissions.allow must be an array' }
        foreach ($item in $value.permissions.allow) {
          if ($item -isnot [string]) { throw 'permissions.allow must contain strings' }
        }
      }
    }
  } catch {
    throw "Cannot safely read settings at $full. Original file was not changed. Repair its UTF-8 JSON before retrying."
  }
  $script:CCBConfigBytes[$full] = $bytes
  return $value
}

function Write-CCBSettings {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Value)
  $full = [IO.Path]::GetFullPath($Path)
  if (-not $script:CCBConfigBytes.ContainsKey($full)) { throw 'Read settings before writing them.' }
  # PowerShell 5.1 otherwise silently truncates deeply nested user settings.
  $text = ConvertTo-Json -InputObject $Value -Depth 100 -WarningAction Stop
  $null = ConvertFrom-Json -InputObject $text -ErrorAction Stop
  $expected = $script:CCBConfigBytes[$full]
  $actual = if ([IO.File]::Exists($full)) { [IO.File]::ReadAllBytes($full) } else { $null }
  if (($null -eq $expected) -ne ($null -eq $actual) -or
      ($null -ne $expected -and [Convert]::ToBase64String($expected) -cne [Convert]::ToBase64String($actual))) {
    throw "Settings changed during installation: $full. Retry without overwriting the concurrent edit."
  }
  $temporary = $full + '.ccb-tmp-' + [Guid]::NewGuid().ToString('N')
  try {
    [IO.File]::WriteAllText($temporary, $text + "`n", [Text.UTF8Encoding]::new($false, $true))
    if ($null -ne $expected) {
      $backup = $full + '.ccb-backup-' + [Guid]::NewGuid().ToString('N')
      [IO.File]::Replace($temporary, $full, $backup)
      Write-Host "Original settings backup: $backup"
    } else {
      [IO.File]::Move($temporary, $full)
    }
  } finally {
    if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
  }
}
