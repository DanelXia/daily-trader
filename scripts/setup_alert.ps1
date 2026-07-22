$action = New-ScheduledTaskAction -Execute 'python' -Argument 'D:\work\daily-trader\scripts\price_alert_300738.py'
$trigger = New-ScheduledTaskTrigger -Once -At '09:00' -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Hours 6)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'DailyTrader_300738_Alert' -Action $action -Trigger $trigger -Settings $settings -Force
Write-Host "Task created OK"
