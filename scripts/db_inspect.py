#!/usr/bin/env python3
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text


def main() -> int:
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set. Check your .env or environment.")
        return 1

    engine = create_engine(db_url, future=True)
    print("Connected to:", engine.url.render_as_string(hide_password=True))
    insp = inspect(engine)

    try:
        tables = sorted(insp.get_table_names())
        print("Tables:", ", ".join(tables))
    except Exception as e:
        print("Error listing tables:", e)

    def show_table(name: str):
        try:
            cols = insp.get_columns(name)
            print(f"\n{name} columns:")
            for c in cols:
                pk = " PK" if c.get("primary_key") else ""
                print(f" - {c['name']} ({str(c['type'])}){pk}")
            try:
                pkc = insp.get_pk_constraint(name)
                print("Primary key:", pkc)
            except Exception as e:
                print("PK inspect error:", e)
        except Exception as e:
            print(f"No table named '{name}' or failed to inspect: {e}")

    for t in ("users", "roles", "user_roles", "otps", "conversations", "conversation_owners"):
        show_table(t)

    # Show alembic version, if present
    try:
        with engine.connect() as conn:
            res = conn.execute(text("SELECT version_num FROM alembic_version"))
            rows = res.fetchall()
            print("\nalembic_version:", [r[0] for r in rows])
    except Exception as e:
        print("alembic_version read error:", e)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
