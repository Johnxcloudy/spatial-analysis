$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$root = Split-Path -Parent $PSScriptRoot
$destination = Join-Path $root 'apps\desktop\src-tauri\icons'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$bitmap = [System.Drawing.Bitmap]::new(256, 256)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.Clear([System.Drawing.ColorTranslator]::FromHtml('#153e35'))
$light = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#c6e8d5'))
$green = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#66b98a'))
$white = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#f2f7f3'), 5)
$graphics.FillRectangle($light, 46, 52, 108, 108)
$graphics.FillRectangle($green, 102, 104, 108, 108)
$graphics.DrawRectangle($white, 46, 52, 108, 108)
$graphics.DrawRectangle($white, 102, 104, 108, 108)
$bitmap.Save((Join-Path $destination 'icon.png'), [System.Drawing.Imaging.ImageFormat]::Png)
$icon = [System.Drawing.Icon]::FromHandle($bitmap.GetHicon())
$stream = [System.IO.File]::Create((Join-Path $destination 'icon.ico'))
try { $icon.Save($stream) } finally { $stream.Dispose(); $icon.Dispose(); $white.Dispose(); $green.Dispose(); $light.Dispose(); $graphics.Dispose(); $bitmap.Dispose() }
Write-Output 'Generated desktop icon assets.'
