import os
import datetime
from database import LicenseDB

def test_flow():
    test_db_path = "test_licenses2.db"
    if os.path.exists(test_db_path):
        os.remove(test_db_path)

    db = LicenseDB(test_db_path)

    # 1. Assign lifetime key
    key1, is_new, exp, dur = db.assign_key("111", "User1", days=0)
    print(f"[TEST] Lifetime key: {key1}, exp={exp}, dur={dur}")
    assert exp is None
    assert dur == "lifetime"

    ok, msg = db.verify(key1, "hwid1")
    assert ok == True
    print(f"[TEST] Lifetime verify: ok={ok}, msg='{msg}'")

    # 2. Assign 30 days key
    key2, is_new2, exp2, dur2 = db.assign_key("222", "User2", days=30)
    print(f"[TEST] 30d key: {key2}, exp={exp2}, dur={dur2}")
    assert exp2 is not None
    assert dur2 == "30d"

    ok, msg = db.verify(key2, "hwid2")
    assert ok == True
    print(f"[TEST] 30d verify: ok={ok}, msg='{msg}'")

    # 3. Simulate expired key (-1 days)
    key3, _, exp3, _ = db.assign_key("333", "User3", days=-1)
    # Manually set expired timestamp in past
    past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)).isoformat()
    with db._get_conn() as conn:
        conn.execute("UPDATE licenses SET expires_at = ? WHERE key = ?", (past, key3))

    ok, msg = db.verify(key3, "hwid3")
    print(f"[TEST] Expired key verify: ok={ok}, msg='{msg}'")
    assert ok == False
    assert "expired" in msg.lower()

    # 4. Clean up
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
    print("\nALL EXPIRY AND ASSIGNMENT TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_flow()