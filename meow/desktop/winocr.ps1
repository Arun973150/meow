# Read text and its boxes out of an image, using the OCR engine Windows
# already ships. No install, no model download, no GPU.
#
# WinRT's async methods return IAsyncOperation, which PowerShell will not
# await on its own - hence the reflection to reach Task.FromAsync's generic
# form. Printing JSON because the caller is Python.
param([Parameter(Mandatory = $true)][string]$Path,
      [Parameter(Mandatory = $true)][string]$Out)

$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null

$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1

function Await($operation, $type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($operation))
    $task.Wait(-1) | Out-Null
    $task.Result
}

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'no OCR engine for the user profile languages' }

$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

$words = New-Object System.Collections.ArrayList
foreach ($line in $result.Lines) {
    foreach ($word in $line.Words) {
        $box = $word.BoundingRect
        [void]$words.Add([ordered]@{
            text = $word.Text
            left = [int]$box.X
            top = [int]$box.Y
            right = [int]($box.X + $box.Width)
            bottom = [int]($box.Y + $box.Height)
            line = $line.Text
        })
    }
}
# Written to a FILE as UTF-8, not to stdout. Windows PowerShell 5.1
# encodes the console in the ANSI codepage, and an application's UI text
# is routinely not representable in cp1252 - the caller's reader thread
# died on byte 0x90 out of Photoshop and lost the whole reading.
$json = @{ width = $decoder.PixelWidth; height = $decoder.PixelHeight;
           words = $words } | ConvertTo-Json -Depth 5 -Compress
[System.IO.File]::WriteAllText($Out, $json, (New-Object System.Text.UTF8Encoding $false))
