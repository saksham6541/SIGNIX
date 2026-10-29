$ErrorActionPreference = "Stop"
$scenes = Get-Content "$PSScriptRoot/production.json" -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Force "$PSScriptRoot/audio" | Out-Null
$voice = New-Object -ComObject SAPI.SpVoice
$voice.Voice = $voice.GetVoices() | Where-Object { $_.GetDescription() -like '*Zira*' } | Select-Object -First 1
$voice.Rate = 0
foreach ($scene in $scenes) {
  $i = 0
  foreach ($line in $scene.lines) {
    $stream = New-Object -ComObject SAPI.SpFileStream
    $stream.Format.Type = 22
    $stream.Open("$PSScriptRoot/audio/$($scene.name)-$i.wav", 3, $false)
    $voice.AudioOutputStream = $stream
    [void]$voice.Speak($line)
    $stream.Close()
    $i++
  }
}
