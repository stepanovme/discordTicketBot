import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import json
import asyncio
import aiohttp
import io
import aiomysql
from datetime import datetime
import re

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
CATEGORY_ID = int(os.getenv('CATEGORY_ID'))
ADMIN_ROLES = json.loads(os.getenv('ADMIN_ROLES'))
ARCHIVE_CHANNEL_ID = int(os.getenv('ARCHIVE_CHANNEL_ID'))
MYSQL_LP_DB = os.getenv('MYSQL_LP_DB')
ACCEPT_ROLE = os.getenv('ACCEPT_ROLE')
REJECT_ROLE = os.getenv('REJECT_ROLE')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

QUESTIONS = [
    "Ваш возраст. (полных лет)",
    "Ваш ник. (Нужен для добавления в WhiteList. Желательно указывать в таких кавычках: ``)",
    "Сколько уже играете в Minecraft?",
    "Расскажите немного о себе. (игровые цели/опыт игры, умения, навыки, планы на сервер и т.п.) [подробно]",
    "Ознакомлены ли вы с правилами сервера? Есть ли вопросы или уточнения? (при необходимости задавайте вопросы)",
    "Как узнали про наш сервер? (укажите ссылку на сайт, стримера или видео / человека, который вас пригласил - @ник)",
    "Почему выбрали наш сервер? [подробно]",
    "Играли ли вы на подобных серверах? Если да, то на каких именно?",
    "Есть ли у вас при необходимости возможность пойти в голосовой канал с администратором?"
]

emoji_pattern = r"<(a?):(\w+):(\d+)>"

def replace_emoji(match):
    is_animated = match.group(1)
    emoji_name = match.group(2)
    emoji_id = match.group(3)
    extension = 'gif' if is_animated else 'png'
    emoji_url = f'https://cdn.discordapp.com/emojis/{emoji_id}.{extension}'
    return f"<img src='{emoji_url}' alt=':{emoji_name}:' style='vertical-align: middle; height: 1em;'>"

class ApplicationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Создать заявку", style=discord.ButtonStyle.primary, custom_id="create_application"))

class ApplicationResponseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Отправить", style=discord.ButtonStyle.primary, custom_id="send_response"))

class ApplicationReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Принять", style=discord.ButtonStyle.success, custom_id="accept"))
        self.add_item(discord.ui.Button(label="Отклонить", style=discord.ButtonStyle.danger, custom_id="reject"))
        self.add_item(discord.ui.Button(label="Попросить дополнить", style=discord.ButtonStyle.secondary, custom_id="request_more"))
        self.add_item(discord.ui.Button(label="Закрыть заявку", style=discord.ButtonStyle.grey, custom_id="close_application"))

class RequestMoreModal(discord.ui.Modal, title="Попросить дополнить"):
    questions = discord.ui.TextInput(
        label="Вопросы (номера через запятую)",
        placeholder="Например: 4, 7"
    )
    explanation = discord.ui.TextInput(
        label="Пояснение",
        style=discord.TextStyle.paragraph,
        required=False
    )
    async def on_submit(self, interaction: discord.Interaction):
        question_numbers = [int(num.strip()) for num in self.questions.value.split(',') if num.strip().isdigit()]
        explanation = self.explanation.value
        application = next((app for app in active_applications if app.channel == interaction.channel), None)
        if application:
            await application.request_more(interaction.user, question_numbers, explanation)
        await interaction.response.send_message("Запрос на дополнение отправлен!", ephemeral=True)

class Application:
    def __init__(self, user, channel):
        self.user = user
        self.channel = channel
        self.responses = []
        self.current_question = 0
        self.collecting_response = False
        self.temp_messages = []

    async def start(self):
        await self.ask_question()

    async def ask_question(self):
        if self.current_question < len(QUESTIONS):
            embed = discord.Embed(
                title=f"Вопрос {self.current_question + 1} из {len(QUESTIONS)}",
                description=QUESTIONS[self.current_question],
                color=discord.Color.blue()
            )
            embed.set_footer(text="Отправьте ваш ответ в чат и нажмите кнопку 'Отправить'")
            
            view = ApplicationResponseView()
            await self.channel.send(embed=embed, view=view)
            self.collecting_response = True
        else:
            await self.show_summary()

    def get_media_url(self, attachment):
        return attachment.url.replace('cdn.discordapp.com', 'media.discordapp.net')

    async def add_response(self, messages):
        response_parts = []
        files = []
        for message in messages:
            if message.content:
                response_parts.append(message.content)
            for attachment in message.attachments:
                async with aiohttp.ClientSession() as session:
                    async with session.get(attachment.url) as resp:
                        if resp.status == 200:
                            data = io.BytesIO(await resp.read())
                            data.seek(0)
                            files.append({"filename": attachment.filename, "data": data})
                file_link = f"[{attachment.filename}](файл будет приложен ниже)"
                response_parts.append(file_link)
            if message.attachments and any(att.content_type and att.content_type.startswith('audio/') for att in message.attachments):
                response_parts.append("[Голосовое сообщение]")
        full_response = "\n".join(response_parts)
        self.responses.append({"text": full_response, "files": files, "messages": messages})
        self.current_question += 1
        self.temp_messages = []
        self.collecting_response = False
        await self.channel.purge(limit=None)
        await self.ask_question()

    async def show_summary(self):
        application_number = 0
        async with mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT application_number FROM whitelist WHERE channel_id=%s", (self.channel.id,))
                result = await cur.fetchone()
                if result:
                    application_number = result[0]

        embed = discord.Embed(
            title=f"Анкета",
            description="Результаты заполнения анкеты",
            color=discord.Color.green()
        )

        nickname_raw = self.responses[1]["text"]
        nickname_cleaned = nickname_raw.strip().replace('`', '')

        avatar_url = f"https://minotar.net/avatar/{nickname_cleaned}/100"

        embed.set_thumbnail(url=avatar_url)

        view = ApplicationReviewView()
        all_files = []
        for i, (question, response) in enumerate(zip(QUESTIONS, self.responses), 1):
            text = response["text"]
            if len(text) > 1024:
                chunks = [text[i:i+1024] for i in range(0, len(text), 1024)]
                for j, chunk in enumerate(chunks):
                    embed.add_field(
                        name=f"{i}. {question}" if j == 0 else f"Продолжение ответа {i}",
                        value=chunk,
                        inline=False
                    )
            else:
                embed.add_field(
                    name=f"{i}. {question}",
                    value=text if text else "[нет ответа]",
                    inline=False
                )
            if "files" in response:
                all_files.extend(response["files"])
        await self.channel.send(embed=embed, view=view)
        # Отправляем все файлы как attachments
        for file in all_files:
            file['data'].seek(0)
            await self.channel.send(file=discord.File(file['data'], file['filename']))

    async def request_more(self, moderator, question_numbers, explanation):
        self.additional_questions = question_numbers
        self.additional_answers = {}
        self.additional_explanation = explanation
        questions_text = "\n".join([f"{num}. {QUESTIONS[num-1]}" for num in question_numbers])
        msg = (
            f"{self.user.mention}, модератор {moderator.mention} просит дополнить ответы на следующие вопросы:\n"
            f"{questions_text}\n"
            f"Пояснение: {explanation}\n"
            "Пожалуйста, отправьте ваши дополнения по очереди на каждый вопрос. После каждого ответа нажимайте кнопку 'Отправить'."
        )
        await self.channel.send(msg)
        self.current_additional_index = 0
        await self.ask_additional_question()

    async def ask_additional_question(self):
        if self.current_additional_index < len(self.additional_questions):
            q_num = self.additional_questions[self.current_additional_index]
            embed = discord.Embed(
                title=f"Дополнение к вопросу {q_num}",
                description=QUESTIONS[q_num-1],
                color=discord.Color.orange()
            )
            view = ApplicationResponseView()
            await self.channel.send(embed=embed, view=view)
            self.collecting_response = True
        else:
            await self.save_additional_answers()

    async def add_additional_response(self, messages):
        response_parts = []
        files = []
        for message in messages:
            if message.content:
                response_parts.append(message.content)
            for attachment in message.attachments:
                async with aiohttp.ClientSession() as session:
                    async with session.get(attachment.url) as resp:
                        if resp.status == 200:
                            data = io.BytesIO(await resp.read())
                            data.seek(0)
                            files.append({"filename": attachment.filename, "data": data})
                file_link = f"[{attachment.filename}](файл будет приложен ниже)"
                response_parts.append(file_link)
            if message.attachments and any(att.content_type and att.content_type.startswith('audio/') for att in message.attachments):
                response_parts.append("[Голосовое сообщение]")
        full_response = "\n".join(response_parts)
        q_num = self.additional_questions[self.current_additional_index]
        self.additional_answers[q_num] = {"text": full_response, "files": files, "messages": messages}
        self.current_additional_index += 1
        self.temp_messages = []
        self.collecting_response = False
        await self.channel.purge(limit=None)
        await self.ask_additional_question()

    async def save_additional_answers(self):
        for q_num, answer in self.additional_answers.items():
            self.responses[q_num-1]['text'] += f"\n**Дополнение:**\n{answer['text']}"
            self.responses[q_num-1]['files'].extend(answer['files'])
        await self.show_summary()

class RejectReasonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.reason = None
        self.set_role = None
        self.reason_select = discord.ui.Select(
            placeholder="Причина отказа",
            options=[
                discord.SelectOption(label="Слабая заявка", value="слабая заявка"),
                discord.SelectOption(label="Недопустимый возраст", value="недопустимый возраст"),
                discord.SelectOption(label="Другое", value="другое"),
            ]
        )
        self.set_role_select = discord.ui.Select(
            placeholder="Ставить роль?",
            options=[
                discord.SelectOption(label="Да", value="да"),
                discord.SelectOption(label="Нет", value="нет"),
            ]
        )
        self.reason_select.callback = self.reason_callback
        self.set_role_select.callback = self.set_role_callback
        self.add_item(self.reason_select)
        self.add_item(self.set_role_select)
        self.add_item(RejectNextButton(self))

    async def reason_callback(self, interaction: discord.Interaction):
        self.reason = self.reason_select.values[0]
        await interaction.response.defer()

    async def set_role_callback(self, interaction: discord.Interaction):
        self.set_role = self.set_role_select.values[0]
        await interaction.response.defer()

class RejectNextButton(discord.ui.Button):
    def __init__(self, view):
        super().__init__(label="Далее", style=discord.ButtonStyle.primary)
        self.reject_view = view

    async def callback(self, interaction: discord.Interaction):
        if not self.reject_view.reason or not self.reject_view.set_role:
            await interaction.response.send_message("Пожалуйста, выберите причину и вариант роли.", ephemeral=True)
            return

        if self.reject_view.reason == "другое":
            modal = RejectDetailsCustomReasonModal(self.reject_view.reason, self.reject_view.set_role)
        else:
            modal = RejectDetailsModal(self.reject_view.reason, self.reject_view.set_role)

        await interaction.response.send_modal(modal)

class RejectDetailsCustomReasonModal(discord.ui.Modal, title="Отклонить заявку - другая причина"):
    def __init__(self, reason, set_role):
        super().__init__()
        self.reason = reason
        self.set_role = set_role

        self.custom_reason = discord.ui.TextInput(
            label="Укажите конкретную причину отказа",
            placeholder="Например: Недостаточно информации о себе"
        )
        self.details = discord.ui.TextInput(
            label="Подробное пояснение",
            style=discord.TextStyle.paragraph,
            required=False
        )
        self.nickname = discord.ui.TextInput(
            label="Ник игрока",
            placeholder="Введите ник игрока"
        )
        self.add_item(self.custom_reason)
        self.add_item(self.details)
        self.add_item(self.nickname)

    async def on_submit(self, interaction: discord.Interaction):
        custom_reason_text = self.custom_reason.value
        details = self.details.value
        nickname = self.nickname.value
        action = "rejected" if self.set_role == "да" else "temporary_failure"
        now = datetime.now()

        async with mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE whitelist SET action=%s, role_datetime=%s, nickname=%s WHERE channel_id=%s",
                    (action, now, nickname, interaction.channel.id)
                )

        embed = discord.Embed(
            title="Ваша заявка отклонена",
            description=f"Причина: Другое ({custom_reason_text})\n{details}\nНик: {nickname}",
            color=discord.Color.red()
        )
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("Заявка успешно отклонена!", ephemeral=True)

class RejectDetailsModal(discord.ui.Modal, title="Отклонить заявку - подробная причина"):
    def __init__(self, reason, set_role):
        super().__init__()
        self.reason = reason
        self.set_role = set_role
        self.details = discord.ui.TextInput(
            label="Подробная причина",
            style=discord.TextStyle.paragraph,
            required=False
        )
        self.nickname = discord.ui.TextInput(
            label="Ник игрока",
            placeholder="Введите ник игрока"
        )
        self.add_item(self.details)
        self.add_item(self.nickname)

    async def on_submit(self, interaction: discord.Interaction):
        details = self.details.value
        nickname = self.nickname.value
        action = "rejected" if self.set_role == "да" else "temporary_failure"
        now = datetime.now()
        async with mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE whitelist SET action=%s, role_datetime=%s, nickname=%s WHERE channel_id=%s",
                    (action, now, nickname, interaction.channel.id)
                )
        embed = discord.Embed(
            title="Ваша заявка отклонена",
            description=f"Причина: {self.reason}\n{details}\nНик: {nickname}",
            color=discord.Color.red()
        )
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("Заявка успешно отклонена!", ephemeral=True)

class AcceptModal(discord.ui.Modal, title="Принять заявку"):
    nickname = discord.ui.TextInput(
        label="Ник игрока",
        placeholder="Введите ник игрока"
    )

    async def on_submit(self, interaction: discord.Interaction):
        nickname = self.nickname.value
        now = datetime.now()
        async with mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE whitelist SET action=%s, nickname=%s, role_datetime=%s WHERE username=%s",
                    ("accept", nickname, now, str(interaction.user))
                )
        embed = discord.Embed(
            title="Заявка принята",
            description=f"Ник: {nickname}",
            color=discord.Color.green()
        )
        await interaction.channel.send(embed=embed)
        try:
            await interaction.user.send(
                embed=discord.Embed(
                    title="Ваша заявка принята!",
                    description=f"Поздравляем! Ваш ник: {nickname}",
                    color=discord.Color.green()
                )
            )
        except Exception:
            pass
        await interaction.response.send_message("Заявка успешно принята!", ephemeral=True)

mysql_pool = None
mysql_lp_pool = None

async def init_mysql():
    global mysql_pool
    mysql_pool = await aiomysql.create_pool(
        host=os.getenv('MYSQL_HOST'),
        port=int(os.getenv('MYSQL_PORT')),
        user=os.getenv('MYSQL_USER'),
        password=os.getenv('MYSQL_PASSWORD'),
        db=os.getenv('MYSQL_DB'),
        autocommit=True
    )
    async with mysql_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS whitelist (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(100) UNIQUE,
                    action VARCHAR(32) DEFAULT 'none',
                    create_datetime DATETIME,
                    role_datetime DATETIME,
                    nickname VARCHAR(100),
                    channel_id BIGINT,
                    `join` INT DEFAULT 0,
                    application_number INT DEFAULT 0
                )
            """)
            print("MySQL whitelist table checked/created successfully!")

            await cur.execute(
                """CREATE TABLE IF NOT EXISTS application_counter (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    last_number INT DEFAULT 0
                )
                """
            )
            print("MySQL application_counter table checked/created successfully!")

            await cur.execute("SELECT COUNT(*) FROM application_counter")
            count = await cur.fetchone()
            if count[0] == 0:
                await cur.execute("INSERT INTO application_counter (last_number) VALUES (0)")
                await conn.commit()
                print("Application counter initialized.")

async def init_mysql_lp():
    global mysql_lp_pool
    mysql_lp_pool = await aiomysql.create_pool(
        host=os.getenv('MYSQL_HOST'),
        port=int(os.getenv('MYSQL_PORT')),
        user=os.getenv('MYSQL_USER'),
        password=os.getenv('MYSQL_PASSWORD'),
        db=MYSQL_LP_DB,
        autocommit=True
    )

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    await init_mysql()
    await init_mysql_lp()
    bot.loop.create_task(check_accepted_users())
    bot.loop.create_task(check_rejected_users())
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(e)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.channel.name.startswith("заявка-"):
        application = next((app for app in active_applications if app.channel == message.channel), None)
        if application and application.collecting_response:
            application.temp_messages.append(message)

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data.get('custom_id')
        if custom_id == "create_application":
            await interaction.response.send_message("Создаю заявку...", ephemeral=True)
            
            async with mysql_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT id FROM whitelist WHERE username=%s AND action=%s", (str(interaction.user), 'none'))
                    exists = await cur.fetchone()
                    if exists:
                        await interaction.followup.send("У вас уже есть активная заявка!", ephemeral=True)
                        return
                    
                    await cur.execute("SELECT last_number FROM application_counter WHERE id=1")
                    result = await cur.fetchone()
                    current_application_number = result[0] if result else 0
                    
                    new_application_number = current_application_number + 1
                    
                    if result:
                        await cur.execute("UPDATE application_counter SET last_number=%s WHERE id=1", (new_application_number,))
                    else:
                        await cur.execute("INSERT INTO application_counter (id, last_number) VALUES (1, %s)", (new_application_number,))
                    
                    await cur.execute(
                        "INSERT INTO whitelist (username, action, create_datetime, channel_id, application_number) VALUES (%s, %s, %s, %s, %s)",
                        (str(interaction.user), 'none', datetime.now(), interaction.channel.id, new_application_number)
                    )
            
            channel_name = f"заявка-{new_application_number:04d}"
            category = bot.get_channel(CATEGORY_ID)
            
            if not category:
                print(f"Error: Category with ID {CATEGORY_ID} not found.")
                await interaction.followup.send("Произошла ошибка при создании заявки: категория не найдена.", ephemeral=True)
                return

            channel = await category.create_text_channel(
                channel_name,
                overwrites={
                    interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                    interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
                }
            )
            for role_name in ADMIN_ROLES:
                role = discord.utils.get(interaction.guild.roles, name=role_name)
                if role:
                    await channel.set_permissions(role, read_messages=True, send_messages=True)
            
            application = Application(interaction.user, channel)
            active_applications.append(application)
            
            await application.start()
            
            await interaction.followup.send(f"Заявка #{new_application_number} создана в канале {channel.mention}", ephemeral=True)

        elif custom_id == "send_response":
            application = next((app for app in active_applications if app.channel == interaction.channel), None)
            if not application or not application.collecting_response:
                await interaction.response.send_message("Ошибка: заявка не найдена или не ожидает ответа", ephemeral=True)
                return
            if not application.temp_messages:
                await interaction.response.send_message("Пожалуйста, отправьте хотя бы одно сообщение перед нажатием кнопки 'Отправить'", ephemeral=True)
                return
            await interaction.response.defer()
            try:
                if hasattr(application, 'additional_questions') and application.additional_questions and getattr(application, 'current_additional_index', 0) < len(application.additional_questions):
                    await application.add_additional_response(application.temp_messages)
                else:
                    await application.add_response(application.temp_messages)
            except Exception as e:
                await interaction.followup.send(f"Произошла ошибка при обработке ответа: {str(e)}", ephemeral=True)

        elif custom_id == "request_more":
            has_permission = False
            for role in interaction.user.roles:
                if role.name in ADMIN_ROLES:
                    has_permission = True
                    break
            if not has_permission:
                await interaction.response.send_message("У вас нет прав для выполнения этого действия!", ephemeral=True)
                return
            modal = RequestMoreModal()
            await interaction.response.send_modal(modal)

        elif custom_id == "accept":
            has_permission = False
            for role in interaction.user.roles:
                if role.name in ADMIN_ROLES:
                    has_permission = True
                    break
            if not has_permission:
                await interaction.response.send_message("У вас нет прав для выполнения этого действия!", ephemeral=True)
                return
            modal = AcceptModal()
            await interaction.response.send_modal(modal)
        elif custom_id == "reject":
            has_permission = False
            for role in interaction.user.roles:
                if role.name in ADMIN_ROLES:
                    has_permission = True
                    break
            if not has_permission:
                await interaction.response.send_message("У вас нет прав для выполнения этого действия!", ephemeral=True)
                return
            view = RejectReasonView()
            await interaction.response.send_message("Выберите причину отказа и вариант роли:", view=view, ephemeral=True)
        elif custom_id == "close_application":
            has_permission = False
            for role in interaction.user.roles:
                if role.name in ADMIN_ROLES:
                    has_permission = True
                    break
            if not has_permission:
                await interaction.response.send_message("У вас нет прав для выполнения этого действия!", ephemeral=True)
                return

            await interaction.response.defer()
            channel = interaction.channel
            
            application = next((app for app in active_applications if app.channel == channel), None)

            if application:
                applicant_user = application.user
                applicant_username_str = str(applicant_user)
                applicant_id = applicant_user.id
                nickname_from_db = "Неизвестно"
                async with mysql_pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute("SELECT nickname FROM whitelist WHERE username=%s", (applicant_username_str,))
                        result = await cur.fetchone()
                        if result:
                            nickname_from_db = result[0]

            else:
                applicant_user = None
                nickname_from_db = "Неизвестно"
                applicant_username_str = "Неизвестно"
                applicant_id = "Неизвестно"
                async with mysql_pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute("SELECT username, nickname FROM whitelist WHERE channel_id=%s", (channel.id,))
                        result = await cur.fetchone()
                        if result:
                            applicant_username_str = result[0]
                            nickname_from_db = result[1]
                            try:
                                applicant_user = await bot.fetch_user(int(applicant_username_str.split('#')[-1]) if '#' in applicant_username_str else int(applicant_username_str))
                                applicant_id = applicant_user.id
                            except:
                                pass

                if applicant_user is None and applicant_username_str == "Неизвестно":
                    await interaction.followup.send("Не удалось найти информацию о заявке в памяти или БД.", ephemeral=True)
                    return

            closer_username_str = str(interaction.user)

            html_content = f"""<!DOCTYPE html>
<html>
<head>
<title>Чат заявки: {channel.name}</title>
<style>
body {{ font-family: sans-serif; background-color: #36393f; color: #dcddde; }}
.message {{ display: flex; margin-bottom: 10px; padding: 5px; background-color: #303338; border-radius: 5px; }}
.avatar {{ width: 40px; height: 40px; border-radius: 50%; margin-right: 10px; }}\n.message-content {{ flex-grow: 1; }}\n.author {{ font-weight: bold; color: #ffffff; }}\n.timestamp {{ font-size: 0.8em; color: #72767d; margin-left: 10px; }}\n.content {{ margin-top: 5px; white-space: pre-wrap; }}\n.embed {{ border-left: 4px solid #7289da; padding: 10px; margin-top: 10px; background-color: #2c2f33; }}\n.embed-title {{ font-weight: bold; color: #ffffff; margin-bottom: 5px; }}\n.embed-description {{ font-size: 0.9em; color: #dcddde; margin-bottom: 5px; }}\n.embed-field {{ margin-bottom: 5px; }}\n.embed-field-name {{ font-weight: bold; color: #ffffff; }}\n.embed-field-value {{ font-size: 0.9em; color: #dcddde; }}\n.attachment {{ display: block; margin-top: 5px; color: #00b0f4; text-decoration: none; }}\n</style>
</head>
<body>
<h1>Чат заявки: {channel.name}</h1>
"""

            async for message in channel.history(limit=None, oldest_first=True):
                html_content += "<div class='message'>"
                if message.author:
                    avatar_url = message.author.avatar.url if message.author.avatar else message.author.default_avatar.url
                    author_display_name = message.author.display_name
                else:
                    avatar_url = ""
                    author_display_name = "Неизвестный пользователь"

                html_content += f"<img class='avatar' src='{avatar_url}' alt='{author_display_name}'>"
                html_content += "<div class='message-content'>"
                html_content += f"<span class='author'>{author_display_name}</span><span class='timestamp'>{message.created_at.strftime('%Y-%m-%d %H:%M:%S')}</span>"

                if message.content:
                    processed_content = re.sub(emoji_pattern, replace_emoji, message.content)
                    html_content += f"<p class='content'>{processed_content}</p>"

                for embed in message.embeds:
                    html_content += "<div class='embed'>"
                    if embed.title:
                        processed_title = re.sub(emoji_pattern, replace_emoji, embed.title)
                        html_content += f"<div class='embed-title'>{processed_title}</div>"
                    if embed.description:
                        processed_description = re.sub(emoji_pattern, replace_emoji, embed.description)
                        html_content += f"<div class='embed-description'>{processed_description}</div>"
                    for field in embed.fields:
                        html_content += "<div class='embed-field'>"
                        processed_field_name = re.sub(emoji_pattern, replace_emoji, field.name)
                        processed_field_value = re.sub(emoji_pattern, replace_emoji, field.value)
                        html_content += f"<div class='embed-field-name'>{processed_field_name}</div>"
                        html_content += f"<div class='embed-field-value'>{processed_field_value}</div>"
                        html_content += "</div>"
                    html_content += "</div>"

                for attachment in message.attachments:
                    html_content += f"<a href='{attachment.url}' class='attachment'>{attachment.filename}</a>"

                html_content += "</div></div>"

            html_content += """
</body>
</html>
"""

            filename = f"{channel.name}.html"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(html_content)

            archive_channel = bot.get_channel(ARCHIVE_CHANNEL_ID)
            if archive_channel:
                info_message = (
                    f"**Заявка закрыта:** {channel.name}\n"
                    f"Ник игрока: {nickname_from_db}\n"
                    f"Пользователь дискорда: {applicant_username_str}\n"
                    f"ID пользователя дискорда: {applicant_id}\n"
                    f"Кто закрыл заявку: {closer_username_str}"
                )
                await archive_channel.send(info_message)
                
                with open(filename, "rb") as f:
                    await archive_channel.send(file=discord.File(f, filename=filename))

            os.remove(filename)

            active_applications[:] = [app for app in active_applications if app.channel != channel]

            await channel.delete()

            await interaction.followup.send("Заявка закрыта и заархивирована!", ephemeral=True)

async def check_accepted_users():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(10)
        try:
            async with mysql_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT username, nickname FROM whitelist WHERE action=%s AND `join`=%s", ('accept', 0))
                    users_to_process = await cur.fetchall()

            for user_data in users_to_process:
                username = user_data[0]
                nickname = user_data[1].lower()

                if not nickname or nickname == "неизвестно":
                    print(f"Skipping user {username}: nickname not specified or is 'неизвестно'")
                    continue

                lp_inserted = False
                async with mysql_lp_pool.acquire() as lp_conn:
                    async with lp_conn.cursor() as lp_cur:
                        await lp_cur.execute("SELECT uuid FROM luckperms_players WHERE username=%s", (nickname,))
                        lp_user = await lp_cur.fetchone()

                        if lp_user:
                            user_uuid = lp_user[0]
                            permission_value = f"group.{ACCEPT_ROLE}"
                            try:
                                await lp_cur.execute(
                                    "INSERT INTO luckperms_user_permissions (uuid, permission, value, server, world, expiry, contexts) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                                    (user_uuid, permission_value, 1, 'global', 'global', 0, '{}')
                                )
                                lp_inserted = True
                                print(f"Inserted permission '{permission_value}' for user {username} (UUID: {user_uuid})")
                            except aiomysql.IntegrityError:
                                lp_inserted = True
                                print(f"Permission '{permission_value}' already exists for user {username} (UUID: {user_uuid})")
                            except Exception as e:
                                print(f"Error inserting permission for user {username} (UUID: {user_uuid}): {e}")
                        else:
                             print(f"User {nickname} not found in LuckyPerms DB.")

                if lp_inserted:
                    async with mysql_pool.acquire() as conn:
                        async with conn.cursor() as cur:
                            await cur.execute("UPDATE whitelist SET `join`=%s WHERE username=%s", (1, username))
                            print(f"Updated join status for user {username}")

        except Exception as e:
            print(f"Error in check_accepted_users: {e}")

async def check_rejected_users():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(10)
        try:
            async with mysql_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT username, nickname FROM whitelist WHERE action=%s AND `join`=%s", ('rejected', 0))
                    users_to_process = await cur.fetchall()

            for user_data in users_to_process:
                username = user_data[0]
                nickname = user_data[1].lower()

                if not nickname or nickname == "неизвестно":
                    print(f"Skipping rejected user {username}: nickname not specified or is 'неизвестно'")
                    continue

                lp_inserted = False
                async with mysql_lp_pool.acquire() as lp_conn:
                    async with lp_conn.cursor() as lp_cur:
                        await lp_cur.execute("SELECT uuid FROM luckperms_players WHERE username=%s", (nickname,))
                        lp_user = await lp_cur.fetchone()

                        if lp_user:
                            user_uuid = lp_user[0]
                            permission_value = f"group.{REJECT_ROLE}"
                            try:
                                await lp_cur.execute(
                                    "INSERT INTO luckperms_user_permissions (uuid, permission, value, server, world, expiry, contexts) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                                    (user_uuid, permission_value, 1, 'global', 'global', 0, '{}')
                                )
                                lp_inserted = True
                                print(f"Inserted rejected permission '{permission_value}' for user {username} (UUID: {user_uuid})")
                            except aiomysql.IntegrityError:
                                lp_inserted = True
                                print(f"Rejected permission '{permission_value}' already exists for user {username} (UUID: {user_uuid})")
                            except Exception as e:
                                print(f"Error inserting rejected permission for user {username} (UUID: {user_uuid}): {e}")
                        else:
                             print(f"Rejected user {nickname} not found in LuckyPerms DB.")

                if lp_inserted:
                    async with mysql_pool.acquire() as conn:
                        async with conn.cursor() as cur:
                            await cur.execute("UPDATE whitelist SET `join`=%s WHERE username=%s", (1, username))
                            print(f"Updated join status for rejected user {username}")

        except Exception as e:
            print(f"Error in check_rejected_users: {e}")

active_applications = []

@bot.tree.command(name='setupticketbot', description='Настраивает систему заявок (только для администраторов)')
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def setupticketbot(interaction: discord.Interaction):
    print("Setting up application system via slash command...")
    await interaction.response.defer(ephemeral=True) 

    current_channel = interaction.channel
    
    application_message = None
    async for msg in current_channel.history(limit=10):
        if msg.author == bot.user and "Нажмите кнопку ниже, чтобы создать заявку." in msg.content:
            application_message = msg
            break
    else:
        application_message = None

    embed = discord.Embed(
        title="Система заявок",
        description="Нажмите кнопку ниже, чтобы создать заявку.",
        color=discord.Color.blue()
    )
    view = ApplicationView()

    if application_message:
        await application_message.edit(embed=embed, view=view)
    else:
        await current_channel.send(embed=embed, view=view)

    await interaction.followup.send("Система заявок настроена в текущем канале!", ephemeral=True)

bot.run(TOKEN)
