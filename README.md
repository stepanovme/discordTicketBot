# Discord Application Bot

Бот для обработки заявок на Discord сервере.

## Установка

1. Установите Python 3.8 или выше
2. Установите зависимости:
```bash
pip install -r requirements.txt
```

3. Создайте файл `.env` и заполните его следующими данными:
```
DISCORD_TOKEN=your_bot_token_here
CATEGORY_ID=your_category_id_here
ADMIN_ROLES=["Администратор", "Модератор"]
MYSQL_HOST=your_ip_mysql
MYSQL_PORT=your_port_mysql
MYSQL_USER=your_user_mysql
MYSQL_PASSWORD=your_password_mysql
MYSQL_DB=your_DB_whitelist_for_bot
ARCHIVE_CHANNEL_ID=channel_id_for_saved_ticket
ACCEPT_ROLE=name_group_palyer
REJECT_ROLE=name_group_reject
MYSQL_LP_DB=your_DB_LuckyPerms
```

## Настройка

1. Создайте бота на [Discord Developer Portal](https://discord.com/developers/applications)
2. Получите токен бота и добавьте его в файл `.env`
3. Укажите ID категории, где будут создаваться каналы для заявок
4. Укажите названия ролей администраторов в формате JSON массива
5. Заполните отсальные данные в файле `.env`

## Использование

1. Запустите бота:
```bash
python main.py
```

2. В нужном канале используйте команду `/setupticketbot` для создания кнопки "Создать заявку"
3. Пользователи могут нажать на кнопку для создания заявки
4. Бот создаст приватный канал и начнет задавать вопросы
5. После ответа на все вопросы, администраторы смогут принять, отклонить или запросить дополнительную информацию

## Функциональность

- Создание приватных каналов для заявок
- Пошаговый опрос пользователей
- Кнопки для ответов
- Система модерации заявок
- Защита от спама (один пользователь может иметь только одну активную заявку) 