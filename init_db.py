#!/usr/bin/env python3
"""
Initialize the database schema for Proper_Diagnosis.

Supports:
- create: Create all tables defined in SQLAlchemy models if missing
- drop: Drop all tables defined in SQLAlchemy models
- reset: Drop then create (fresh schema)
- seed: Seed minimal data (roles + admin) using models.init_db()
- inspect: Print current tables and key columns

Note: If an old/legacy 'users' table exists without an 'id' primary key,
	  --create will NOT modify it. Use --reset to recreate from models.
"""

from __future__ import annotations

import argparse
import os
from typing import Sequence

from dotenv import load_dotenv
from sqlalchemy import inspect

from models import Base, engine, init_db as seed_roles_admin


def _echo_env() -> None:
	# Render URL without password
	try:
		print("DATABASE_URL:", engine.url.render_as_string(hide_password=True))
	except Exception:
		print("DATABASE_URL: <unavailable>")


def create_tables() -> None:
	print("Creating tables (if not present)...")
	Base.metadata.create_all(bind=engine)
	print("Done.")


def drop_tables() -> None:
	print("Dropping tables defined by models...")
	Base.metadata.drop_all(bind=engine)
	print("Done.")


def seed() -> None:
	print("Seeding roles/admin (idempotent)...")
	seed_roles_admin()
	print("Done.")


def inspect_db() -> None:
	insp = inspect(engine)
	try:
		tables: Sequence[str] = sorted(insp.get_table_names())
	except Exception as e:
		print("Failed to list tables:", e)
		return

	print("Tables:", ", ".join(tables) if tables else "<none>")

	def cols(tbl: str) -> None:
		try:
			cs = insp.get_columns(tbl)
			names = [c.get("name", "?") for c in cs]
			print(f"- {tbl}: {', '.join(names) if names else '<no columns>'}")
		except Exception as e:
			print(f"- {tbl}: <error> {e}")

	for t in ("users", "roles", "user_roles", "otps", "institutions", "conversations", "conversation_owners", "messages", "screening_events"):
		if t in tables:
			cols(t)

	# Check alembic_version presence
	if "alembic_version" in tables:
		try:
			with engine.connect() as conn:
				rows = conn.exec_driver_sql("select version_num from alembic_version").fetchall()
			print("alembic_version:", [r[0] for r in rows])
		except Exception as e:
			print("alembic_version read error:", e)


def main() -> int:
	load_dotenv()
	if not os.getenv("DATABASE_URL"):
		print("DATABASE_URL not set. Create a .env or export it before running.")
		return 2

	parser = argparse.ArgumentParser(description="Initialize database schema")
	g = parser.add_mutually_exclusive_group(required=True)
	g.add_argument("--create", action="store_true", help="Create missing tables")
	g.add_argument("--drop", action="store_true", help="Drop tables defined by models")
	g.add_argument("--reset", action="store_true", help="Drop and recreate tables")
	g.add_argument("--seed", action="store_true", help="Seed roles/admin only")
	g.add_argument("--inspect", action="store_true", help="Print current tables and columns")
	args = parser.parse_args()

	_echo_env()

	if args.inspect:
		inspect_db()
		return 0

	if args.drop:
		drop_tables()
		return 0

	if args.create:
		create_tables()
		return 0

	if args.reset:
		drop_tables()
		create_tables()
		return 0

	if args.seed:
		seed()
		return 0

	return 0


if __name__ == "__main__":
	raise SystemExit(main())

