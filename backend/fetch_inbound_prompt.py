#!/usr/bin/env python3
"""Fetch and print the current inbound prompt from Supabase agent_config table."""

import os
from dotenv import load_dotenv

from services.supabase_client import get_supabase_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_KEY must be set in .env")
        return

    supabase = get_supabase_client()
    if not supabase:
        print("Error: could not initialize Supabase client; check SUPABASE_URL/SUPABASE_KEY")
        return

    response = supabase.table("agent_config").select("key", "value").execute()

    if not response.data:
        print("No entries found in agent_config table")
        return

    print("=" * 60)
    print("INBOUND PROMPT CONFIGURATION")
    print("=" * 60)

    output_lines = []

    for item in response.data:
        key = item.get("key")
        value = item.get("value")
        if key in ("system_prompt", "opening_greeting"):
            print(f"\n【{key}】")
            print("-" * 40)
            print(value if value else "(empty)")
            output_lines.append(f"=== {key} ===\n{value if value else '(empty)'}\n")

    print("\n" + "=" * 60)

    output_path = os.path.join(os.path.dirname(__file__), "..", "fetched.txt")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    print(f"\nPrompt saved to: {os.path.abspath(output_path)}")


if __name__ == "__main__":
    main()
