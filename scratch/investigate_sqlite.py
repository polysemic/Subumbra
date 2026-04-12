
import sqlite3
import multiprocessing
import os
import time

DB_PATH = "scratch/test_nonce.db"

def init_db():
    if os.path.exists(DB_PATH): os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE nonces (nonce TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

def attempt_insert(nonce, results):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        cur = conn.execute("INSERT OR IGNORE INTO nonces (nonce) VALUES (?)", (nonce,))
        conn.commit()
        results.put(cur.rowcount)
    except Exception as e:
        results.put(str(e))
    finally:
        conn.close()

def run_test():
    init_db()
    results = multiprocessing.Queue()
    procs = []
    
    # Simulate 10 processes hitting the same nonce at the exact same time
    for _ in range(10):
        p = multiprocessing.Process(target=attempt_insert, args=("test_nonce", results))
        procs.append(p)
        p.start()
        
    for p in procs:
        p.join()
        
    success_count = 0
    errors = []
    while not results.empty():
        res = results.get()
        if res == 1:
            success_count += 1
        elif res == 0:
            pass
        else:
            errors.append(res)
            
    print(f"Success Count: {success_count}")
    print(f"Total Procs: 10")
    print(f"Errors: {errors}")

if __name__ == "__main__":
    run_test()
