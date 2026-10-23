import sqlite3
import json
from collections import Counter

c_before = sqlite3.connect("before_seed.sqlite3")
c_cur = sqlite3.connect("db.sqlite3")

# 1. Total counts
b_att = c_before.execute("SELECT COUNT(*) FROM attendance_attendance").fetchone()[0]
c_att = c_cur.execute("SELECT COUNT(*) FROM attendance_attendance").fetchone()[0]

print(f"Total attendance in before_seed: {b_att}")
print(f"Total attendance in db.sqlite3: {c_att}")

# Check attendance notes in before_seed
b_notes = c_before.execute("SELECT note, count(*) FROM attendance_attendance GROUP BY note ORDER BY count(*) DESC").fetchall()
print("\nTop notes in before_seed.sqlite3:")
for n, cnt in b_notes[:10]:
    print(f"  [{cnt}]: {repr(n)}")

# Check attendance notes in db.sqlite3
c_notes = c_cur.execute("SELECT note, count(*) FROM attendance_attendance GROUP BY note ORDER BY count(*) DESC").fetchall()
print("\nTop notes in db.sqlite3:")
for n, cnt in c_notes[:10]:
    print(f"  [{cnt}]: {repr(n)}")

# Check addresses in attendance_attendancelocation
b_locs = c_before.execute("SELECT address, count(*) FROM attendance_attendancelocation GROUP BY address ORDER BY count(*) DESC").fetchall()
print("\nTop location addresses in before_seed.sqlite3:")
for a, cnt in b_locs[:10]:
    print(f"  [{cnt}]: {repr(a)}")

c_locs = c_cur.execute("SELECT address, count(*) FROM attendance_attendancelocation GROUP BY address ORDER BY count(*) DESC").fetchall()
print("\nTop location addresses in db.sqlite3:")
for a, cnt in c_locs[:10]:
    print(f"  [{cnt}]: {repr(a)}")
