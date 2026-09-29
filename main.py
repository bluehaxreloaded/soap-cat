import asyncio
import datetime
import discord
import hashlib
import json
import re
import os
import requests
import time
from base64 import b64decode
from cleaninty.ctr.simpledevice import SimpleCtrDevice
from cleaninty.ctr.soap.manager import CtrSoapManager
from cleaninty.ctr.soap import helpers
from db_abstractor import the_db
from discord.ext import commands
from dotenv import load_dotenv
from cleaninty_abstractor import cleaninty_abstractor
from cleaninty.nintendowifi.soapenvelopebase import SoapCodeError
from io import BytesIO, StringIO
from pyctr.type.exefs import ExeFSReader
from pathlib import Path
from urllib.parse import urlparse


intents = discord.Intents.default()
intents.message_content = True  # to read SOAP_REQUEST messages from maidy
bot = discord.Bot(intents=intents)
log_channel = None
load_dotenv()
soap_lock = asyncio.Lock()
active_requests: set[int] = set()  # user ids with a SOAP_REQUEST running
SOAP_REQUEST_COOLDOWN = 15  # seconds before a request for the same essential is accepted again
recent_essentials: dict[str, float] = {}  # essential hash -> when a request for it last came in

SOAP_REQUEST_RE = re.compile(
    r"^SOAP_REQUEST\s+(\d{15,25})\s+(\S+)\s+(\S+)\s*$", re.IGNORECASE
)
MESSAGE_LINK_RE = re.compile(
    r"^https://(?:\w+\.)?discord(?:app)?\.com/channels/\d+/(\d+)/(\d+)$"
)
DISCORD_CDN_HOSTS = ["cdn.discordapp.com", "media.discordapp.net"]
SERIAL_RECEIVED_TITLE = "✅ Serial number received"  # maidy's message with the serial the helpee entered
SERIAL_RE = re.compile(r"\b([A-Z]{2,3}\d{8,9})\b")
# Where maidy stores helpees' essential.exefs files, one per soap channel (same server and user as maidy)
ESSENTIALS_DIR = Path(os.getenv("ESSENTIALS_DIR") or "~/essentials").expanduser()
MAX_ESSENTIAL_SIZE = 0x4000


def can_run():
    async def uhhhhhhh(interaction: discord.Interaction) -> bool:
        for id in [1345177409154191414, 1316931678509334548, 1398475463927791697]:
            try:
                if interaction.user.roles[-1] >= interaction.guild.get_role(id):
                    return True
            except TypeError:
                pass
            else:
                raise commands.MissingRole(id)

    return commands.check(uhhhhhhh)


@bot.slash_command(description="does a soap")
@can_run()
@commands.cooldown(1, 5, commands.BucketType.channel)
@discord.option(
    "serial",
    str,
    required=False,
    description="the serial on the sticker, uses the one the helpee gave maidy if blank, 'skip' to skip the check",
    max_length=12,
)
@discord.option(
    "essential_exefs",
    discord.Attachment,
    required=False,
    description="...the essential.exefs of the console to soap, uses the one maidy saved if left blank",
)
@discord.option(
    "essential_exefs_link",
    str,
    required=False,
    description="a link to the essential.exefs of the console to soap",
)
@discord.option(
    "console_json",
    discord.Attachment,
    required=False,
    description="...the .json of the console to soap",
)
@discord.option(
    "maidy",
    bool,
    required=False,
    description="involve maidy in the soap things, defaults to true",
    default=True,
)
async def doasoap(
    ctx: discord.ApplicationContext,
    serial: str,
    essential_exefs: discord.Attachment,
    essential_exefs_link: str,
    console_json: discord.Attachment,
    maidy: bool,
):
    try:
        await ctx.defer(ephemeral=True)
    except discord.errors.NotFound:
        return

    await log(
        f"doing soap for {ctx.author.global_name} ({ctx.author.id}) in {ctx.interaction.channel.jump_url}"
        + f" ({ctx.interaction.channel.name})"
    )

    # Extract channel and user_id
    channel = bot.get_channel(ctx.channel_id)
    user_id = user_id_from_topic(getattr(channel, "topic", None))

    # No serial given, so use the one the helpee entered with maidy
    if serial is None:
        serial = await entered_serial(ctx.interaction.channel)
        if serial is None:
            await ctx.respond(
                ephemeral=True,
                content="no serial given and maidy doesn't have one for this channel, give the serial and try again",
            )
            await log(
                f"soap for {ctx.author.global_name} ({ctx.author.id}) failed due to no serial"
            )
            return

    await send_soap_status(maidy, ctx.interaction.channel.id, "PROGRESS", "START")

    if essential_exefs is not None:
        try:
            soap_json = generate_json(await essential_exefs.read())
            soap_name = essential_exefs.filename[:-6]
        except Exception as e:
            await ctx.respond(ephemeral=True, content=f"Failed to load essential\n{e}")
            await log(
                f"soap for {ctx.author.global_name} ({ctx.author.id}) failed due to loading the essential failing"
            )
            await send_soap_status(
                maidy, ctx.interaction.channel.id, "ERROR", "ESSENTIAL_LOAD_FAILED"
            )
            raise e

    elif essential_exefs_link is not None:
        request_data = requests.get(essential_exefs_link)

        if request_data.status_code != 200:
            await ctx.respond(
                ephemeral=True,
                content=f"Non-200 status code: {request_data.status_code}",
            )
            await log(
                f"soap for {ctx.author.global_name} ({ctx.author.id}) failed "
                + "due to non-200 status code ({request_data.status_code}) when fetching exefs from link"
            )  # split into 2 so it isn't so long
            await send_soap_status(
                maidy, ctx.interaction.channel.id, "ERROR", "ESSENTIAL_LINK_FAILED"
            )
            return

        try:
            soap_json = generate_json(request_data.content)
            soap_name = get_json_serial(soap_json).upper()
        except Exception as e:
            await ctx.respond(ephemeral=True, content=f"Failed to load essential\n{e}")
            await log(
                f"soap for {ctx.author.global_name} ({ctx.author.id}) failed due to loading the essential failing"
            )
            await send_soap_status(
                maidy, ctx.interaction.channel.id, "ERROR", "ESSENTIAL_LOAD_FAILED"
            )
            raise e

    elif console_json is not None:
        soap_json = await console_json.read()
        soap_name = console_json.filename[:-5]
        if not donorcheck(soap_json):
            await ctx.respond(ephemeral=True, content="Failed to verify json")
            await log(
                f"soap for {ctx.author.global_name} ({ctx.author.id}) failed due to invalid json"
            )
            await send_soap_status(
                maidy, ctx.interaction.channel.id, "ERROR", "INVALID_JSON"
            )
            return
    # Nothing given, so use the essential.exefs maidy saved for this channel if there is one
    elif (stored := read_stored_essential(ctx.interaction.channel.id, user_id)) is not None:
        try:
            soap_json = generate_json(stored)
            soap_name = get_json_serial(soap_json).upper()
        except Exception as e:
            await ctx.respond(ephemeral=True, content=f"Failed to load essential\n{e}")
            await log(
                f"soap for {ctx.author.global_name} ({ctx.author.id}) failed due to loading the essential failing"
            )
            await send_soap_status(
                maidy, ctx.interaction.channel.id, "ERROR", "ESSENTIAL_LOAD_FAILED"
            )
            raise e

    else:
        await ctx.respond(
            ephemeral=True,
            content="uh... what? you didn't send a .json, .exefs, or link to .exefs, "
            + "and maidy doesn't have one saved for this channel, try again",
        )
        await log(
            f"soap for {ctx.author.global_name} ({ctx.author.id}) failed due to lack of file"
        )
        await send_soap_status(maidy, ctx.interaction.channel.id, "ERROR", "NO_FILE")
        return

    await perform_soap(
        respond=lambda **kwargs: ctx.respond(ephemeral=True, **kwargs),
        who=f"{ctx.author.global_name} ({ctx.author.id})",
        channel_id=ctx.interaction.channel.id,
        user_id=user_id,
        guild=ctx.guild,
        soap_json=soap_json,
        soap_name=soap_name,
        serial=serial,
        maidy=maidy,
    )


@bot.listen("on_message")
async def soap_request(message: discord.Message):
    """SOAP_REQUEST <USERID> <SERIAL> <ESSENTIALS>, sent by maidy in the bots only channel.
    ESSENTIALS is one of:
    - STORED, to use the essential.exefs maidy saved for this helpee in their soap channel
    - a link to a message with the essential.exefs attached (e.g. maidy's "essential.exefs received" message)
    - ATTACHED, with the essential.exefs attached to the SOAP_REQUEST message itself
    - a cdn.discordapp.com link to the essential.exefs
    """
    bots_only_channel = os.getenv("BOTS_ONLY_CHANNEL")
    if (
        not bots_only_channel
        or message.channel.id != int(bots_only_channel)
        or not message.author.bot
        or message.author.id == bot.user.id
    ):
        return

    match = SOAP_REQUEST_RE.match((message.content or "").strip())
    if not match:
        return
    user_id = int(match.group(1))
    serial = match.group(2)
    essentials = match.group(3)

    channel = find_soap_channel(message.guild, user_id)
    if channel is None:
        await log(f"SOAP_REQUEST for user {user_id} failed, no soap channel found for them")
        await message.reply(f"SOAP_REQUEST {user_id} [WARN: CHANNEL NOT FOUND]")
        return

    if user_id in active_requests:
        await log(f"ignoring SOAP_REQUEST for user {user_id}, one is already running")
        return
    active_requests.add(user_id)

    who = f"user {user_id} (SOAP_REQUEST from {message.author.name})"
    try:
        try:
            essential = await read_essential_ref(message, essentials, channel, user_id)
        except Exception as e:
            await log(f"soap for {who} failed due to loading the essential failing\n{e}")
            await send_soap_status(True, channel.id, "ERROR", "ESSENTIAL_LOAD_FAILED")
            return

        # Cancel duplicates of a request for the same essential that came in just before
        essential_hash = hashlib.sha256(essential).hexdigest()
        now = time.monotonic()
        if now - recent_essentials.get(essential_hash, 0) < SOAP_REQUEST_COOLDOWN:
            await log(f"ignoring duplicate SOAP_REQUEST for {who}")
            return
        recent_essentials[essential_hash] = now

        await log(f"doing soap for {who} in {channel.jump_url} ({channel.name})")
        await send_soap_status(True, channel.id, "PROGRESS", "START")

        try:
            soap_json = generate_json(essential)
            soap_name = get_json_serial(soap_json).upper()
        except Exception as e:
            await log(f"soap for {who} failed due to loading the essential failing\n{e}")
            await send_soap_status(True, channel.id, "ERROR", "ESSENTIAL_LOAD_FAILED")
            return

        try:
            await perform_soap(
                # Only the result text goes back, the console .json isn't needed and shouldn't sit in the channel
                respond=lambda content, file=None: message.reply(content=content),
                who=who,
                channel_id=channel.id,
                user_id=user_id,
                guild=message.guild,
                soap_json=soap_json,
                soap_name=soap_name,
                serial=serial,
                maidy=True,
            )
        except Exception as e:
            await log(f"soap for {who} failed with an error\n{e}")
            await send_soap_status(True, channel.id, "ERROR", "UNKNOWN")
            raise e
    finally:
        active_requests.discard(user_id)


async def perform_soap(
    respond,
    who: str,
    channel_id: int,
    user_id: int | None,
    guild: discord.Guild,
    soap_json: str,
    soap_name: str,
    serial: str,
    maidy: bool,
):
    """The soap itself, shared by /doasoap and SOAP_REQUEST. respond sends content/file back to whoever asked."""
    resultStr = str("")

    if serial is not None:
        # .upper() is just for consistency
        soap_serial = get_json_serial(soap_json).upper()
        serial = str(serial).upper()

        await send_soap_status(maidy, channel_id, "PROGRESS", "SERIAL_CHECK_ATTEMPT")

        if serial == "SKIP":
            resultStr += "skipping serial check\n"

        elif serial[0] not in ["C", "S", "A", "Y", "Q", "N"]:
            resultStr += f"{serial[0]} is not a valid console digit" + (
                "(nice aliexpress serial)" if serial[0] == "U" else ""
            )
            await respond(content=resultStr)
            await log(f"soap for {who} failed due to invalid serial")
            await send_soap_status(maidy, channel_id, "ERROR", "INVALID_SERIAL")
            return

        elif len(serial) not in [10, 11, 12]:
            resultStr += f"invalid serial length, must be 10-12 characters long instead of {len(serial)}"
            await respond(content=resultStr)
            await log(f"soap for {who} failed due to invalid serial")
            await send_soap_status(maidy, channel_id, "ERROR", "INVALID_SERIAL_LENGTH")
            return

        elif serial[: len(soap_serial)] != soap_serial:
            resultStr += f"secinfo serial and given serial do not match!\nsecinfo: {soap_serial}\ngiven: {serial[: len(soap_serial)]}\n"
            resultStr += "nothing has been done to any donors or the soapee"
            await respond(content=resultStr)
            await log(f"soap for {who} failed due to mismatching serials")
            await send_soap_status(maidy, channel_id, "ERROR", "SERIAL_MISMATCH")
            return
        else:
            resultStr += "secinfo serial and given serial match, continuing\n"

    if soap_lock.locked():
        await send_soap_status(maidy, channel_id, "PROGRESS", "QUEUED")
        await respond(
            content="Another soap operation is currently being processed, please wait...",
        )

    async with soap_lock:
        try:
            await send_soap_status(maidy, channel_id, "PROGRESS", "CLEANINTY_INIT")
            dev = SimpleCtrDevice(json_string=soap_json)
            soapMan = CtrSoapManager(dev, False)
            await asyncio.to_thread(helpers.CtrSoapCheckRegister, soapMan)
            cleaninty = cleaninty_abstractor()
        except Exception as e:
            await log(f"soap for {who} failed due to a cleaninty error")
            raise e

        soap_json = dev.serialize_json()
        await send_soap_status(maidy, channel_id, "PROGRESS", "CLEANINTY_INIT_SUCCESS")

        if json.loads(soap_json)["region"] == "USA":
            source_region_change = "JPN"
            source_country_change = "JP"
            source_language_change = "ja"
        else:
            source_region_change = "USA"
            source_country_change = "US"
            source_language_change = "en"

        resultStr += "Attempting eShopRegionChange on source...\n"
        await send_soap_status(
            maidy, channel_id, "PROGRESS", "ESHOP_REGION_CHANGE_ATTEMPT"
        )
        try:
            soap_json, resultStr = await asyncio.to_thread(
                cleaninty.eshop_region_change,
                json_string=soap_json,
                region=source_region_change,
                country=source_country_change,
                language=source_language_change,
                result_string=resultStr,
            )
            await send_soap_status(
                maidy, channel_id, "PROGRESS", "ESHOP_REGION_CHANGE_SUCCESS"
            )
        except SoapCodeError as err:
            if err.soaperrorcode != 602:
                await log(
                    f"soap for {who} failed due to non-602 soap error code (wtf)"
                )
                raise err

            resultStr += "sticky titles are sticking, doing system transfer...\n"
            lottery = False
            await send_soap_status(
                maidy, channel_id, "PROGRESS", "SYSTEM_TRANSFER_ATTEMPT"
            )
            soap_json, donor_json_name, resultStr = await asyncio.to_thread(
                cleaninty.do_transfer_with_donor, soap_json, resultStr
            )
            await send_soap_status(
                maidy, channel_id, "PROGRESS", "SYSTEM_TRANSFER_SUCCESS"
            )

            resultStr += f" `{donor_json_name}` is now on cooldown\n"

            await asyncio.to_thread(helpers.CtrSoapCheckRegister, soapMan)
            soap_json = cleaninty.clean_json(soap_json)

        else:
            resultStr += "sticky titles aren't sticking or don't exist (you won the soap lottery), deleting eShop account...\n"
            lottery = True
            soap_json, resultStr = await asyncio.to_thread(
                cleaninty.delete_eshop_account,
                json_string=soap_json,
                result_string=resultStr,
            )
            await send_soap_status(
                maidy, channel_id, "PROGRESS", "ESHOP_DELETE_SUCCESS"
            )

        await asyncio.to_thread(helpers.CtrSoapCheckRegister, soapMan)
        soap_json = cleaninty.clean_json(soap_json)

    await log(f"soap for {who} succeeded")
    resultStr += "Done!"
    await send_soap_status(maidy, channel_id, "PROGRESS", "SUCCESS")

    await respond(
        content=resultStr,
        file=discord.File(fp=StringIO(soap_json), filename=f"{soap_name}.json"),
    )

    # Try to get member (only if user_id was found)
    if user_id is not None:
        member_obj = guild.get_member(user_id)
        # Fallback to fetching from API
        if not member_obj:
            try:
                member_obj = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden):
                member_obj = None
    else:
        member_obj = None
    member_name = member_obj.name if member_obj else None

    # await channel.send(f"{member_obj.mention} :arrow_down:")

    await log(
        "Debug info:\n"
        + f"member_obj is {member_obj}\n"
        + f"member_name is {member_name}\n"
        + f"user_id is {user_id}\n"
        + f"lottery is {lottery}\n"
    )

    if lottery:
        # Send SOAP_STATUS message
        await send_soap_status(maidy, channel_id, "LOTTERY", serial)

    else:
        # Send SOAP_STATUS message
        await send_soap_status(maidy, channel_id, "SUCCESS", serial)


@bot.slash_command(description="check soap donor availability")
@discord.option(
    "count", int, required=False, max_value=25, default=9, description="defaults to 9"
)
@can_run()
async def soapcheck(ctx: discord.ApplicationContext, count: int):
    try:
        await ctx.defer(ephemeral=True)
    except discord.errors.NotFound:
        return

    db = the_db()

    db.cursor.execute("SELECT * FROM donors ORDER BY status DESC, last_transferred ASC")
    donors = db.cursor.fetchall()

    embed = discord.Embed(
        title="SOAP check",
        description="Checks what SOAP donors are available",
        color=discord.Color.green(),
    )

    the_time = int(datetime.datetime.now(datetime.UTC).timestamp())
    available_donors = 0
    disabled_donors = 0
    broken_donors = 0

    for i in range((count if len(donors) > count else len(donors))):
        if donors[i][5] == 1:
            embed.add_field(name=f"{i + 1}. `{donors[i][0]}`", value="Disabled")

        elif donors[i][5] == 3:
            embed.add_field(name=f"{i + 1}. `{donors[i][0]}`", value="Broken")

        elif donors[i][5] == 5:
            embed.add_field(name=f"{i + 1}. `{donors[i][0]}`", value="In use")

        elif (donors[i][2] + 604800) <= the_time and donors[i][5] == 0:
            embed.add_field(name=f"{i + 1}. `{donors[i][0]}`", value="Ready")

        elif donors[i][5] == 0:
            embed.add_field(
                name=f"{i + 1}. `{donors[i][0]}`",
                value=f"Ready <t:{donors[i][2] + 604800}:R>",
            )

        else:
            embed.add_field(name=f"{i + 1}. `{donors[i][0]}`", value="Unknown (??)")

    for i in range(len(donors)):
        if (donors[i][2] + 604800) <= the_time and donors[i][5] == 0:
            available_donors += 1

        elif donors[i][5] == 1:
            disabled_donors += 1

        elif donors[i][5] == 3:
            broken_donors += 1

    embed.set_footer(
        text=f"{len(donors)} total, {available_donors} available, {disabled_donors} disabled manually, {broken_donors} broken"
    )

    await ctx.respond(ephemeral=True, embed=embed)


@bot.slash_command(description="uploads a donor to be used for future soaps")
@can_run()
@discord.option("donor_json_file", discord.Attachment, required=False)
@discord.option("donor_exefs_file", discord.Attachment, required=False)
@discord.option(
    "note",
    str,
    required=False,
    description="any notes you want attached to the donor",
    max_length=128,
)
@discord.option(
    "name",
    str,
    required=False,
    description="if blank name is taken from the file name",
)
async def uploaddonortodb(
    ctx: discord.ApplicationContext,
    donor_json_file: discord.Attachment,
    donor_exefs_file: discord.Attachment,
    note: str,
    name: str,
):
    try:
        await ctx.defer(ephemeral=True)
    except discord.errors.NotFound:
        return

    if donor_exefs_file is not None:
        if not donor_exefs_file.filename[-6:] == ".exefs":
            await ctx.respond(ephemeral=True, content="not a .exefs!")
            return

        try:
            donor_json = generate_json(essential=await donor_exefs_file.read())

            if name is None:
                donor_name = donor_exefs_file.filename[:-6]
            else:
                donor_name = name

        except Exception as e:
            await ctx.respond(ephemeral=True, content=e)
            return

    elif donor_json_file is not None:
        if not donor_json_file.filename[-5:] == ".json":
            await ctx.respond(ephemeral=True, content="not a .json!")
            return

        try:
            donor_json = await donor_json_file.read()
            donor_json = donor_json.decode("utf-8")
            json.loads(donor_json)  # Validate the json, output useless

            if name is None:
                donor_name = donor_json_file.filename[:-5]
            else:
                donor_name = name

        except Exception:
            await ctx.respond(ephemeral=True, content="Failed to load json")
            return

    else:
        await ctx.respond(
            ephemeral=True,
            content="uh... what? you didn't send a .json or .exefs, try again",
        )
        return

    if not donorcheck(donor_json):
        await ctx.respond(
            ephemeral=True,
            content="not a valid donor!\nif you believe this to be a mistake contact blueness",
        )
        return

    db = the_db()
    cleaninty = cleaninty_abstractor()

    if db.read_index(table="donors", index_field_name="name", index=name) is not None:
        await ctx.respond(
            ephemeral=True, content=f"`{donor_name}` is already in the db!"
        )
        return

    if json.loads(donor_json)["region"] == "USA":
        donor_region_change = "JPN"
        donor_country_change = "JP"
        donor_language_change = "ja"
    else:
        donor_region_change = "USA"
        donor_country_change = "US"
        donor_language_change = "en"

    await log(f"uploading donor from {ctx.author.global_name} ({ctx.author.id})")

    if soap_lock.locked():
        await ctx.respond(
            ephemeral=True,
            content="Another soap operation is currently being processed, please wait...",
        )

    async with soap_lock:
        try:
            donor_json = cleaninty.eshop_region_change(
                json_string=donor_json,
                region=donor_region_change,
                country=donor_country_change,
                language=donor_language_change,
                result_string="",
            )[0]

        except SoapCodeError as err:
            if err.soaperrorcode != 602:
                raise err

            donor_json = cleaninty.do_transfer_with_donor(donor_json, "")[0]

            donor_json = cleaninty.eshop_region_change(
                json_string=donor_json,
                region=donor_region_change,
                country=donor_country_change,
                language=donor_language_change,
                result_string="",
            )[0]

        db.write_donor(
            name=donor_name,
            json=cleaninty.clean_json(donor_json),
            last_transferred=cleaninty.get_last_moved_time(donor_json),
            uploader=ctx.author.id,
            note=note,
            status=0,
        )

    await ctx.respond(
        ephemeral=True,
        content=f"`{donor_name}` has been uploaded to the donor database\nwant to remove it? contact blueness",
    )
    await log(
        f"{ctx.author.global_name} ({ctx.author.id}) uploaded {donor_name} to the db"
    )


@bot.slash_command(description="get the info of a donor")
@can_run()
@discord.option("name", str)
async def donorinfo(ctx: discord.ApplicationContext, name: str):
    try:
        await ctx.defer(ephemeral=True)
    except discord.errors.NotFound:
        return

    embed = discord.Embed(color=discord.Color.green(), title=f"info about `{name}`")

    donor = the_db().read_index(table="donors", index_field_name="name", index=name)
    if donor is None:
        await ctx.respond(ephemeral=True, content=f"The donor `{name}` does not exist!")
        return

    uploader = await ctx.bot.fetch_user(donor[3])
    embed.set_thumbnail(url=uploader.display_avatar.url)

    embed.add_field(name="Uploader:", value=f"{uploader.name} ({uploader.id})")
    embed.add_field(name="Note:", value=donor[4])
    embed.add_field(name="Last transfer time:", value=f"<t:{donor[2]}:f>")

    match donor[5]:
        case 0:
            embed.add_field(name="Status:", value="Healthy and enabled")
        case 1:
            embed.add_field(name="Status:", value="Manually disabled")
        case 3:
            embed.add_field(name="Status:", value="Automatically disabled due to error")
        case 5:
            embed.add_field(name="Status:", value="In use")
        case _:
            embed.add_field(
                name="Status:", value=f"{donor[5]}, this should not be possible"
            )

    await ctx.respond(ephemeral=True, embed=embed)


@bot.slash_command(description="renames a donor")
@can_run()
@discord.option("old_name", str)
@discord.option("new_name", str)
async def renamedonor(ctx: discord.ApplicationContext, old_name: str, new_name: str):
    try:
        await ctx.defer(ephemeral=True)
    except discord.errors.NotFound:
        return

    db = the_db()

    if db.read_index(table="donors", index_field_name="name", index=old_name) is None:
        await ctx.respond(
            ephemeral=True, content=f"The donor `{old_name}` does not exist!"
        )
        return

    db.cursor.execute(
        "UPDATE donors SET name = %s WHERE name = %s",
        (new_name, old_name),
    )
    db.connection.commit()

    await ctx.respond(
        ephemeral=True,
        content=f"`{old_name}` has been successfully renamed to `{new_name}`",
    )
    await log(
        f"{ctx.author.name} ({ctx.author.id}) renamed `{old_name}` to `{new_name}`"
    )


@bot.slash_command(
    description="disables a donor to stop it being used in soap operations"
)
@can_run()
@discord.option("name", str)
async def disabledonor(ctx: discord.ApplicationContext, name: str):
    try:
        await ctx.defer(ephemeral=True)
    except discord.errors.NotFound:
        return

    db = the_db()
    donor = db.read_index(table="donors", index_field_name="name", index=name)

    if donor is None:
        await ctx.respond(ephemeral=True, content=f"The donor `{name}` does not exist!")
        return

    elif donor[5] in [1, 3]:
        await ctx.respond(ephemeral=True, content="This donor is already disabled")
        return

    else:
        await asyncio.to_thread(db.set_donor_status, name, 1)
        await ctx.respond(
            ephemeral=True,
            content=f"`{name}` is now disabled\nuse enabledonor to re-enable it if wanted",
        )
        await log(f"{ctx.author.name} ({ctx.author.id}) disabled `{name}`")


@bot.slash_command(description="enables a donor to use it in soap operations")
@can_run()
@discord.option("name", str)
async def enabledonor(ctx: discord.ApplicationContext, name: str):
    try:
        await ctx.defer(ephemeral=True)
    except discord.errors.NotFound:
        return

    db = the_db()
    donor = db.read_index(table="donors", index_field_name="name", index=name)

    if donor is None:
        await ctx.respond(ephemeral=True, content=f"The donor `{name}` does not exist!")
        return

    elif donor[5] not in [1, 3]:
        await ctx.respond(ephemeral=True, content="This donor is not disabled")
        return

    else:
        await asyncio.to_thread(db.set_donor_status, name, 0)

        await ctx.respond(
            ephemeral=True,
            content=f"`{name}` is now enabled\nuse disabledonor to re-disable it if wanted",
        )
        await log(f"{ctx.author.name} ({ctx.author.id}) enabled `{name}`")


async def log(string: str):
    await bot.get_channel(int(os.getenv("LOG_CHANNEL"))).send(content=string)
    print(string)


async def send_soap_status(
    enable: bool, channel_id, status, error_type=None, serial=None
):
    if not enable:
        return
    bots_only_channel = os.getenv("BOTS_ONLY_CHANNEL")
    if not bots_only_channel:
        await log("BOTS_ONLY_CHANNEL not set, skipping SOAP_STATUS message")
        return
    if not channel_id:
        await log("User ID missing, skipping SOAP_STATUS message")
        return
    message_parts = ["SOAP_STATUS", str(channel_id), str(status).upper()]
    if serial:
        message_parts.append(str(serial))
    if error_type:
        message_parts.append(str(error_type).upper())
    await bot.get_channel(int(bots_only_channel)).send(" ".join(message_parts))


def user_id_from_topic(topic: str | None) -> int | None:
    """The helpee a soap channel belongs to, from the mention in its topic."""
    match = re.search(r"<@!?(\d+)>", topic or "")
    return int(match.group(1)) if match else None


def find_soap_channel(guild: discord.Guild, user_id: int) -> discord.TextChannel | None:
    """The soap channel of a helpee, found by the mention maidy puts in the channel topic."""
    for channel in guild.text_channels:
        if user_id_from_topic(channel.topic) == user_id:
            return channel
    return None


async def read_essential_ref(
    message: discord.Message, ref: str, channel: discord.TextChannel, user_id: int
) -> bytes:
    """Get the essential.exefs a SOAP_REQUEST points to, see soap_request for the formats."""
    if ref.upper() == "STORED":
        stored = read_stored_essential(channel.id, user_id)
        if stored is None:
            raise Exception(f"No stored essential for user {user_id} in channel {channel.id}")
        return stored

    elif ref.upper() == "ATTACHED":
        attachments = message.attachments

    elif match := MESSAGE_LINK_RE.match(ref):
        source_channel = bot.get_channel(int(match.group(1))) or await bot.fetch_channel(
            int(match.group(1))
        )
        attachments = (await source_channel.fetch_message(int(match.group(2)))).attachments

    elif urlparse(ref).hostname in DISCORD_CDN_HOSTS:
        request_data = await asyncio.to_thread(requests.get, ref, timeout=30)
        if request_data.status_code != 200:
            raise Exception(f"Non-200 status code: {request_data.status_code}")
        return request_data.content

    else:
        raise Exception(f"Don't know how to get an essential from {ref}")

    for attachment in attachments:
        if attachment.filename.lower().endswith(".exefs"):
            return await attachment.read()
    raise Exception("No .exefs attached")


async def entered_serial(channel: discord.TextChannel) -> str | None:
    """The serial the helpee entered with maidy, from its latest "Serial number received" message."""
    async for message in channel.history(limit=100):
        if message.author.bot and message.embeds and message.embeds[0].title == SERIAL_RECEIVED_TITLE:
            match = SERIAL_RE.search(message.embeds[0].description or "")
            if match:
                return match.group(1)
    return None


def read_stored_essential(channel_id: int, user_id: int | None) -> bytes | None:
    """The essential.exefs maidy stored for this helpee's soap channel, or None if there isn't one.
    maidy names each file after both, so it's only found for the helpee it belongs to.
    user_id is None for a channel with no helpee in its topic (e.g. testing), where maidy lets anyone upload."""
    if user_id is None:
        files = sorted(ESSENTIALS_DIR.glob(f"{int(channel_id)}-*.exefs"))
        if not files:
            return None
        path = files[0]
    else:
        path = ESSENTIALS_DIR / f"{int(channel_id)}-{int(user_id)}.exefs"
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as f:
        data = f.read(MAX_ESSENTIAL_SIZE + 1)
    if len(data) > MAX_ESSENTIAL_SIZE:
        raise Exception("Stored essential is too big")
    return data


def donorcheck(input_json: str) -> bool:
    try:
        input_json_obj = json.loads(input_json)

        if len(input_json_obj["otp"]) != 344:
            return False
        if len(input_json_obj["msed"]) not in [384, 428]:
            return False
        if len(input_json_obj["region"]) != 3:
            return False

    except Exception:
        return False
    return True


def generate_json(essential) -> str:  # thanks soupman

    reader = ExeFSReader(BytesIO(essential))

    if not "secinfo" and "otp" in reader.entries:
        raise Exception("Essential missing secinfo and/or otp")

    secinfo = reader.open("secinfo")
    secinfo.seek(0x100)
    country_byte = secinfo.read(1)

    if country_byte == b"\x01":
        country = "US"
    elif country_byte == b"\x02":
        country = "GB"
    elif country_byte == b"\x06":
        country = "TW"
    else:
        country = None

    generated_json = SimpleCtrDevice.generate_new_json(
        otp_data=reader.open("otp").read(),
        secureinfo_data=reader.open("secinfo").read(),
        country=country,
    )

    return generated_json


def get_json_serial(json_string: str) -> str:
    json_secinfo = b64decode(str(json.loads(json_string)["secureinfo"]).encode("ascii"))
    serial_bytes = bytes(json_secinfo[0x102:0x112]).replace(b"\x00", b"")
    return serial_bytes.upper().decode("utf-8")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} successfully!")
    print(
        discord.utils.oauth_url(
            bot.user.id, permissions=discord.Permissions(permissions=2147518464)
        )
    )
    global log_channel
    log_channel = bot.get_channel(1399953634560573551)

    await bot.change_presence(activity=discord.Game(name="I HAS SOAP *om nom nom*"))


@bot.event
async def on_application_command_error(
    ctx: discord.ApplicationContext, error: discord.DiscordException
):
    if isinstance(error, commands.MissingRole):
        await ctx.respond(ephemeral=True, content="you can't use this command!")

    elif isinstance(error, commands.errors.CommandOnCooldown):
        await ctx.respond(
            ephemeral=True,
            content=f"This command is currently on cooldown to avoid double-soaping, please wait {str(error.retry_after)[:4]}s",
        )
    else:
        await ctx.respond(
            ephemeral=True,
            content="an error has occurred, please do not try again",
        )

        if str(ctx.command) == "doasoap":
            for dict in ctx.selected_options:
                if dict["name"] == "maidy":
                    maidy = dict["value"]
                    break
                else:
                    maidy = True
            await send_soap_status(
                maidy, ctx.interaction.channel.id, "ERROR", "UNKNOWN"
            )

        await ctx.respond(ephemeral=True, content=f"Debug info:\n{error}")
        raise error


bot.load_extension("soupman")

bot.run(os.getenv("DISCORD_TOKEN"))
