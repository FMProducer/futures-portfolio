Ты работаешь в проекте:

C:\Python\Prosperous_Bot\futures_portfolio

Нужно существенно улучшить интерактивный HTML-дашборд бэктестера:

C:\Python\Prosperous_Bot\futures_portfolio\reports\zec_12m_dashboard.html

Используй как визуальный и функциональный ориентир репозиторий:

file:///c%3A/Python/Prosperous_Bot/t-invest-2-build/reports/dashboard_unified_enhanced.html

Не начинай с написания кода. Сначала составь краткий план после изучения текущей реализации, затем реализуй только необходимые изменения.

Сначала изучи:

- текущий генератор дашборда;
- скрипт запуска бэктеста;
- формат JSON-результатов;
- структуру локальных исторических данных;
- все существующие поля сделок, ребалансировок, equity, PnL, комиссий, funding, drawdown и событий;
- reference-проект и его dashboard-компоненты.

Не меняй торговую стратегию, формулы бэктестера, параметры риска или конфигурацию. Работай только над визуализацией, сбором уже существующих данных и удобством анализа результатов.

Цель: создать информативный, интерактивный, самодостаточный offline HTML-дашборд для анализа одного запуска бэктеста.

Обязательные возможности:

1. Верхняя панель KPI

Покажи:

- период бэктеста и часовой пояс;
- тикер;
- начальный и конечный капитал;
- Net PnL в USDT и процентах;
- Max Drawdown;
- Sortino/Sharpe, если доступны;
- число ребалансировок;
- количество сделок;
- количество BUY/SELL;
- комиссии;
- funding;
- realized PnL;
- unrealized PnL;
- liquidations;
- trailing stops;
- skipped actions;
- средний размер сделки;
- turnover;
- win rate;
- profit factor;
- среднее время между ребалансировками;
- изменение цены базового актива.

Каждую метрику снабди tooltip с коротким объяснением формулы.

2. Главный график цены

Построй интерактивный график цены BTC/USDT или ZEC/USDT:

- свечи OHLC, если доступны;
- иначе line chart close price;
- маркеры каждой сделки;
- разные цвета для BUY и SELL;
- разные формы маркеров для LONG, SHORT и VIRTUAL;
- маркеры trailing stop;
- маркеры liquidation;
- маркеры rebalance;
- hover показывает timestamp, side, leg, qty, price, commission, realized PnL, TPV;
- range selector: 1h, 6h, 1d, 1w, весь период;
- range slider;
- zoom/pan/reset;
- возможность скрывать отдельные серии через legend.

3. График equity

Сделай отдельный интерактивный график:

- total equity/TPV;
- initial capital horizontal line;
- running high-water mark;
- drawdown;
- liquidations и trailing stops на общей временной шкале;
- отдельная панель drawdown в процентах;
- возможность переключить линейную и логарифмическую шкалу;
- hover показывает значение equity, PnL и drawdown;
- корректно обрабатывай очень большие и очень маленькие значения;
- не допускай, чтобы один выброс делал остальные данные невидимыми.

Если equity имеет патологический выброс, добавь переключатель:

- full scale;
- clipped percentile scale;
- log scale.

Не скрывай выброс из данных — только меняй способ отображения.

4. PnL Attribution

Сделай интерактивную панель разложения результата:

- realized PnL;
- unrealized PnL;
- commissions;
- funding;
- slippage;
- reserve/siphoning;
- residual/other;
- net equity PnL.

Покажи:

- cumulative lines;
- stacked area chart;
- итоговые значения;
- таблицу сверки:

Initial capital
+ realized PnL
+ unrealized PnL
- commissions
- funding
- slippage
- reserve/siphoning
= final equity

Добавь проверку баланса и выделяй несоответствие красным цветом.

5. Анализ ребалансировок

Создай отдельную секцию:

- количество ребалансировок во времени;
- объём каждой ребалансировки;
- изменение долей LONG/SHORT/VIRTUAL/CASH;
- target share против actual share;
- surplus/deficit threshold;
- причины срабатывания;
- действия, заблокированные trend guard;
- действия, заблокированные velocity guard;
- skipped expansions;
- cooldown periods.

Сделай график actual share vs target share для каждой ноги.

6. Таблица сделок

Создай полноценную таблицу с:

- timestamp;
- local time и UTC;
- side;
- leg;
- symbol;
- quantity;
- price;
- notional;
- leverage;
- commission;
- funding;
- realized PnL;
- equity/TPV after;
- trigger;
- rebalance id;
- event type.

Таблица должна поддерживать:

- сортировку по любой колонке;
- поиск;
- фильтр BUY/SELL;
- фильтр leg;
- фильтр по диапазону PnL;
- фильтр по времени;
- экспорт CSV;
- отображение количества найденных строк;
- fixed header;
- виртуализацию или разумную оптимизацию для больших таблиц.

7. Фильтры и управление

Добавь панель управления:

- ticker;
- date range;
- event type;
- side;
- leg;
- profitable/loss trades;
- min/max notional;
- min/max PnL;
- show/hide price;
- show/hide trades;
- show/hide stops;
- show/hide liquidations;
- show/hide equity;
- show/hide drawdown.

Добавь кнопки:

- Reset filters;
- Reset zoom;
- Export trades CSV;
- Export visible data JSON;
- Copy summary;
- Toggle dark/light theme.

8. Data quality

Отдельно покажи:

- число входных баров;
- число использованных баров;
- фактический период;
- пропуски timestamp;
- duplicate timestamps;
- NaN/Inf;
- отсутствие сделок;
- отсутствие equity;
- ограниченный лог сделок;
- несоответствие количества баров и цен;
- несоответствие final equity и PnL attribution;
- подозрительные выбросы.

Если результат экономически подозрительный, например equity выросла на несколько порядков, явно показывай предупреждение:

“Visualization is valid, but economic result may be invalid. Inspect equity/PnL accounting.”

Не маскируй такие результаты.

9. Производительность

Дашборд должен:

- работать offline;
- использовать локальный plotly asset;
- не зависеть от CDN;
- открываться из file:///;
- не содержать сетевых запросов;
- не зависать на сотнях тысяч минутных баров;
- использовать downsampling только для линий;
- сохранять все сделки и события;
- не терять маркеры;
- не сериализовать numpy arrays в bdata, если браузерный Plotly не сможет их прочитать;
- все данные перед JSON-сериализацией привести к обычным Python list/float/int.

10. Интеграция

Сохрани один канонический файл:

C:\Python\Prosperous_Bot\futures_portfolio\reports\zec_12m_dashboard.html

При каждом запуске бэктеста этот файл должен обновляться автоматически.

Не создавай версии v2, v3 и дополнительные dashboard HTML-файлы.

Если генератор дашборда запускается отдельным скриптом, интегрируй его в существующий pipeline запуска бэктеста.

Проверь, что:

python backtest/backtest_rebalance.py ...

после завершения:

- пишет JSON-результаты;
- обновляет один HTML;
- HTML открывается через file:/// без сервера;
- интерактивные графики работают;
- таблица сделок работает;
- маркеры отображаются;
- даты не схлопываются в 1969/1970;
- графики соответствуют фактическому окну бэктеста.

11. Проверки

После изменений выполни:

- запуск бэктеста на существующих локальных данных;
- генерацию HTML;
- проверку HTML через headless Chromium/Playwright;
- проверку отсутствия JavaScript errors;
- проверку наличия Plotly SVG/canvas для всех графиков;
- проверку количества marker points;
- проверку начала и конца временной шкалы;
- проверку, что количество строк таблицы соответствует числу сделок;
- проверку экспорта CSV;
- проверку отсутствия дополнительных HTML-дубликатов.

Не меняй config.json без необходимости.

Не исправляй торговую механику ради красивых цифр.

В финальном отчёте укажи:

- какие файлы изменены;
- какие графики добавлены;
- какие фильтры добавлены;
- сколько сделок отображается;
- какой период отображается;
- какие проверки прошли;
- какие ограничения остались.