from db_abstractor import the_db
from cleaninty_abstractor import cleaninty_abstractor

cleaninty = cleaninty_abstractor()
myDB = the_db()

donors = the_db().read_donor_table()

for i in range(len(donors)):
    print(donors[i][0])
    cleaninty.refresh_donor_lt_time(donors[i][0])

myDB.exit()
