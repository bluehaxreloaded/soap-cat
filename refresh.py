import warnings

# cryptography 46 warns on every sect233r1 use that it's going away. It's the curve the 3DS uses, see requirements.txt
warnings.filterwarnings("ignore", message=r"SECT\d+R\d+ will be removed")
from db_abstractor import the_db
from cleaninty_abstractor import cleaninty_abstractor

cleaninty = cleaninty_abstractor()
myDB = the_db()

myDB.cursor.execute("SELECT * FROM donors ORDER BY last_transferred ASC")
donors = myDB.cursor.fetchall()

for i in range(len(donors)):
    print(f"refreshing {donors[i][0]}")
    cleaninty.refresh_donor_lt_time(donors[i][0])

myDB.exit()
