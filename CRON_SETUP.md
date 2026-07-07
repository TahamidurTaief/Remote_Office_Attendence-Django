# Cron Job Setup (Shared Hosting - cPanel)

## Daily Retention Job
Time: 2:00 AM daily

Command:
```bash
0 2 * * * cd /home/username/fieldtrack && /home/username/venv/bin/python manage.py run_retention >> /home/username/logs/retention.log 2>&1
```

## Manual Run
```bash
python manage.py run_retention
```

## Test (dry run - just shows counts):
```bash
python manage.py run_retention --dry-run
```

## Backup Jobs

### Daily backup at 1:00 AM
```bash
0 1 * * * cd /home/username/fieldtrack && /home/username/venv/bin/python manage.py run_backup --type=auto_daily >> /home/username/logs/backup_daily.log 2>&1
```

### 3-day backup at 1:30 AM
```bash
30 1 * * * cd /home/username/fieldtrack && /home/username/venv/bin/python manage.py run_backup --type=auto_3day >> /home/username/logs/backup_3day.log 2>&1
```
