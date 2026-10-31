import os
import re
import logging
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

# ============================================================
# CONFIGURATION
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

GUILD_ID = 1533075462383730838
REVIEW_CHANNEL_ID = 1533076154783764663
FIRED_ROLE_ID = 1533075835097976893
SENIOR_ROLE_ID = 1533075692785504327
DB_PATH = os.getenv("DB_PATH", "resignations.sqlite3")

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing. Add the bot token as the DISCORD_TOKEN "
        "environment variable in your hosting panel."
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("lipton-resignation")

# Members intent is required to fetch members and change their roles.
intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ============================================================
# HELPERS
# ============================================================

def has_reviewer_access(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(role.id == SENIOR_ROLE_ID for role in member.roles)


async def get_review_channel(guild: discord.Guild):
    channel = guild.get_channel(REVIEW_CHANNEL_ID)
    if channel is None:
        channel = await bot.fetch_channel(REVIEW_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        raise RuntimeError("The configured review channel is not a text channel.")
    return channel


def get_applicant_id(embed: discord.Embed) -> int | None:
    footer = embed.footer.text or ""
    match = re.search(r"Applicant ID:\s*(\d+)", footer)
    return int(match.group(1)) if match else None


def get_game_nickname(embed: discord.Embed) -> str | None:
    """Read the submitted in-game full name from the formal application text."""
    description = embed.description or ""
    match = re.search(r"Я,\s*(.+?),\s*находящийся в звании", description, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return discord.utils.escape_markdown(match.group(1).strip())


def next_application_number() -> int:
    """Persistent sequential application number, safe across bot restarts."""
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL)"
        )
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT OR IGNORE INTO counters(name, value) VALUES ('resignation', 0)"
        )
        connection.execute(
            "UPDATE counters SET value = value + 1 WHERE name = 'resignation'"
        )
        row = connection.execute(
            "SELECT value FROM counters WHERE name = 'resignation'"
        ).fetchone()
        connection.commit()
        return int(row[0])


def make_application_embed(
    applicant: discord.Member,
    application_number: int,
    nickname: str,
    static_id: str,
    rank: str,
    reason: str,
) -> discord.Embed:
    submitted_at = datetime.now(timezone.utc)
    joined_at = applicant.joined_at
    joined_text = (
        f"<t:{int(joined_at.timestamp())}:f> (<t:{int(joined_at.timestamp())}:R>)"
        if joined_at else "Неизвестно"
    )

    # The body follows the formal application layout from the reference image.
    description = (
        f"**Пользователь:** {applicant.mention}\n"
        f"**ID:** `{applicant.id}`\n"
        f"**Присоединился:** {joined_text}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Начальнику Управления ГИБДД ГУ МВД по г. Москве и Московской области "
        "Генерал-полковнику полиции\n"
        f"<@&{SENIOR_ROLE_ID}>\n\n"
        f"от **{rank} {nickname}**\n\n"
        "**Заявление**\n\n"
        f"Я, {nickname}, находящийся в звании {rank}, служебное удостоверение "
        f"№ {static_id}, прошу Вас рассмотреть моё заявление на увольнение из рядов "
        "Управления Государственной Инспекции Безопасности Дорожного Движения "
        f"по причине: {reason}.\n\n"
        f"**Дата:** <t:{int(submitted_at.timestamp())}:d>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "**Статус:** 🟠 Ожидает рассмотрения"
    )

    embed = discord.Embed(
        title=f"Заявление на увольнение №{application_number}",
        description=description,
        color=discord.Color.from_rgb(54, 83, 73),
        timestamp=submitted_at,
    )
    embed.set_thumbnail(url=applicant.display_avatar.url)
    embed.set_footer(
        text=f"ГИБДД | RMRP «Тверской» • Applicant ID: {applicant.id}"
    )
    return embed


async def update_review_message(
    interaction: discord.Interaction,
    status_text: str,
    color: discord.Color,
):
    if not interaction.message or not interaction.message.embeds:
        return
    embed = interaction.message.embeds[0].copy()
    description = embed.description or ""
    marker = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n**Статус:**"
    marker_index = description.rfind(marker)
    if marker_index >= 0:
        description = description[:marker_index]
    else:
        # Fallback for applications created by an older version of the bot.
        status_index = description.rfind("**Статус:**")
        if status_index >= 0:
            description = description[:status_index]
        else:
            description += "\n\n"
    embed.description = description + marker + " " + status_text
    embed.color = color
    await interaction.message.edit(embed=embed, view=None)


async def notify_applicant(guild: discord.Guild, applicant_id: int, message: str):
    try:
        member = guild.get_member(applicant_id) or await guild.fetch_member(applicant_id)
        await member.send(message)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        log.info("Could not send a DM to applicant %s", applicant_id)


# ============================================================
# APPLICATION MODAL
# ============================================================

class ResignationModal(discord.ui.Modal, title="Заявление на увольнение"):
    nickname = discord.ui.TextInput(
        label="Игровой никнейм",
        placeholder="Введите игровой никнейм",
        min_length=2,
        max_length=64,
    )
    static_id = discord.ui.TextInput(
        label="Static ID",
        placeholder="Введите ваш Static ID",
        min_length=1,
        max_length=32,
    )
    rank = discord.ui.TextInput(
        label="Текущее звание",
        placeholder="Например: Младший сержант полиции",
        min_length=2,
        max_length=100,
    )
    reason = discord.ui.TextInput(
        label="Причина увольнения",
        placeholder="Подробно укажите причину",
        style=discord.TextStyle.paragraph,
        min_length=3,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Подать заявление можно только на сервере.",
                ephemeral=True,
            )
            return

        try:
            channel = await get_review_channel(interaction.guild)
            application_number = next_application_number()
            embed = make_application_embed(
                interaction.user,
                application_number,
                str(self.nickname.value).strip(),
                str(self.static_id.value).strip(),
                str(self.rank.value).strip(),
                str(self.reason.value).strip(),
            )
            await channel.send(embed=embed, view=ReviewView())
            await interaction.response.send_message(
                "✅ Ваше заявление отправлено старшему составу на рассмотрение.",
                ephemeral=True,
            )
        except Exception as error:
            log.exception("Failed to submit resignation application")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"❌ Не удалось отправить заявление: {str(error)[:800]}",
                    ephemeral=True,
                )


# ============================================================
# PANEL BUTTON
# ============================================================

class ApplicationPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Подать заявление на увольнение",
        style=discord.ButtonStyle.danger,
        custom_id="lipton_resignation:open_form",
        emoji="📝",
    )
    async def open_form(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_modal(ResignationModal())


# ============================================================
# REVIEW BUTTONS
# ============================================================

class ReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _check_reviewer(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Это действие доступно только на сервере.",
                ephemeral=True,
            )
            return False
        if not has_reviewer_access(interaction.user):
            await interaction.response.send_message(
                "❌ Рассматривать заявления может только старший состав.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Одобрить",
        style=discord.ButtonStyle.success,
        custom_id="lipton_resignation:approve",
        emoji="✅",
    )
    async def approve(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await self._check_reviewer(interaction):
            return

        if not interaction.message or not interaction.message.embeds:
            await interaction.response.send_message(
                "Не удалось прочитать заявление.",
                ephemeral=True,
            )
            return

        applicant_id = get_applicant_id(interaction.message.embeds[0])
        if applicant_id is None:
            await interaction.response.send_message(
                "В заявлении не найден ID заявителя.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        try:
            member = guild.get_member(applicant_id) or await guild.fetch_member(applicant_id)
            fired_role = guild.get_role(FIRED_ROLE_ID)
            if fired_role is None:
                raise RuntimeError("Роль «Уволен» не найдена. Проверь FIRED_ROLE_ID.")

            bot_member = guild.me
            if bot_member is None or not bot_member.guild_permissions.manage_roles:
                raise RuntimeError("У бота нет разрешения Manage Roles.")

            if fired_role.managed or fired_role >= bot_member.top_role or not fired_role.is_assignable():
                raise RuntimeError(
                    "Бот не может выдать роль «Уволен». Подними высшую роль бота "
                    "выше роли «Уволен» и проверь разрешение Manage Roles."
                )

            # Remove every assignable role except @everyone and the final fired role.
            roles_to_remove = [
                role for role in member.roles
                if not role.is_default()
                and role.id != FIRED_ROLE_ID
                and not role.managed
            ]

            blocked = [
                role for role in roles_to_remove
                if role >= bot_member.top_role or not role.is_assignable()
            ]
            if blocked:
                names = ", ".join(f"{role.name} ({role.id})" for role in blocked)
                raise RuntimeError(
                    "Бот не может снять некоторые роли из-за иерархии Discord: "
                    + names
                    + ". Подними роль бота выше этих ролей."
                )

            if roles_to_remove:
                await member.remove_roles(
                    *roles_to_remove,
                    reason=f"Resignation approved by {interaction.user} ({interaction.user.id})",
                )

            if fired_role not in member.roles:
                await member.add_roles(
                    fired_role,
                    reason=f"Resignation approved by {interaction.user} ({interaction.user.id})",
                )

            # Rename the member on this Discord server to "Уволен | Имя Фамилия".
            # Discord server nicknames are limited to 32 characters.
            game_full_name = get_game_nickname(interaction.message.embeds[0])
            nickname_result = ""
            if game_full_name:
                new_nickname = f"Уволен | {game_full_name}"[:32]
                try:
                    await member.edit(
                        nick=new_nickname,
                        reason=f"Resignation approved by {interaction.user} ({interaction.user.id})",
                    )
                    nickname_result = f"\n**Новое имя в Discord:** `{new_nickname}`"
                except discord.Forbidden:
                    nickname_result = (
                        "\n⚠️ **Имя не изменено:** боту не хватает права «Управлять никнеймами» "
                        "или его роль ниже роли сотрудника."
                    )
                except discord.HTTPException as nick_error:
                    log.warning("Could not change nickname for %s: %s", applicant_id, nick_error)
                    nickname_result = "\n⚠️ Не удалось автоматически изменить имя в Discord."
            else:
                nickname_result = "\n⚠️ Игровое имя не найдено в тексте заявления."

            removed_role_names = [role.name for role in roles_to_remove]
            if removed_role_names:
                removed_roles_text = ", ".join(removed_role_names)
                if len(removed_roles_text) > 1200:
                    removed_roles_text = removed_roles_text[:1197] + "..."
            else:
                removed_roles_text = "Служебных ролей для снятия не было."

            await update_review_message(
                interaction,
                (
                    f"🟢 Одобрено {interaction.user.mention}\n"
                    "**Сотрудник уволен.**\n"
                    f"**Сняты роли:** {removed_roles_text}"
                    f"{nickname_result}\n"
                    "**Итоговый статус:** Уволен"
                ),
                discord.Color.green(),
            )
            await notify_applicant(
                guild,
                applicant_id,
                f"✅ Ваше заявление на увольнение одобрено на сервере **{guild.name}**. "
                "Доступные служебные роли сняты, вам выдана роль «Уволен».",
            )
            await interaction.followup.send(
                f"✅ Заявление одобрено. Роли участника {member.mention} обновлены.",
                ephemeral=True,
            )

        except discord.NotFound:
            await interaction.followup.send(
                "❌ Заявитель больше не состоит на сервере.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Discord запретил изменение ролей. Проверь Manage Roles и иерархию ролей.",
                ephemeral=True,
            )
        except Exception as error:
            log.exception("Failed to approve resignation")
            await interaction.followup.send(
                f"❌ Не удалось одобрить заявление: {str(error)[:1200]}",
                ephemeral=True,
            )

    @discord.ui.button(
        label="Отклонить",
        style=discord.ButtonStyle.danger,
        custom_id="lipton_resignation:reject",
        emoji="❌",
    )
    async def reject(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await self._check_reviewer(interaction):
            return

        if not interaction.message or not interaction.message.embeds:
            await interaction.response.send_message(
                "Не удалось прочитать заявление.",
                ephemeral=True,
            )
            return

        applicant_id = get_applicant_id(interaction.message.embeds[0])
        if applicant_id is None:
            await interaction.response.send_message(
                "В заявлении не найден ID заявителя.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await update_review_message(
            interaction,
            f"🔴 Отклонено {interaction.user.mention}",
            discord.Color.red(),
        )
        await notify_applicant(
            interaction.guild,
            applicant_id,
            f"❌ Ваше заявление на увольнение на сервере **{interaction.guild.name}** отклонено.",
        )
        await interaction.followup.send("Заявление отклонено.", ephemeral=True)


# ============================================================
# SLASH COMMAND — THE ONLY SETUP COMMAND
# ============================================================

@app_commands.guilds(discord.Object(id=GUILD_ID))
@bot.tree.command(
    name="setup_resignation",
    description="Создать панель заявок на увольнение",
)
async def setup_resignation(interaction: discord.Interaction):
    if interaction.guild_id != GUILD_ID:
        await interaction.response.send_message(
            "Эта команда предназначена для рабочего сервера.",
            ephemeral=True,
        )
        return

    if not isinstance(interaction.user, discord.Member) or not has_reviewer_access(interaction.user):
        await interaction.response.send_message(
            "❌ Установить панель может только старший состав.",
            ephemeral=True,
        )
        return

    if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message(
            "Выберите текстовый канал для панели.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="Расторжение контракта",
        description=(
            "Нажмите кнопку ниже, чтобы открыть форму расторжения контракта "
            "по собственному желанию.\n\n"
            "**Требуемые данные:**\n"
            "• Игровой никнейм\n"
            "• Static ID\n"
            "• Текущее звание\n"
            "• Причина увольнения"
        ),
        color=discord.Color.from_rgb(48, 83, 73),
    )
    embed.set_footer(text="ГИБДД | RMRP «Тверской» • Система увольнений")

    await interaction.channel.send(embed=embed, view=ApplicationPanelView())
    await interaction.response.send_message(
        "✅ Панель заявок на увольнение опубликована.",
        ephemeral=True,
    )


# ============================================================
# STARTUP
# ============================================================

@bot.event
async def on_ready():
    log.info("Bot connected as %s (%s)", bot.user, bot.user.id)

    # Persistent views keep existing buttons working after restarts.
    if not getattr(bot, "_persistent_views_registered", False):
        bot.add_view(ApplicationPanelView())
        bot.add_view(ReviewView())
        bot._persistent_views_registered = True

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        log.error("Configured guild %s was not found. Check bot invite and GUILD_ID.", GUILD_ID)
        return

    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        log.info("Synced %s guild slash command(s).", len(synced))
    except Exception:
        log.exception("Slash command synchronization failed")


if __name__ == "__main__":
    bot.run(TOKEN, reconnect=True)
