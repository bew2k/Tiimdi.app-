# Tiimdi.app

Geo-aggregator of food deals in Almaty: shows nearby venue promotions on a map, ranks them by "value",
and lets venues add their own deals (cold-start solution). Portfolio project, showcase version.

**Stack:** Python 3.12 · FastAPI · asyncpg (raw SQL) · PostgreSQL 16 + PostGIS · Redis · Docker Compose · Postman · MapLibre GL (vector map, 3D buildings, OpenFreeMap tiles)

**Highlights**
- PostGIS radius search (`ST_DWithin` on a GiST index), ranking formula `0.35·discount + 0.25·cheapness + 0.25·proximity + 0.15·upvotes`
- Redis: versioned response cache, rate limits, one upvote per client per day
- AI layer behind a provider interface (mock / OpenAI-compatible), circuit breaker, daily quota, fallback provider, Pydantic validation of model output; the AI never chooses deals, it only explains the ranked result and reads menu photos
- Single-file frontend: fuzzy search (typos, synonyms, wrong keyboard layout), ru / kk / en, accessibility settings, reviews, saved deals
- Unit tests for ranking, breaker/quota and AI failure scenarios

## Screenshots

<p>
  <img src="docs/screenshots/map.png" width="24%" alt="Map with deals">
  <img src="docs/screenshots/venue.png" width="24%" alt="Venue page">
  <img src="docs/screenshots/filters.png" width="24%" alt="Filters">
  <img src="docs/screenshots/map-3d.png" width="24%" alt="3D map on zoom">
</p>
<p>
  <img src="docs/screenshots/dark-theme.png" width="24%" alt="Dark theme">
</p>

API documentation (Swagger):

![API docs](docs/screenshots/api-docs.png)

**Showcase version:** the real venue dataset, photos and one-click launchers are intentionally not included, so the repo is for reading, not a turnkey demo. Fill `data/*.csv` with your own data to run it: `docker compose up --build`, then http://localhost:8000 (Swagger: `/docs`).

**Authors:** Bek Kadyrbekov ([@Bew2k](https://github.com/Bew2k)) and Zholdas Bekasyl ([@bekassyl08](https://github.com/bekassyl08)).
**License:** [PolyForm Noncommercial 1.0.0](LICENSE). Viewing and noncommercial use are allowed; commercial use requires the authors' written permission.

Documentation below is in Russian.

## Эндпоинты

| Метод | URL | Что делает |
|---|---|---|
| GET | `/deals/nearby?lat&lon&radius&category&max_price&sort&limit` | акции в радиусе (PostGIS `ST_DWithin`), sort = best / price / distance / discount |
| GET | `/deals/trending?lat&lon` | «Trending nearby» — топ по рейтингу выгоды |
| GET | `/recommend?lat&lon&budget&ai=true` | совет «что взять за бюджет»: по правилам, с `ai=true` объяснение от ИИ (поле `advice_source` = `ai` или `rules`) |
| GET | `/deals/{id}` · `/venues/nearby` | карточка акции · заведения в радиусе |
| POST | `/venues` | заведение регистрируется |
| POST | `/deals` | заведение добавляет акцию (лимит 10/мин на клиента, Redis) |
| POST | `/deals/{id}/upvote` | подтвердить цену (1 раз в сутки с клиента, Redis) |
| POST | `/menu/extract` | фото меню → список позиций (ничего не сохраняет; 5 запросов/мин, до 5 МБ) |
| POST/GET | `/venues/{id}/menu` | сохранить / получить меню заведения |
| GET/POST | `/venues/{id}/reviews` | отзывы заведения (оценка 1–5, лимит 5/мин) |
| POST | `/business-requests` | заявка заведения на проверку (лимит 3/час) |
| GET | `/health` · `/ai/status` | статус API, Redis и ИИ (провайдер, сбой, расход за сутки) |

## Поток запроса: поиск и рекомендация

1. Клиент → `GET /deals/nearby (lat, lon, radius)`
2. API → Redis: есть кеш? если да — отдаём сразу (`"cached": true`)
3. Нет → PostGIS: `ST_DWithin` по GiST-индексу (R-Tree) + `ST_Distance` в метрах
4. API ранжирует: `score = 0.35·скидка + 0.25·дешевизна + 0.25·близость + 0.15·апвоуты` (`app/ranking.py`)
5. Любая запись (`POST`) сбрасывает кеш (версия ключа в Redis увеличивается)

## ИИ и что будет, если он недоступен

ИИ **не выбирает** акции: выбор и сортировку делает формула в `app/ranking.py`. ИИ только объясняет
готовый топ-3 (`/recommend?ai=true`) и читает фото меню (`/menu/extract`). Ответ модели считается
недоверенным: он проверяется схемой (Pydantic), мусорные позиции отбрасываются, названия акций в
промпте помечены как данные, а не инструкции.

Настройка: `AI_PROVIDER=mock` (по умолчанию, без ключей) или `openai_compat` (Gemini, OpenRouter, Groq, Ollama...).
Подробности и примеры: `.env.example`. Проверка связи: `docker compose exec api python -m app.ai [фото.jpg]`.

| Ситуация | Что делает система |
|---|---|
| Закончились токены / лимит (HTTP 429) | провайдер отключается на время из `Retry-After` (по умолчанию 5 мин), используется запасной провайдер, если он настроен; иначе совет по правилам |
| ИИ не ответил за `AI_TEXT_TIMEOUT` (8 с) или нет связи | то же; отключение на 2 мин, чтобы следующие запросы не ждали таймаут |
| Неверный ключ (401/403) / сбой сервера (5xx) | отключение на 15 мин / 1 мин, `/ai/status` показывает причину |
| Исчерпан собственный потолок `AI_DAILY_LIMIT` | ИИ не вызывается до следующих суток (UTC), счётчик в Redis |
| Повторный запрос с теми же данными | ответ берётся из Redis (совет 10 мин, меню 24 ч), токены не тратятся |
| Redis недоступен | кеш и лимиты отключаются на 10 с, API работает; дневной лимит считается в памяти процесса |
| ИИ недоступен совсем | `/recommend` отдаёт совет по правилам (`advice_source: "rules"`), для меню — ошибка с понятным текстом и ручной ввод позиций |

## Тесты

```bash
docker compose exec api python -m unittest discover -s tests -v
```
Файлы `tests/test_ranking.py`, `test_guard.py`, `test_ai.py`: формула ранжирования, выключатель, дневной лимит и все сценарии отказа ИИ (без сети и без ключей).
Postman: импортируйте `postman/tiimdi.postman_collection.json` → Collection Runner → Run
(`base_url` = `http://localhost:8000`; запрос №19 с фото запускается вручную).

## Интерфейс

Вкладки: Карта, Рядом (карточки с обложками), Избранное (завершённые акции серые, исчезают через 3 дня), Кабинет
(только для ролей Бизнес/Модератор/Админ), Профиль (уровни, достижения, настройки).
Карточка заведения: обложка, рейтинг, маршрут, акции, меню.
Поиск с автокоррекцией: опечатки («донр» → донер), синонимы (шаверма = шаурма), неверная раскладка («ljyth» → донер).
Настройки: язык (русский, қазақша, English), размер текста, инверсия цветов, режим без цветов, отключение анимаций.
Карточка заведения с вкладками «Акции, Меню, Отзывы, Контакты» (отзывы можно оставлять). Нажми на логотип tiimdi: шутка держится 5 секунд.
Первый запуск: выбор роли «студент / школьник / пользователь / заведение»; роли модератора и админа выбрать нельзя. Вкладка «Настройки»: сверху профиль (по нажатию открывается), прогресс по нажатию открывает достижения. Карта: вдали плоская, при приближении 3D; границы: Алматы и пригороды.
Параметр `?debug` в адресе включает выбор роли в «Редактировать профиль».
Продуктовые решения и роли: `docs/PRODUCT.md`.

## Структура

```
app/        main.py (API) · ai.py (ИИ-провайдеры) · guard.py (выключатель, дневной лимит) · cache.py (Redis)
            db.py (PostGIS, сид из CSV) · ranking.py (формула) · config.py · schema.sql
static/     index.html (всё приложение в одном файле)
data/       venues.csv, deals.csv, menu.csv (только заголовки)      tests/   unittest      postman/   коллекция запросов
```

## Безопасность и ограничения

Учётные данные БД в `docker-compose.yml` и CORS `*` рассчитаны на локальную разработку и перед публичным деплоем меняются.


- Перевод интерфейса: основная часть на трёх языках; кабинет и диалоги пока только на русском.
- Фото заведений: колонка `venues.photo_url` есть, загрузки пока нет (без фото показывается яркая обложка с иконкой).
- **Вход и роли пока только в интерфейсе:** роль и баллы хранятся в `localStorage` браузера, а `POST /deals`,
  `/venues`, `/venues/{id}/menu` на сервере открыты всем. Серверная авторизация (JWT, роли) — следующий шаг.
- Баллы считает браузер, а не сервер, поэтому при смене устройства они сбрасываются.
- Меню заведения показывается в карточке, но не влияет на ранжирование.
- Схема БД создаётся через `CREATE IF NOT EXISTS`, без миграций: после изменения таблиц нужен `docker compose down -v` (до Alembic).
- Если Redis недоступен, лимит «1 апвоут в сутки» не действует.
- Локальный Ollama на CPU медленный: поставьте `AI_TIMEOUT=180` и `AI_TEXT_TIMEOUT=30`.

## Что мок и что дальше

- **Пока нет:** серверная авторизация/роли, модерация (поле `status` в `deals` уже есть), пеший маршрут, ML-ранжирование.
- **Дальше:** JWT и роли, модерация, репутация на сервере (Redis Sorted Set), подписки, ML-ранжирование, тесты в CI, деплой.
