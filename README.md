# NKON stock monitor

Мониторинг наличия [BAK N21700CG NO WRAP](https://www.nkon.nl/ua/bak-n21700cg-5000mah-10-2a-no-wrap.html) на nkon.nl с уведомлением в Telegram.

Запускается каждые 15 минут через systemd timer на **Oracle Cloud Free Tier VM**.  
Использует [Camoufox](https://github.com/daijro/camoufox) — патченый Firefox, который проходит Cloudflare Bot Management без CAPTCHA.

---

## Архитектура

```
GitHub (код)
    │  push → main
    ▼
GitHub Actions (только деплой)
    │  SSH → deploy/update.sh
    ▼
Oracle Cloud VM (мониторинг)
    systemd timer каждые 15 мин
    └─ stock_monitor.py
           │  Camoufox Firefox
           │  → nkon.nl (обход Cloudflare)
           └─ Telegram Bot API → уведомление
```

---

## Первоначальная настройка Google Cloud VM

### 1. Создать VM (бесплатно навсегда)

[console.cloud.google.com/compute/instances](https://console.cloud.google.com/compute/instances) → **Create Instance**

| Параметр | Значение |
|---|---|
| Region | `us-central1` (Iowa) |
| Zone | `us-central1-a` |
| Machine type | **e2-micro** |
| Boot disk OS | **Ubuntu 22.04 LTS** |
| Boot disk type | Standard persistent disk (не SSD) |
| Boot disk size | **30 GB** |
| Firewall | HTTP/HTTPS — выключено (не нужно) |

Нажми **Create**, подожди ~1 мин.

### 2. Открыть SSH

В списке инстансов нажми кнопку **SSH** — откроется терминал прямо в браузере. Дополнительных ключей не нужно.

### 3. Запустить setup на VM

В SSH-терминале:

```bash
git clone https://github.com/<ты>/<репо>.git ~/nkon-monitor
bash ~/nkon-monitor/deploy/setup.sh
```

Скрипт установит всё сам и спросит credentials в интерактивном режиме.

---

## GitHub Actions — автодеплой при push

При каждом `git push` в `main` GitHub Actions подключается к VM по SSH и запускает `deploy/update.sh` (pull + pip install + restart timer).

### Нужные секреты в GitHub

Открой **Settings → Secrets and variables → Actions** и добавь:

| Secret | Значение |
|--------|----------|
| `VM_HOST` | Публичный IP Oracle VM |
| `VM_USER` | `ubuntu` (или `opc` для Oracle Linux) |
| `VM_SSH_KEY` | Приватный SSH ключ (содержимое `~/.ssh/id_rsa`) |
| `VM_PORT` | `22` (опционально, это дефолт) |

> Credentials NKON и Telegram хранятся **только на VM** в `~/nkon-monitor/.env` — в GitHub они не нужны.

---

## Управление на VM

```bash
# Статус таймера (когда следующий запуск)
systemctl --user status nkon-monitor.timer

# Запустить проверку прямо сейчас
systemctl --user start nkon-monitor.service

# Живые логи
journalctl --user -u nkon-monitor.service -f

# Последние 50 строк лога
journalctl --user -u nkon-monitor.service -n 50

# Проверить без отправки в Telegram
cd ~/nkon-monitor && .venv/bin/python stock_monitor.py --check-only

# Тест Telegram
cd ~/nkon-monitor && .venv/bin/python stock_monitor.py --telegram-test

# Обновить вручную (без push)
bash ~/nkon-monitor/deploy/update.sh
```

---

## Локальная проверка (Windows / macOS / Linux)

Нужен Python 3.11+.

```bash
pip install -r requirements.txt
python -m camoufox fetch

# Создай .env с credentials (см. .env.example)
python stock_monitor.py --simulate-stock   # офлайн-тест логики
python stock_monitor.py --telegram-test    # проверить Telegram
python stock_monitor.py --check-only       # реальная проверка nkon.nl
```

---

## Как работает защита от дублей

- Статус хранится в файле `.monitor/stock.txt` на VM
- Уведомление отправляется только при переходе `OUT_OF_STOCK → IN_STOCK`
- Если проверка упала — статус не перезаписывается, следующий запуск попробует снова
- Встроен retry: 3 попытки с паузой 30 с между ними (на случай временного сбоя Cloudflare)

---

## Файловая структура

```
stock_monitor.py      # основной скрипт
requirements.txt      # Python зависимости
deploy/
  setup.sh            # установка на Oracle VM (запустить один раз)
  update.sh           # обновление (вызывает GitHub Actions при push)
.github/workflows/
  nkon-stock.yml      # CI/CD: деплой на VM + ручной запуск команд
.env                  # локальные credentials (в .gitignore)
```
