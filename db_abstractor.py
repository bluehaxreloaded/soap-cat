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
        self, name: str, json: str, last_transferred: int, uploader: str, note: str
    ) -> None:
        sql = "INSERT INTO donors (name, json_data, last_transferred, uploader, note) VALUES (%s, %s, %s, %s, %s)"
        val = (name, json, last_transferred, uploader, note)

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
            "SELECT * FROM donors WHERE last_transferred < %s ORDER BY last_transferred ASC",
            (utc_time_ready_for_transfer,),
        )
        result = self.cursor.fetchone()

        try:
            self.cursor.fetchall()
        except Exception:
            pass

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
