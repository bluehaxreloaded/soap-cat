import json
from cleaninty.ctr.simpledevice import SimpleCtrDevice
from cleaninty.ctr.soap.manager import CtrSoapManager
from cleaninty.ctr.soap import helpers, ias
from cleaninty.nintendowifi.soapenvelopebase import SoapCodeError
from cleaninty.ctr.ninja import NinjaManager, NinjaException
from db_abstractor import the_db


class cleaninty_abstractor:
    def eshop_region_change(
        self,
        json_string: str,
        region: str,
        country: str,
        language: str,
        result_string: str,
    ) -> tuple[str, str]:
        device = SimpleCtrDevice(json_string=json_string)
        soap_device = CtrSoapManager(device, False)

        helpers.CtrSoapCheckRegister(soap_device)

        json_string = device.serialize_json()

        if region == soap_device.region and soap_device.account_status != "U":
            result_string += "Console is already in the desired region.\n"
            return device.serialize_json(), result_string

        device.reboot()

        if soap_device.account_status != "U":
            helpers.CtrSoapUseSystemApps(soap_device, helpers.SysApps.ESHOP)
            helpers.CtrSoapSessionConnect(soap_device)

            result_string = _run_unregister(device, soap_device, result_string)

            json_string = device.serialize_json()

            device.reboot()

        soap_device.region_change(region, country, language)

        helpers.CtrSoapUseSystemApps(soap_device, helpers.SysApps.ESHOP)

        helpers.CtrSoapSessionConnect(soap_device)

        helpers.CtrSoapCheckRegister(soap_device)

        return device.serialize_json(), result_string

    def delete_eshop_account(
        self, json_string: str, result_string: str
    ) -> tuple[str, str]:
        result_string += "Initializing console...\n"
        device = SimpleCtrDevice(json_string=json_string)
        soap_device = CtrSoapManager(device, False)

        helpers.CtrSoapCheckRegister(soap_device)

        json_string = device.serialize_json()

        if soap_device.account_status == "U":
            result_string += "Console already does not have EShop account.\n"
            return device.serialize_json(), result_string

        device.reboot()

        helpers.CtrSoapUseSystemApps(soap_device, helpers.SysApps.ESHOP)
        helpers.CtrSoapSessionConnect(soap_device)

        json_string = device.serialize_json()

        result_string += "Unregister...\n"
        result_string = _run_unregister(device, soap_device, result_string)

        return device.serialize_json(), result_string

    def get_last_moved_time(self, json_string: str) -> int:
        device = SimpleCtrDevice(json_string=json_string)
        soap_device = CtrSoapManager(device, False)
        helpers.CtrSoapCheckRegister(soap_device)

        json_string = device.serialize_json()
        acct_attributes = ias.GetAccountAttributesByProfile(soap_device, "MOVE_ACCT")

        for i in acct_attributes.accountattributes:
            if i.name == "MoveAccountLastMovedDate":
                return (int(i.value) if i.value else 0) / 1000

    def do_system_transfer(
        self, source_json: str, donor_json: str, result_string: str
    ) -> tuple[str, str, str]:
        source = SimpleCtrDevice(json_string=source_json)
        soap_source = CtrSoapManager(source, False)
        target = SimpleCtrDevice(json_string=donor_json)
        soap_target = CtrSoapManager(target, False)

        helpers.CtrSoapUseSystemApps(soap_source, helpers.SysApps.SYSTRANSFER)
        helpers.CtrSoapUseSystemApps(soap_target, helpers.SysApps.SYSTRANSFER)

        result_string += "Initializing console sessions...\n"
        helpers.CtrSoapSessionConnect(soap_source)
        helpers.CtrSoapSessionConnect(soap_target)

        source_json = source.serialize_json()
        donor_json = target.serialize_json()

        result_string += "Checking if we can do a transfer...\n"
        ias.MoveAccount(
            soap_source,
            soap_target.device_id,
            soap_target.account_id,
            soap_target.st_token,
            True,
        )

        result_string += "Performing transfer...\n"
        ias.MoveAccount(
            soap_source,
            soap_target.device_id,
            soap_target.account_id,
            soap_target.st_token,
            False,
        )

        result_string += "System transfer complete!"
        return source_json, donor_json, result_string

    def do_transfer_with_donor(
        self, source_json: str, resultStr: str
    ) -> tuple[str, str, str]:
        myDB = the_db()
        source_json_object = json.loads(source_json)
        donor_json_name, donor_json = myDB.get_donor_json_ready_for_transfer()

        if json.loads(donor_json)["region"] != source_json_object["region"]:
            resultStr += "Source and target regions do not match, changing...\n"
            donor_json, resultStr = self.eshop_region_change(
                json_string=donor_json,
                region=source_json_object["region"],
                country=source_json_object["country"],
                language=source_json_object["language"],
                result_string=resultStr,
            )

        source_json, donor_json, resultStr = self.do_system_transfer(
            source_json=source_json, donor_json=donor_json, result_string=resultStr
        )
        donor_json = self.clean_json(donor_json)
        myDB.update_donor(donor_json_name, donor_json)
        self.refresh_donor_lt_time(donor_json_name)

        return source_json, donor_json_name, resultStr

    def refresh_donor_lt_time(self, name: str) -> None:
        myDB = the_db()
        myDB.cursor.execute(
            "SELECT * FROM donors WHERE name = %s",
            (name,),
        )
        donor_json = myDB.cursor.fetchone()[1]
        last_transferred = self.get_last_moved_time(donor_json)
        myDB.cursor.execute(
            "UPDATE donors SET last_transferred = %s WHERE name = %s",
            (last_transferred, name),
        )
        myDB.connection.commit()

    def clean_json(self, json_string: str) -> str:
        json_object = json.loads(json_string)
        try:
            del json_object["titles"]
        except Exception:
            pass
        return json.dumps(json_object)


def _run_unregister(
    device: SimpleCtrDevice, soap_device: CtrSoapManager, result_string: str
) -> str:
    try:
        ias.Unregister(soap_device, ias.GetChallenge(soap_device).challenge)
        soap_device.unregister_account()
        virtual = False
    except SoapCodeError as e:
        if e.soaperrorcode != 434:
            raise
        virtual = True

    if virtual:
        result_string += "Virtual account link! Attempt detach by error..."
        device.reboot()

        result_string += "Initializing console session..."
        helpers.CtrSoapUseSystemApps(soap_device, helpers.SysApps.SYSTRANSFER)
        helpers.CtrSoapSessionConnect(soap_device)

        device_ninja = NinjaManager(soap_device, False)
        try:
            device_ninja.open_without_nna()
        except NinjaException as e:
            if e.errorcode != 3136:
                raise

        device.reboot()

        helpers.CtrSoapUseSystemApps(soap_device, helpers.SysApps.ESHOP)

        helpers.CtrSoapCheckRegister(soap_device)

        if soap_device.account_status != "U":
            result_string += "Unregister..."
            ias.Unregister(soap_device, ias.GetChallenge(soap_device).challenge)
            soap_device.unregister_account()
        else:
            result_string += "Unregistered!"
    return result_string
