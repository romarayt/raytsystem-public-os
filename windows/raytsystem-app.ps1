# Запуск «Системы Райта» как десктоп-приложения.
# Поднимает локальный сервер (если ещё не запущен) и открывает Edge в режиме
# приложения — отдельное окно без адресной строки и вкладок.
$ErrorActionPreference = 'SilentlyContinue'
$repo = Split-Path -Parent $PSScriptRoot
$url = 'http://127.0.0.1:8765/'

function Test-Server {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

if (-not (Test-Server)) {
    Start-Process -FilePath 'uv' `
        -ArgumentList 'run', 'raytsystem', 'start', '--no-open' `
        -WorkingDirectory $repo -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(90)
    while (-not (Test-Server) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
}

if (Test-Server) {
    Start-Process msedge -ArgumentList "--app=$url"
} else {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        'Сервер «Системы Райта» не поднялся за 90 секунд. Запусти raytsystem start вручную и посмотри ошибку.',
        'Система Райта', 'OK', 'Warning') | Out-Null
}
