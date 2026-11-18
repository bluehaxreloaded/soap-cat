import datetime
from dotenv import load_dotenv
import mysql.connector
import os


class the_db:
    def __init__(self):
        load_dotenv()
        self.connection = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST"),
            user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASS"),
            database=os.getenv("MYSQL_DB"),
        )
        self.cursor = self.connection.cursor()

    def exit(self) -> None:
        self.connection.commit()
        self.connection.close()
        self.cursor.close()

    def write_donor(
        self, name: str, json: str, last_transferred: int, uploader: str, note: str, status:int
    ) -> None:
        sql = "INSERT INTO donors (name, json_data, last_transferred, uploader, note, status) VALUES (%s, %s, %s, %s, %s, %s)"
        val = (name, json, last_transferred, uploader, note, status)

        self.cursor.execute(sql, val)

        self.connection.commit()

    def update_donor(self, name, json) -> None:
        sql = "UPDATE donors SET json_data = %s WHERE name = %s"
        self.cursor.execute(sql, (json, name))
        self.connection.commit()

    def get_donor_json_ready_for_transfer(self) -> tuple[str, str]:
        utc_time = datetime.datetime.now(datetime.UTC)
        utc_time_ready_for_transfer = int(utc_time.timestamp()) - 604800

        # this fixes something but i'm not sure what
        try:
            self.cursor.fetchall()
        except Exception:
            pass

        self.cursor.execute(
            "SELECT * FROM donors WHERE last_transferred < %s AND status = 0 ORDER BY last_transferred ASC",
            (utc_time_ready_for_transfer,),
        )
        result = self.cursor.fetchone()

        try:
            self.cursor.fetchall()
        except Exception:
            pass

        if result is None:
            raise Exception("No more available donors, see soapcheck")

        return result[0], result[1]

    def read_index(self, table: str, index_field_name: str, index):
        # this fixes something but i'm not sure what
        try:
            self.cursor.fetchall()
        except Exception:
            pass

        sql = f"SELECT * FROM {table} WHERE {index_field_name} = %s"
        val = (index,)

        self.cursor.execute(sql, val)

        return self.cursor.fetchone()

    def read_donor_table(self):
        """ordered by last_transferred with the oldest coming first"""

        # this fixes something but i'm not sure what
        try:
            self.cursor.fetchall()
        except Exception:
            pass

        self.cursor.execute("SELECT * FROM donors ORDER BY last_transferred ASC")

        return self.cursor.fetchall()

    def set_donor_status(self, name: str, status: int) -> None:
        # is this needed?
        try:
            self.cursor.fetchall()
        except Exception:
            pass

        self.cursor.execute(
            "UPDATE donors SET status = %s WHERE name = %s",
            (status, name),
        )
        self.connection.commit()

    def record_soap_details(self, channel_id, soaper_id, donor) -> None:
        """Save the donor and Soaper a soap used on maidy's row for the channel, so maidy records them with the SOAP.
        Does nothing until maidy has created the channels table."""
        if not channel_id:
            return
        try:
            self.cursor.execute(
                "UPDATE channels SET donor = %s, soaper_id = COALESCE(%s, soaper_id) WHERE channel_id = %s",
                (donor, soaper_id, channel_id),
            )
            self.connection.commit()
        finally:
            self.connection.close()

    def channel_helpee(self, channel_id) -> int | None:
        """Who maidy saved a soap channel as being for (see maid-cat's channel_state.py), or None if it isn't saved."""
        try:
            self.cursor.execute(
                "SELECT user_id FROM channels WHERE channel_id = %s AND status <> 'deleted'", (channel_id,)
            )
            row = self.cursor.fetchone()
            return int(row[0]) if row and row[0] else None
        finally:
            self.connection.close()

    def write_heartbeat(self, latency_ms) -> None:
        """Tell maidy soap-cat is up and connected, see maid-cat's status.py. Does nothing until maidy has created
        the bot_heartbeats table."""
        try:
            self.cursor.execute(
                """INSERT INTO bot_heartbeats (bot, beat_at, latency_ms) VALUES ('soap-cat', UTC_TIMESTAMP(), %s)
                   ON DUPLICATE KEY UPDATE beat_at = VALUES(beat_at), latency_ms = VALUES(latency_ms)""",
                (latency_ms,),
            )
            self.connection.commit()
        finally:
            self.connection.close()
