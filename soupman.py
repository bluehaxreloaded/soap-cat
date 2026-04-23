"""
MIT License

Copyright (c) 2024 Jade Herd

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import asyncio
import discord
import json
from base64 import b64decode
from discord.ext import commands
from cleaninty.ctr.simpledevice import SimpleCtrDevice
from cleaninty.ctr.soap.manager import CtrSoapManager
from cleaninty.ctr.soap import helpers
from pyctr.type.exefs import ExeFSReader
from io import BytesIO, StringIO
import os


class soupman(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(description="Generate a consoles soap key (soupman)")
    @discord.option(
        "secinfo", discord.Attachment, description="secinfo.bin, SecureInfo_A"
    )
    @discord.option("otp", discord.Attachment, description="otp.bin")
    async def genjson(
        self,
        ctx: discord.ApplicationContext,
        secinfo: discord.Attachment,
        otp: discord.Attachment,
    ):
        try:
            await ctx.defer(ephemeral=True)
        except discord.errors.NotFound:
            return

        await self._log(f"{ctx.author} is generating a json from secinfo and otp...")
        secinfo_bytes = BytesIO(await secinfo.read())
        secinfo_bytes.seek(0x100)
        country_byte = secinfo_bytes.read(1)
        secinfo_bytes.close()

        if country_byte == b"\x01":
            country = "US"
        elif country_byte == b"\x02":
            country = "GB"
        elif country_byte == b"\x06":
            country = "TW"
        else:
            country = None

        try:
            jsonStr = SimpleCtrDevice.generate_new_json(
                otp_data=await otp.read(),
                secureinfo_data=await secinfo.read(),
                country=country,
            )

            dev = SimpleCtrDevice(json_string=jsonStr)
            soapMan = CtrSoapManager(dev, False)
            await asyncio.to_thread(helpers.CtrSoapCheckRegister, soapMan)
            jsonStr = dev.serialize_json()

            serial = b64decode(json.loads(jsonStr)["secureinfo"])[0x102:0x112]
            serial = serial.replace(b"\x00", b"").upper().decode("utf-8")

            retStr = f"Serial: {serial}\n"
            retStr += f"Account status: {soapMan.account_status}\n"
            if soapMan.account_status != "U":
                retStr += f"Account register: {'Expired' if soapMan.register_expired else 'Valid'}\n"
            retStr += f"Current effective region: {soapMan.region}\n"
            retStr += f"Current effective country: {soapMan.country}\n"
            retStr += f"Current effective language: {soapMan.language}\n"
        except Exception as e:
            await self._log(
                f"{ctx.author} has failed to generate a json from secinfo and otp"
            )
            raise e

        try:
            await ctx.respond(
                ephemeral=True,
                file=discord.File(fp=StringIO(jsonStr), filename="soap.json"),
                content=f"```\n{retStr}```",
            )
        except Exception as e:
            await ctx.respond(
                ephemeral=True, content="Failed to respond with soap.json"
            )
            raise e

        await self._log(
            f"{ctx.author} has successfully generated a json from secinfo and otp"
        )

    @discord.slash_command(
        description="Generate a consoles soap key using essential.exefs (soupman)"
    )
    @discord.option("essential", discord.Attachment, description="essential.exefs")
    async def genjsonessential(
        self,
        ctx: discord.ApplicationContext,
        essential: discord.Attachment,
    ):
        await ctx.defer(ephemeral=True)

        try:
            reader = ExeFSReader(BytesIO(await essential.read()))
        except Exception:
            await ctx.respond(ephemeral=True, content="Failed to read essential")
            raise

        if not "secinfo" and "otp" in reader.entries:
            await ctx.respond(ephemeral=True, content="Invalid essential")
            return

        await self._log(f"{ctx.author} is generating a json from essential...")

        secinfo = reader.open("secinfo")
        secinfo.seek(0x100)
        country_byte = secinfo.read(1)
        secinfo.seek(0, 1)  # reset secinfo.seek to avoid possible issues

        if country_byte == b"\x01":
            country = "US"
        elif country_byte == b"\x02":
            country = "GB"
        elif country_byte == b"\x06":
            country = "TW"
        else:
            country = None

        try:
            jsonStr = SimpleCtrDevice.generate_new_json(
                otp_data=reader.open("otp").read(),
                secureinfo_data=reader.open("secinfo").read(),
                country=country,
            )

            dev = SimpleCtrDevice(json_string=jsonStr)
            soapMan = CtrSoapManager(dev, False)
            await asyncio.to_thread(helpers.CtrSoapCheckRegister, soapMan)
            jsonStr = dev.serialize_json()

            serial = b64decode(json.loads(jsonStr)["secureinfo"])[0x102:0x112]
            serial = serial.replace(b"\x00", b"").upper().decode("utf-8")

            retStr = f"Serial: {serial}\n"
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
            await self._log(
                f"{ctx.author} has failed to generate a json from essential"
            )
            raise e

        try:
            await ctx.respond(
                ephemeral=True,
                file=discord.File(
                    fp=StringIO(jsonStr), filename=(essential.filename[:-6] + ".json")
                ),
                content=f"```\n{retStr}```",
            )

        except Exception:
            await ctx.respond(
                ephemeral=True, content="Failed to respond with soap.json"
            )
            raise
        await self._log(
            f"{ctx.author} has successfully generated a json from essential"
        )

    @discord.slash_command(description="check console registry (soupman)")
    @discord.option("jsonfile", discord.Attachment, description="soap.json")
    async def checkreg(
        self,
        ctx: discord.ApplicationContext,
        jsonfile: discord.Attachment,
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
            await asyncio.to_thread(helpers.CtrSoapCheckRegister, soapMan)
            jsonStr = dev.serialize_json()

            serial = b64decode(json.loads(jsonStr)["secureinfo"])[0x102:0x112]
            serial = serial.replace(b"\x00", b"").upper().decode("utf-8")

            retStr = f"Serial: {serial}\n"
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
    @discord.option(
        "infile", discord.Attachment, description="essential.exefs or secinfo"
    )
    async def checkserial(
        self,
        ctx: discord.ApplicationContext,
        infile: discord.Attachment,
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

    async def _log(self, string: str):
        await self.bot.get_channel(int(os.getenv("LOG_CHANNEL"))).send(content=string)
        print(string)


def setup(bot):
    bot.add_cog(soupman(bot))
