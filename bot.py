import os
import re
import logging
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


def make_application_embed(
    applicant: discord.Member,
    nickname: str,
    static_id: str,
    rank: str,
    reason: str,
) -> discord.Embed:
    embed = discord.Embed(
        title="Заявление на увольнение",
        color=discord.Color.from_rgb(230, 145, 45),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="Пользователь Discord",
        value=f"{applicant.mention} (`{applicant.id}`)",
        inline=False,
    )
    embed.add_field(name="Игровой никнейм", value=nickname, inline=True)
    embed.add_field(name="Static ID", value=static_id, inline=True)
    embed.add_field(name="Текущее звание", value=rank, inline=True)
    embed.add_field(name="Причина увольнения", value=reason, inline=False)
    embed.add_field(name="Статус", value="🟠 Ожидает рассмотрения", inline=False)
    embed.set_footer(text=f"Система увольнений • Applicant ID: {applicant.id}")
    return embed


async def update_review_message(
    interaction: discord.Interaction,
    status_text: str,
    color: discord.Color,
):
    if not interaction.message or not interaction.message.embeds:
        return
    embed = interaction.message.embeds[0].copy()
    # Replace the status field while preserving all application details.
    status_index = next(
        (i for i, field in enumerate(embed.fields) if field.name == "Статус"),
        None,
    )
    if status_index is not None:
        embed.set_field_at(
            status_index,
            name="Статус",
            value=status_text,
            inline=False,
        )
    else:
        embed.add_field(name="Статус", value=status_text, inline=False)
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
            embed = make_application_embed(
                interaction.user,
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

            await update_review_message(
                interaction,
                f"✅ Одобрено — {interaction.user.mention}\nРоль «Уволен» выдана, доступные роли сняты.",
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
            f"❌ Отклонено — {interaction.user.mention}",
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
