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
