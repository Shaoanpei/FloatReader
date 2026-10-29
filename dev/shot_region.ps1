# 区域截图并放大（开发调试用）
param([string]$Out = "region.png", [int]$X = 0, [int]$Y = 0,
      [int]$W = 600, [int]$H = 300, [int]$Scale = 2)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap $W, $H
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($X, $Y, 0, 0, $bmp.Size)
$big = New-Object System.Drawing.Bitmap ($W * $Scale), ($H * $Scale)
$g2 = [System.Drawing.Graphics]::FromImage($big)
$g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
$g2.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Half
$g2.DrawImage($bmp, 0, 0, $big.Width, $big.Height)
$big.Save((Join-Path (Get-Location) $Out), [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $g2.Dispose(); $bmp.Dispose(); $big.Dispose()
Write-Output "saved $Out"
