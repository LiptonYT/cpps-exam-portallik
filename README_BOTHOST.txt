LIPTON УВОЛЬНЕНИЕ — BOTHOST

ВАЖНО: В этом ZIP файлы находятся прямо в корне архива. Не загружайте архив, распакованный в дополнительную папку.

В Bothost выберите Dockerfile / Docker запуск. В корне проекта должны лежать:
- Dockerfile
- bot.py
- requirements.txt

Системный токен Bothost задаётся во вкладке «Токен» и доступен процессу как BOT_TOKEN.
Код сначала читает BOT_TOKEN, затем DISCORD_TOKEN и DISCORD_BOT_TOKEN.
Не создавайте вручную BOT_TOKEN, если Bothost создаёт её автоматически.
Удалите старые пользовательские DISCORD_TOKEN и DISCORD_BOT_TOKEN, чтобы не было путаницы.

Пользовательские переменные Bothost:
GUILD_ID=1533075462383730838
REVIEW_CHANNEL_ID=1533076154783764663
TERMINATED_ROLE_ID=1533075835097976893
REVIEWER_ROLE_IDS=1533075692785504327
DB_PATH=/app/data/terminations.sqlite3

Проверьте, что роль бота выше ролей сотрудников и роли «Уволен» и у бота есть Manage Roles, Manage Nicknames, View Channels, Send Messages, Embed Links, Read Message History.
В Discord Developer Portal → Bot включите Server Members Intent.

Команда: /setup_resignation
Дальнейшая подача и рассмотрение заявлений выполняются кнопками и модальными формами.

Если появляется 401 Unauthorized — Bothost передаёт недействительный токен. Сбросьте Bot Token в Discord Developer Portal → Bot → Reset Token, вставьте новый во вкладке «Токен» Bothost и перезапустите. Никому не отправляйте токен.
