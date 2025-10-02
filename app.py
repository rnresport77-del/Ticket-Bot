import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import Button, View
import os
from dotenv import load_dotenv
import asyncio
import datetime

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID")) if os.getenv("LOG_CHANNEL_ID") else None
SUPPORT_ROLE_ID = int(os.getenv("SUPPORT_ROLE_ID")) if os.getenv("SUPPORT_ROLE_ID") else None

intents = discord.Intents.default()
intents.message_content = True  # 필요한 경우 transcript-এ message content পেতে অন করো
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Utility functions ---
async def create_ticket_channel(guild: discord.Guild, member: discord.Member, reason: str = None):
    # ensure category exists
    category_name = "TICKETS"
    category = discord.utils.get(guild.categories, name=category_name)
    if category is None:
        category = await guild.create_category(category_name)

    # channel name
    safe_name = f"ticket-{member.name}".lower().replace(" ", "-")
    channel_name = f"{safe_name}-{member.discriminator}"

    # permission overwrites
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    if SUPPORT_ROLE_ID:
        support_role = guild.get_role(SUPPORT_ROLE_ID)
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    # create channel
    channel = await guild.create_text_channel(channel_name, overwrites=overwrites, category=category, reason="New ticket created")
    # send initial message with close button
    embed = discord.Embed(title="🎫 Ticket Opened", color=0x00AAFF,
                          description=f"{member.mention} Thank you — your ticket has been opened. The Support team will look into it soon.!\n\n**Cause:** {reason if reason else 'Not provided.'}",
                          timestamp=datetime.datetime.utcnow())
    close_btn = Button(label="Close Ticket", style=discord.ButtonStyle.red, custom_id=f"close_{channel.id}")
    view = View()
    view.add_item(close_btn)

    async def close_callback(interaction: discord.Interaction):
        # only allow support role or ticket author to close
        author = member
        user = interaction.user
        allowed = False
        if user.id == author.id:
            allowed = True
        if SUPPORT_ROLE_ID:
            r = interaction.guild.get_role(SUPPORT_ROLE_ID)
            if r and r in user.roles:
                allowed = True
        if not allowed and not user.guild_permissions.manage_channels:
            await interaction.response.send_message("You do not have permission to close tickets.", ephemeral=True)
            return

        # ask confirmation
        confirm = Button(label="Confirm Close", style=discord.ButtonStyle.red)
        cancel = Button(label="Cancel", style=discord.ButtonStyle.grey)
        confirm_view = View()
        confirm_view.add_item(confirm)
        confirm_view.add_item(cancel)

        async def confirm_cb(i: discord.Interaction):
            await i.response.defer(ephemeral=True)
            await archive_and_delete(channel, interaction.user)
            try:
                await i.followup.send("Ticket closed and archived.", ephemeral=True)
            except:
                pass

        async def cancel_cb(i: discord.Interaction):
            await i.response.edit_message(content="Closing canceled.", view=None, embed=None)

        confirm.callback = confirm_cb
        cancel.callback = cancel_cb

        await interaction.response.send_message("You're about to close this ticket. Confirm?", view=confirm_view, ephemeral=True)

    close_btn.callback = close_callback

    await channel.send(content=f"{member.mention}", embed=embed, view=view)
    return channel

async def archive_and_delete(channel: discord.TextChannel, closed_by: discord.Member):
    # gather messages for transcript
    messages = []
    async for msg in channel.history(limit=None, oldest_first=True):
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        author = f"{msg.author} ({msg.author.id})"
        content = msg.content
        # simple attachments listing
        attachments = ""
        if msg.attachments:
            attachments = " | Attachments: " + ", ".join(a.url for a in msg.attachments)
        messages.append(f"[{timestamp}] {author}: {content}{attachments}")

    transcript_text = "\n".join(messages) if messages else "No messages."

    # save to file
    filename = f"transcript-{channel.name}-{channel.id}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"Transcript for {channel.name} ({channel.id})\nClosed by: {closed_by} ({closed_by.id}) at {datetime.datetime.utcnow().isoformat()} UTC\n\n")
        f.write(transcript_text)

    # send transcript to log channel if configured
    if LOG_CHANNEL_ID:
        guild = channel.guild
        log_channel = guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            try:
                await log_channel.send(content=f"Transcript for {channel.mention} closed by {closed_by.mention}", file=discord.File(fp=filename))
            except Exception as e:
                print("Failed to send transcript to log channel:", e)

    # DM transcript to ticket author (best-effort)
    try:
        # find original opener from channel topic or first message author (fallback)
        opener = None
        async for msg in channel.history(limit=50, oldest_first=True):
            opener = msg.author
            break
        if opener:
            try:
                await opener.send(f"Your ticket `{channel.name}` has been closed by {closed_by}. Transcript attached.", file=discord.File(fp=filename))
            except:
                pass
    except:
        pass

    # wait a short time then delete channel
    await asyncio.sleep(1)
    try:
        await channel.delete(reason=f"Ticket closed by {closed_by}")
    except Exception as e:
        print("Error deleting channel:", e)

# --- Bot events and commands ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print("Failed to sync commands:", e)

# Slash command to post ticket message in a channel
@bot.tree.command(name="setup_ticket", description="Setup a ticket message in the current channel")
@app_commands.describe(reason="Optional: short description shown on the ticket embed")
async def setup_ticket(interaction: discord.Interaction, reason: str = None):
    # permission: require manage_guild or support role
    author = interaction.user
    if not (author.guild_permissions.manage_guild or (SUPPORT_ROLE_ID and interaction.guild.get_role(SUPPORT_ROLE_ID) in author.roles)):
        await interaction.response.send_message("You do not have permission. (Requires Manage Server or Support role))", ephemeral=True)
        return

    embed = discord.Embed(title="Support Ticket", description="To contact the support team, press the button below — a private ticket channel will open.", color=0x00AAFF)
    if reason:
        embed.add_field(name="Note", value=reason, inline=False)
    embed.set_footer(text="Ticket system")

    open_btn = Button(label="Open Ticket", emoji="🎫", style=discord.ButtonStyle.green, custom_id="open_ticket")
    view = View()
    view.add_item(open_btn)

    async def open_callback(inter: discord.Interaction):
        await inter.response.defer(ephemeral=True)
        member = inter.user
        # optional: get reason from modal or just create
        channel = await create_ticket_channel(inter.guild, member, reason=None)
        await inter.followup.send(f"Ticket created: {channel.mention}", ephemeral=True)

    open_btn.callback = open_callback

    await interaction.response.send_message(embed=embed, view=view)
    # ephemeral False -> visible message written to channel for everyone (setup command sender probably wants that)

# Optional: admin command to force-close ticket by channel id
@bot.tree.command(name="force_close", description="Force close a ticket channel by channel id (admin only)")
@app_commands.describe(channel_id="Channel ID to close")
async def force_close(interaction: discord.Interaction, channel_id: int):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Only server admins can use this.", ephemeral=True)
        return
    channel = interaction.guild.get_channel(channel_id)
    if not channel or not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("Channel not found or not a text channel.", ephemeral=True)
        return
    await interaction.response.send_message("Closing and archiving...", ephemeral=True)
    await archive_and_delete(channel, interaction.user)
    await interaction.followup.send("Done.", ephemeral=True)

# run bot
if __name__ == "__main__":
    if not TOKEN:
        print("Please set DISCORD_TOKEN in .env")
    else:
        bot.run(TOKEN)
