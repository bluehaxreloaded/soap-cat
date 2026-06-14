import asyncio
import datetime
import discord
import json
import re
import os
import requests
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


bot = discord.Bot()
log_channel = None
load_dotenv()
soap_lock = asyncio.Lock()


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
    description="the serial on the sticker, use 'skip' to skip the check (only skip if u smart)",
    max_length=12,
)
@discord.option(
    "essential_exefs",
    discord.Attachment,
    required=False,
    description="...the essential.exefs of the console to soap",
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
    resultStr = str("")

    # Extract channel and user_id
    channel = bot.get_channel(ctx.channel_id)
    topic = getattr(channel, "topic", None)
    user_id = None
    if topic:
        match = re.search(r"<@!?(\d+)>", topic)
        if match:
            try:
                user_id = int(match.group(1))
            except ValueError:
                user_id = None

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
            ctx.respond(ephemeral=True, content="Failed to verify json")
            await log(
                f"soap for {ctx.author.global_name} ({ctx.author.id}) failed due to invalid json"
            )
            await send_soap_status(
                maidy, ctx.interaction.channel.id, "ERROR", "INVALID_JSON"
            )
            return
    else:
        await ctx.respond(
            ephemeral=True,
            content="uh... what? you didn't send a .json, .exefs, or link to .exefs, try again",
        )
        await log(
            f"soap for {ctx.author.global_name} ({ctx.author.id}) failed due to lack of file"
        )
        await send_soap_status(maidy, ctx.interaction.channel.id, "ERROR", "NO_FILE")
        return

    if serial is not None:
        # .upper() is just for consistency
        soap_serial = get_json_serial(soap_json).upper()
        serial = str(serial).upper()

        await send_soap_status(
            maidy, ctx.interaction.channel.id, "PROGRESS", "SERIAL_CHECK_ATTEMPT"
        )

        if serial == "SKIP":
            resultStr += "skipping serial check\n"

        elif serial[0] not in ["C", "S", "A", "Y", "Q", "N"]:
            resultStr += f"{serial[0]} is not a valid console digit" + (
                "(nice aliexpress serial)" if serial[0] == "U" else ""
            )
            await ctx.respond(ephemeral=True, content=resultStr)
            await log(
                f"soap for {ctx.author.global_name} ({ctx.author.id}) failed due to invalid serial"
            )
            await send_soap_status(
                maidy, ctx.interaction.channel.id, "ERROR", "INVALID_SERIAL"
            )
            return

        elif len(serial) not in [10, 11, 12]:
            resultStr += f"invalid serial length, must be 10-12 characters long instead of {len(serial)}"
            await ctx.respond(ephemeral=True, content=resultStr)
            await log(
                f"soap for {ctx.author.global_name} ({ctx.author.id}) failed due to invalid serial"
            )
            await send_soap_status(
                maidy, ctx.interaction.channel.id, "ERROR", "INVALID_SERIAL_LENGTH"
            )
            return

        elif serial[: len(soap_serial)] != soap_serial:
            resultStr += f"secinfo serial and given serial do not match!\nsecinfo: {soap_serial}\ngiven: {serial[: len(soap_serial)]}\n"
            resultStr += "nothing has been done to any donors or the soapee"
            await ctx.respond(ephemeral=True, content=resultStr)
            await log(
                f"soap for {ctx.author.global_name} ({ctx.author.id}) failed due to mismatching serials"
            )
            await send_soap_status(
                maidy, ctx.interaction.channel.id, "ERROR", "SERIAL_MISMATCH"
            )
            return
        else:
            resultStr += "secinfo serial and given serial match, continuing\n"

    if soap_lock.locked():
        await send_soap_status(maidy, ctx.interaction.channel.id, "PROGRESS", "QUEUED")
        await ctx.respond(
            ephemeral=True,
            content="Another soap operation is currently being processed, please wait...",
        )

    async with soap_lock:
        try:
            await send_soap_status(
                maidy, ctx.interaction.channel.id, "PROGRESS", "CLEANINTY_INIT"
            )
            dev = SimpleCtrDevice(json_string=soap_json)
            soapMan = CtrSoapManager(dev, False)
            await asyncio.to_thread(helpers.CtrSoapCheckRegister, soapMan)
            cleaninty = cleaninty_abstractor()
        except Exception as e:
            await log(
                f"soap for {ctx.author.global_name} ({ctx.author.id}) failed due to a cleaninty error"
            )
            raise e

        soap_json = dev.serialize_json()
        await send_soap_status(
            maidy, ctx.interaction.channel.id, "PROGRESS", "CLEANINTY_INIT_SUCCESS"
        )

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
            maidy, ctx.interaction.channel.id, "PROGRESS", "ESHOP_REGION_CHANGE_ATTEMPT"
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
                maidy, user_id, "PROGRESS", "ESHOP_REGION_CHANGE_SUCCESS"
            )
        except SoapCodeError as err:
            if err.soaperrorcode != 602:
                await log(
                    f"soap for {ctx.author.global_name} ({ctx.author.id}) failed due to non-602 soap error code (wtf)"
                )
                raise err

            resultStr += "sticky titles are sticking, doing system transfer...\n"
            lottery = False
            await send_soap_status(
                maidy, ctx.interaction.channel.id, "PROGRESS", "SYSTEM_TRANSFER_ATTEMPT"
            )
            soap_json, donor_json_name, resultStr = await asyncio.to_thread(
                cleaninty.do_transfer_with_donor, soap_json, resultStr
            )
            await send_soap_status(
                maidy, ctx.interaction.channel.id, "PROGRESS", "SYSTEM_TRANSFER_SUCCESS"
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
                maidy, ctx.interaction.channel.id, "PROGRESS", "ESHOP_DELETE_SUCCESS"
            )

        await asyncio.to_thread(helpers.CtrSoapCheckRegister, soapMan)
        soap_json = cleaninty.clean_json(soap_json)

    await log(f"soap for {ctx.author.global_name} ({ctx.author.id}) succeeded")
    resultStr += "Done!"
    await send_soap_status(maidy, ctx.interaction.channel.id, "PROGRESS", "SUCCESS")

    await ctx.respond(
        ephemeral=True,
        content=resultStr,
        file=discord.File(fp=StringIO(soap_json), filename=f"{soap_name}.json"),
    )

    # Try to get member (only if user_id was found)
    if user_id is not None:
        member_obj = ctx.guild.get_member(user_id)
        # Fallback to fetching from API
        if not member_obj:
            try:
                member_obj = await ctx.guild.fetch_member(user_id)
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
        await send_soap_status(maidy, ctx.interaction.channel.id, "LOTTERY", serial)

    else:
        # Send SOAP_STATUS message
        await send_soap_status(maidy, ctx.interaction.channel.id, "SUCCESS", serial)


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
