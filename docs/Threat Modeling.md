Комплексное моделирование угроз (Threat Modeling) и аудит безопасности торговой системы

Роль: Security Architect
Методология: STRIDE, Secure-by-Design, Defense-in-Depth, Zero Trust Architecture
1. Executive Summary & Архитектурный профиль

Торговая система Prosperous Bot / Market-Neutral Futures Portfolio Rebalancer представляет собой распределенный торговый комплекс (демоны воркеров под PM2, сервис оркестрации Supervisor, web-дашборд на Flask, Telegram-оповещения, локальные хранилища SQLite/JSON/Feather и шлюз к бирже Binance USD-M Futures).
Ключевые компоненты:

    Core Execution Engine (core/main.py, core/executor.py, core/connector.py): выполняет ребалансировку, ордер-менеджмент, расчет маржи и ликвидаций.
    Supervisor Swarm Manager (supervisor/swarm_manager.py, supervisor_service.py): оркестрирует жизненный цикл paper/real воркеров, анализирует сканер и PnL, управляет процессами через PM2.
    Web Dashboard (dashboard/dashboard.py): панель мониторинга и оперативного управления (start/stop/restart/emergency stop) на Flask/Waitress.
    Data & Accounting (accounting/, data/, SQLite WAL, Feather): локальное состояние, история фандинга, учетные книги и сигналы.
    Внешние интеграции: Binance API (fapi.binance.com, api1.binance.com), Telegram Bot API.

2. Моделирование границ доверия (Trust Boundaries)

 [ Untrusted: Internet / Public Network ]
       │                                     ▲
       │ Web Requests (HTTP)                 │ Telegram Alerts
       ▼                                     │
┌───────────────────────────┐         ┌────────────────────────┐
│ Boundary 1: Web Dashboard │         │ Boundary 5: Telegram   │
│ (Port 8080 / Flask)       │         │ Notifier               │
└─────────────┬─────────────┘         └────────────────────────┘
              │ Local IPC / Subprocess           ▲
              ▼                                  │
┌────────────────────────────────────────────────┴─────────────┐
│ Boundary 2: Host & Supervisor Runtime (Local OS / PM2 / IPC) │
│ - supervisor_service.py / swarm_manager.py                   │
│ - IPC Signals: signals/*.flag                                │
│ - SQLite WAL: paper_budget.sqlite                            │
│ - Local Config & States: config.json, real_state_*.json      │
└─────────────┬────────────────────────────────────────────────┘
              │ In-process execution / python-binance
              ▼
┌──────────────────────────────────────────────────────────────┐
│ Boundary 3: Trading Engine (Core Workers: real-*, paper-*)   │
│ - Decimal calculation, Fuse gate, Invariant gate             │
└─────────────┬────────────────────────────────────────────────┘
              │ HTTPS (TLS 1.2/1.3 + HMAC-SHA256 signature)
              ▼
┌──────────────────────────────────────────────────────────────┐
│ Boundary 4: External Crypto Exchanges (Binance API/FAPI)     │
└──────────────────────────────────────────────────────────────┘

Анализ trust boundaries:

    Boundary 1 (Web Dashboard ↔ Local Host): Критическая граница. Дашборд слушает на 0.0.0.0:8080. При отсутствии обратного прокси с TLS или сегментации сети любой узел локальной сети/VPN может атаковать интерфейс управления.
    Boundary 2 (Supervisor ↔ Worker Bots): Взаимодействие идет через файловую систему (signals/*.flag, *.json) и вызовы командной оболочки (pm2). Отсутствует криптографическая целостность сигналов; любой локальный процесс с правами пользователя может манипулировать флагами siphon_*.flag, stop_*.flag.
    Boundary 3 (Engine ↔ State Files): Состояние кошелька и позиций хранится в открытом JSON/SQLite. Повреждение или подмена файла стейта может привести к рассинхронизации плеча и объема.
    Boundary 4 (Connector ↔ Binance API): Внешний канал защищен TLS, однако присутствуют нюансы с валидацией времени/recvWindow и жестким отключением системных прокси.

3. Моделирование угроз по STRIDE
STRIDE Категория 	Вектор атаки / Уязвимый компонент 	Исходный уровень риска 	Описание сценария угрозы
Spoofing (Спуфинг) 	Вектор 1: Web Dashboard Auth (dashboard/config.yaml) 	HIGH 	Дефолтный хэш пароля admin / Rebalancer0ID и дефолтный secret_key: CHANGE_ME_RANDOM_32_CHARS_MIN зафиксированы в репозитории. Злоумышленник в сети перехватывает/подбирает доступ и подделывает сессионные куки Flask.
Tampering (Подмена данных) 	Вектор 2: Подмена state/config файлов на хосте (config.json, real_state_*.json) 	HIGH 	Конфиг содержит список live_swarm, real_whitelist и параметры кредитования. Нет контроля целостности (HMAC/подписи). Неавторизованная модификация может форсировать открытие опасных позиций.
Tampering (Подмена данных) 	Вектор 3: Подмена доменных имен/DNS к Binance (Client.API_URL = 'https://api1.binance.com/api') 	MEDIUM 	В коде core/connector.py захардкожен fallback URL api1.binance.com с client.session.trust_env = False. Если скомпрометирован локальный DNS/resolv.conf, запросы уходят на сторонний резолв.
Repudiation (Отказ от авторства) 	Вектор 4: Аудит действий оператора в Dashboard 	MEDIUM 	Логируются вызовы в stdout/файл, но отсутствует tamper-evident журнал аудита действий пользователя (кто, когда инициировал emergency-stop или сменил режим).
Information Disclosure (Утечка данных) 	Вектор 5: Утечка API-ключей через логи, дампы окружения и config 	CRITICAL 	Потенциальный fallback на хранение api_key/secret_key прямо в config.json (в docs/CONFIG_REFERENCE.md и коде сохраняется fallback cfg.get("api_key")). Случайный коммит config.json ведет к полной компрометации биржевого счета.
Information Disclosure (Утечка данных) 	Вектор 6: Дашборд слушает на 0.0.0.0 без шифрования (HTTP) 	HIGH 	Логин и сессионные куки передаются в открытом виде (plaintext HTTP). Возможен перехват учетных данных в незащищенном сегменте сети (Wi-Fi/LAN/L2).
Denial of Service (Отказ в обслуживании) 	Вектор 7: Блокировка/бан IP по Rate Limits Binance (429/418) 	HIGH 	Пул воркеров и супервайзер опрашивают REST API. При масштабировании роя (20 paper + real) частые параллельные вызовы могут превысить IP rate limit (1200-2400 weight/min), вызвав автоматический IP-бан биржей и слепоту по ликвидациям.
Denial of Service (Отказ в обслуживании) 	Вектор 8: Unhandled Exception / Network Drift (-1021 Timestamp) 	MEDIUM 	REQUEST_RECVWINDOW = 30000 спасает от дрифта до 30с, но при рассинхронизации NTP свыше 30с торговый бот теряет возможность выставить/снять стоп-ордера.
Elevation of Privilege (Эскалация привилегий) 	Вектор 9: Выполнение произвольных команд через Dashboard/PM2 IPC 	CRITICAL 	В dashboard.py функции _execute_pm2_action и _execute_emergency_stop конструируют имена процессов из аргументов запроса (f"paper-{ticker.lower()}", ["pm2", "stop", process_name]). Недостаточная санитизация аргумента ticker может открыть вектор Command Injection или манипуляции чужими процессами PM2 на сервере.
4. Глубокий аудит ключевых подсистем (Secure-by-Design & Defense-in-Depth)
4.1. Защищенность хранения API-ключей

    Текущее состояние:
        Приоритет отдан переменным окружения: os.environ.get("BINANCE_API_KEY") и BINANCE_SECRET_KEY. Это соответствует Best Current Practices (12-Factor App).
        В .gitignore корректно включены .env, .env.local, config*.json (за исключением шаблонов).
    Выявленные риски:
        Fallback на незашифрованный JSON: В core/main.py, supervisor/swarm_manager.py, supervisor/aggregator.py сохраняется логика: os.environ.get("BINANCE_API_KEY", config.get("api_key", "")). Если оператор пропишет ключи в config.json, они могут случайно попасть в бэкап (backups/) или артефакты отчетов.
        Process memory & sub-processes: При спавне дочерних процессов воркеров передается env = os.environ.copy(), тем самым API-ключи раскрываются дочерним скриптам анализаторов, бэктестеров и сканеров, которым торговые секреты не требуются.
    Архитектурные рекомендации:
        Полностью запретить чтение API-секретов из config.json в боевом режиме (Fail-Safe: выбрасывать исключение при наличии секретов в файле конфига).
        Использовать принцип наименьших привилегий (Principle of Least Privilege): дочерним readonly-процессам (сканер, история) передавать очищенный env без BINANCE_SECRET_KEY.
        Для production-развертывания рекомендовать интеграцию с секрет-хранилищами (HashiCorp Vault, AWS Secrets Manager или DPAPI / системный keyring для локальных хостов).

4.2. Подпись запросов к криптобиржам и сетевой транспорт

    Текущее состояние:
        Используется библиотека python-binance. Подпись генерируется с использованием HMAC-SHA256 со временем запроса (timestamp) и recvWindow=30000.
        Отключен системный прокси (client.session.trust_env = False, proxies={'http': None, 'https': None}), что нейтрализует несанкционированный перехват локальными MITM-прокси/программами перехвата трафика.
    Выявленные риски:
        Завышенный recvWindow (30 000 мс): 30 секунд — это максимальный порог Binance. Если исходящий сетевой запрос будет задержан или перехвачен в течение этого окна, окно для replay-атаки (хотя и ограниченное nonce/timestamp) остается избыточно широким. Стандарт безопасности биржи рекомендует recvWindow <= 5000 мс в связке с постоянной NTP-синхронизацией хоста.
        Отсутствие поддержки RSA / Ed25519 API Keys: Binance API поддерживает асимметричные ключи (Ed25519 / RSA). HMAC-SHA256 требует хранения закрытого симметричного секрета на сервере. При компрометации хоста атакующий получает симметричный ключ. Использование Ed25519 защищает приватный ключ в аппаратных модулях/изолированных хранилищах.

4.3. Оценка Defense-in-Depth (Эшелонированная защита)

Система демонстрирует высокий уровень внутренней инженерной культуры защиты капитала (Defensive Programming), однако присутствуют пробелы на системно-сетевом уровне:

Позитивные паттерны (Сильные стороны):

    Invariant Gate & Dust Guard (supervisor/swarm_manager.py): Защита от потери контроля над открытыми биржевыми позициями. Если бот упал, а позиция открыта, супервайзер не дает ее потерять («Heal Guard»).
    Profit Guard & Fuse Gate (core/executor.py, core/profit_budget.py): Ордера на расширение позиции (expansions) блокируются, пока не подтверждена прибыль от закрытий или пока не получены достоверные котировки стакана (financials_confirmed).
    Decimal Precision: Расчеты ведутся в Decimal(prec=28) с режимом ROUND_HALF_EVEN, что исключает атаки на округление (rounding exploitation) и накопление погрешности float.
    Reaper Guard: Автоматическая зачистка зомби-процессов PM2 и рассинхронов.

Слабые звенья (Gaps):

    Отсутствие Hard Kill-Switch на уровне API биржи: Нет ограничения по IP на стороне ключа Binance (IP Access Restriction). В случае утечки ключа атакующий может отправлять ордера с любого IP.
    Отсутствие разделения прав API-ключа (RBAC): Торговому боту нужны права Enable Futures, но категорически должны быть отключены Enable Spot & Margin Trading, Enable Withdrawals (вывод средств) и Enable Universal Transfer.

4.4. Устойчивость к внешним атакам и внутренним сбоям (Resilience & Chaos Engineering)

    Разрыв сетевого соединения / Websocket / REST Drops:
        Декоратор @retry_on_network_error в connector.py обрабатывает повторы (3-5 попыток), но при фатальных сетевых сбоях переходит в исключение. При этом, если отмена лимитного ордера упадет по таймауту, может возникнуть состояние гонки (ордер исполнится на бирже, а бот посчитает его отмененным и пошлет маркет-ордер).
    Race condition при частичном исполнении (Partial Fill Desync):
        В core/executor.py при fallback лимитного ордера опрашивается статус ордера с задержками asyncio.sleep(0.3 - 0.5). При резкой волатильности статус между отменой и фоллбэком может быть не консистентен.
    Целостность IPC и файлового хранилища:
        Сигналы через создание файлов signals/*.flag не атомарны по сравнению с очередями сообщений (IPC / POSIX message queues / Redis) и подвержены коллизиям при конкурентной записи/удалении.

5. Практическая матрица рекомендаций и план устранения
Приоритет 	Область 	Необходимое действие (Remediation)
P0 (Критический) 	Управление секретами (Credentials) 	1. Принудительно установить ограничение по IP (IP Whitelisting) в консоли Binance API для боевых ключей.
2. Проверить права API-ключа: отключить Withdrawals, Spot, Sub-account transfer.
3. Удалить fallback на чтение api_key / secret_key из config.json.
P0 (Критический) 	Безопасность Dashboard 	1. Сменить дефолтный secret_key и пароль в dashboard/config.yaml. Вынести их строго в переменные окружения (DASHBOARD_SECRET_KEY, DASHBOARD_PASSWORD_HASH).
2. Привязать сервис к 127.0.0.1 вместо 0.0.0.0, закрыв доступ из внешней сети; настроить NGINX reverse-proxy с TLS и Basic/mTLS авторизацией.
P1 (Высокий) 	Инъекция параметров в PM2 	В dashboard/dashboard.py валидировать параметр ticker по строгому regex ^[A-Z0-9]{2,15}$ перед конструированием аргументов команд для subprocess.run().
P1 (Высокий) 	Временное окно подписи (recvWindow) 	Снизить REQUEST_RECVWINDOW с 30000 до 5000 мс в core/connector.py. Настроить демон точной синхронизации времени на сервере (Chrony / Windows Time Service NTP).
P2 (Средний) 	Асимметричная криптография 	Перейти с HMAC-SHA256 ключей на асимметричные Ed25519 ключи, поддерживаемые Binance API (приватный ключ защищен правами доступа chmod 600 или TPM).
P2 (Средний) 	Rate Limit Protection 	Внедрить централизованный Leaky Bucket / Token Bucket счетчик веса запросов для Binance API между супервайзером и всеми воркерами во избежание 429/418 бана.
6. Заключение

Архитектура системы обладает зрелой доменной защитой капитала (изоляция рисков, контроль просадок, отслеживание расхождений позиций). Основные уязвимости концентрируются на периметре взаимодействия (Web Dashboard, дефолтные учетные данные, перехват открытого трафика) и инфраструктурном уровне хранения секретов (хранение симметричных ключей, доступность API-ключей дочерним процессам).

Реализация рекомендаций P0/P1 устранит критические векторы компрометации биржевого депозита и переведет систему в класс надежности Enterprise Secure Trading System.