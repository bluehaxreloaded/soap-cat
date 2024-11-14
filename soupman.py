import discord
import json
from discord.ext import commands
from cleaninty.ctr.simpledevice import SimpleCtrDevice
from cleaninty.ctr.soap.manager import CtrSoapManager
from cleaninty.ctr.soap import helpers
from pyctr.type.exefs import ExeFSReader
from io import BytesIO, StringIO


class soupman(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(description="Generate a consoles soap key (soupman)")
    async def genjson(
        self,
        ctx: discord.ApplicationContext,
        secinfo: discord.Option(discord.Attachment, "secinfo.bin, SecureInfo_A"),
        otp: discord.Option(discord.Attachment, "otp.bin"),
    ):
        try:
            await ctx.defer(ephemeral=True)
        except discord.errors.NotFound:
            return
        print(f"{ctx.author} is generating a json from secinfo and otp...")
        secinfo_bytes = BytesIO(await secinfo.read())
        secinfo_bytes.seek(0x100)
        country_byte = secinfo_bytes.read(1)
        secinfo_bytes.close()

        if country_byte == b"\x00":
            country = "JP"
        elif country_byte == b"\x02":
            country = "GB"
        else:
            country = None

        try:
            soapJson = SimpleCtrDevice.generate_new_json(
                otp_data=await otp.read(),
                secureinfo_data=await secinfo.read(),
                country=country,
            )
        except Exception as e:
            await ctx.respond(
                ephemeral=True, content=f"Cleaninty error:\n```\n{e}\n```"
            )
            print(f"Cleaninty: {e}")
            print(f"{ctx.author} has failed to generate a json from secinfo and otp")
            return

        try:
            await ctx.respond(
                ephemeral=True,
                file=discord.File(fp=StringIO(soapJson), filename="soap.json"),
            )
        except Exception:
            await ctx.respond(
                ephemeral=True, content="Failed to respond with soap.json"
            )
        print(f"{ctx.author} has successfully generated a json from secinfo and otp")

    @discord.slash_command(
        description="Generate a consoles soap key using essential.exefs (soupman)"
    )
    async def genjsonessential(
        self,
        ctx: discord.ApplicationContext,
        essential: discord.Option(discord.Attachment, "essential.exefs"),
    ):
        await ctx.defer(ephemeral=True)

        try:
            reader = ExeFSReader(BytesIO(await essential.read()))
        except Exception:
            await ctx.respond(ephemeral=True, content="Failed to read essential")
            return

        if not "secinfo" and "otp" in reader.entries:
            await ctx.respond(ephemeral=True, content="Invalid essential")
            return
        print(f"{ctx.author} is generating a json from essential...")
        secinfo = reader.open("secinfo")
        secinfo.seek(0x100)
        country_byte = secinfo.read(1)
        secinfo.seek(0, 1)  # reset secinfo.seek to avoid possible issues

        if country_byte == b"\x01":
            country = "US"
        elif country_byte == b"\x02":
            country = "GB"
        else:
            country = None

        try:
            soapJson = SimpleCtrDevice.generate_new_json(
                otp_data=reader.open("otp").read(),
                secureinfo_data=reader.open("secinfo").read(),
                country=country,
            )
        except Exception as e:
            await ctx.respond(
                ephemeral=True, content=f"Cleaninty error:\n```\n{e}\n```"
            )
            print(f"Cleaninty: {e}")
            print(f"{ctx.author} has failed to generate a json from essential")
            return

        try:
            await ctx.respond(
                ephemeral=True,
                file=discord.File(fp=StringIO(soapJson), filename="soap.json"),
            )
            
        except Exception:
            await ctx.respond(
                ephemeral=True, content="Failed to respond with soap.json"
            )
        print(f"{ctx.author} has successfully generated a json from essential")

    @discord.slash_command(description="check console registry (soupman)")
    async def checkreg(
        self,
        ctx: discord.ApplicationContext,
        jsonfile: discord.Option(discord.Attachment, "soap.json"),
    ):
        try:
            await ctx.defer(ephemeral=True)
        except discord.errors.NotFound:
            return

        try:
            jsonStr = await jsonfile.read()
            jsonStr = jsonStr.decode("utf-8")
            json.loads(jsonStr)  # Validate the json, output useless
        except Exception:
            await ctx.respond(ephemeral=True, content="Failed to load json")
            return

        try:
            dev = SimpleCtrDevice(json_string=jsonStr)
            soapMan = CtrSoapManager(dev, False)
            helpers.CtrSoapCheckRegister(soapMan)

            retStr = ""
            retStr += f"Account status: {soapMan.account_status}\n"
            if soapMan.account_status != "U":
                retStr += f"Account register: {'Expired' if soapMan.register_expired else 'Valid'}\n"
            retStr += f"Current effective region: {soapMan.region}\n"
            retStr += f"Current effective country: {soapMan.country}\n"
            retStr += f"Current effective language: {soapMan.language}\n"
        except Exception as e:
            await ctx.respond(
                ephemeral=True, content=f"Cleaninty error:\n```\n{e}\n```"
            )
            return

        await ctx.respond(ephemeral=True, content=f"```\n{retStr}```")

    @discord.slash_command(description="check serial of console uniques (soupman)")
    async def checkserial(
        self,
        ctx: discord.ApplicationContext,
        infile: discord.Option(discord.Attachment, "essential.exefs or secinfo"),
    ):
        try:
            await ctx.defer(ephemeral=True)
        except discord.errors.NotFound:
            return

        try:
            data = await infile.read()
        except Exception:
            await ctx.respond(ephemeral=True, content="Failed to read file")
            return

        # try to read as essential
        try:
            reader = ExeFSReader(BytesIO(data))
            if "secinfo" in reader.entries:
                data = reader.open("secinfo").read()
        except Exception:
            pass

        # The problem here is secinfo has no magic, so we can't really validate it
        # 273 bytes is the only thing we can do lol
        try:
            if len(data) != 273:
                await ctx.respond(ephemeral=True, content="Invalid secinfo provided")
                return

            data = data[0x102:0x112]
            data = data.replace(b"\x00", b"").upper().decode("utf-8")
        except Exception:
            await ctx.respond(ephemeral=True, content="Failed to read serial")
            return

        await ctx.respond(ephemeral=True, content=f"Serial: {data}")


def setup(bot):
    bot.add_cog(soupman(bot))
