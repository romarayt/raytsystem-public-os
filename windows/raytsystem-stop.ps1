# Останавливает локальный сервер «Системы Райта» (процесс на порту 8765).
$connection = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($connection) {
    $serverPid = $connection | Select-Object -First 1 -ExpandProperty OwningProcess
    Stop-Process -Id $serverPid -Force -Confirm:$false
    Write-Host "Сервер остановлен (PID $serverPid)."
} else {
    Write-Host 'Сервер не запущен.'
}
