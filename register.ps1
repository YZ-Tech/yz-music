# Register the custom URL protocols used by yt-play:
#   mpv-yt://   play now (replace playlist)
#   mpv-yt-n:// insert next
#   mpv-yt-q:// append to queue
#   mpv-yt-d:// set audio-delay (payload = ms, e.g. mpv-yt-d://-220)
# User-scope only (HKCU) — no admin rights needed.
# To remove later:
#   Remove-Item HKCU:\Software\Classes\mpv-yt   -Recurse
#   Remove-Item HKCU:\Software\Classes\mpv-yt-n -Recurse
#   Remove-Item HKCU:\Software\Classes\mpv-yt-q -Recurse
#   Remove-Item HKCU:\Software\Classes\mpv-yt-d -Recurse

$scriptDir  = $PSScriptRoot
$scriptPath = Join-Path $scriptDir "yt-play.py"
$python     = "C:\Python314\python.exe"

if (-not (Test-Path $scriptPath)) { Write-Error "yt-play.py not found at $scriptPath"; exit 1 }
if (-not (Test-Path $python))     { Write-Error "python not found at $python";        exit 1 }

function Register-Protocol([string]$proto) {
    $cmd = '"' + $python + '" "' + $scriptPath + '" "%1"'
    $key = "HKCU:\Software\Classes\$proto"

    New-Item -Path $key -Force | Out-Null
    Set-ItemProperty -Path $key -Name "(default)"    -Value "URL:$proto protocol"
    Set-ItemProperty -Path $key -Name "URL Protocol" -Value ""

    New-Item -Path "$key\shell\open\command" -Force | Out-Null
    Set-ItemProperty -Path "$key\shell\open\command" -Name "(default)" -Value $cmd

    Write-Host "Registered ${proto}:// -> $cmd"
}

Register-Protocol "mpv-yt"
Register-Protocol "mpv-yt-n"
Register-Protocol "mpv-yt-q"
Register-Protocol "mpv-yt-d"
