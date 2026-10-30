Все команды выполняйте в PowerShell из проекта. Сначала настройте окружение:

```powershell
cd C:\Python\Prosperous_Bot\futures_portfolio
.\.venv\Scripts\Activate.ps1

$env:PATH = "C:\Users\svsma\AppData\Local\hermes\node;C:\Users\svsma\AppData\Roaming\npm;$env:PATH"
$env:PYTHONPATH = "C:\Python\Prosperous_Bot;C:\Python\Prosperous_Bot\futures_portfolio"
$env:PYTHONIOENCODING = "utf-8"

$taskPython = "C:\Python\Prosperous_Bot\futures_portfolio\.venv\Scripts\python.exe"
```

Посмотреть процессы и состояние dashboard:

```powershell
pm2 list
Invoke-RestMethod http://127.0.0.1:8080/health
Start-Process http://127.0.0.1:8080
```

Читать журналы; `Ctrl+C` завершает только просмотр:

```powershell
pm2 logs supervisor-service --lines 50
pm2 logs paper-apr --lines 50
pm2 logs swarm-observer --lines 30

Get-Content .\logs\supervisor.log -Tail 50 -Wait
Get-Content .\core\logs\paper_APRUSDT.log -Tail 50 -Wait
```

Перезапустить только supervisor после изменения его настроек:

```powershell
pm2 restart supervisor-service --update-env
```

Перезапустить одного существующего бота с сохранением файлов состояния:

```powershell
pm2 restart paper-apr --update-env
```

Перезапустить все существующие PAPER-боты и supervisor. Список берётся из PM2, поэтому старые имена вводить не нужно:

```powershell
pm2 stop supervisor-service

$taskPaperNames = @(pm2 jlist | ConvertFrom-Json |
    Where-Object { $_.name -like "paper-*" } |
    Select-Object -ExpandProperty name)

foreach ($taskBot in $taskPaperNames) {
    pm2 restart $taskBot --update-env
}

pm2 restart supervisor-service --update-env
pm2 list
```

Не публикуйте полный вывод `pm2 jlist`: он может содержать переменные окружения. Здесь он используется только для извлечения имён.

Остановить supervisor и PAPER-воркеры:

```powershell
pm2 stop supervisor-service

$taskPaperNames = @(pm2 jlist | ConvertFrom-Json |
    Where-Object { $_.name -like "paper-*" } |
    Select-Object -ExpandProperty name)

foreach ($taskBot in $taskPaperNames) {
    pm2 stop $taskBot
}
```

**`pm2 stop` не закрывает позиции** — он останавливает процессы. При REAL так останавливать управление открытыми позициями нельзя.

Возобновить остановленные PAPER-воркеры, затем supervisor:

```powershell
$taskPaperNames = @(pm2 jlist | ConvertFrom-Json |
    Where-Object { $_.name -like "paper-*" } |
    Select-Object -ExpandProperty name)

foreach ($taskBot in $taskPaperNames) {
    pm2 restart $taskBot --update-env
}

pm2 restart supervisor-service --update-env
```

Управлять dashboard и сборщиком:

```powershell
pm2 restart swarm-dashboard
pm2 restart swarm-observer
```

После перезагрузки ПК сначала проверьте список. Восстановление допустимо, только если он пуст и ранее был сохранён:

```powershell
pm2 list
pm2 resurrect
pm2 list
```

Сохранить актуальный список процессов после проверки:

```powershell
pm2 save
```

Это сохраняет список для `resurrect`; само по себе автозапуск Windows не настраивает.

Если процессов инфраструктуры вообще нет, создать их:

```powershell
pm2 start scripts/watch_paper_swarm.py --name swarm-observer --interpreter "$taskPython"
pm2 start supervisor/supervisor_service.py --name supervisor-service --interpreter "$taskPython"

cmd.exe /c "pm2 start dashboard/dashboard.py --name swarm-dashboard --interpreter C:\Python\Prosperous_Bot\futures_portfolio\.venv\Scripts\python.exe -- --host 127.0.0.1 --port 8080 --production"
```

Эти `start` выполняйте только при отсутствии соответствующего процесса. PAPER-ботов подбирает и запускает supervisor.

Вручную выполнить ежедневный отбор:

```powershell
$taskLog = ".\reports\daily_universe\manual_$(Get-Date -Format yyyyMMdd_HHmmss).log"
& $taskPython .\scanner\daily_universe.py *> $taskLog
$LASTEXITCODE
Get-Content $taskLog -Tail 30
```

Посмотреть результат отбора и список кандидатов:

```powershell
Get-Content .\reports\daily_universe\latest.json
Get-Content .\tickers.txt
(Get-Content .\tickers.txt | Where-Object { $_.Trim() }).Count
```

Полный reset не включаю в обычные команды: он требует остановки писателей, архива состояний и согласованной инициализации бюджета. Сейчас **торговый бюджет — 8 000 USDT**, а внешние 60 000 USDT текущий PAPER-движок пока не моделирует.