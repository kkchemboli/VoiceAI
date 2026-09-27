"""Generate a bcrypt digest for ADMIN_PASSWORD_HASH.

Run this to create the single admin credential:

    python backend/tools/hash_password.py

It prompts for the password without echoing it. The digest it prints is what
goes in the environment; the password itself never needs to be written down
anywhere, which is the point -- a plaintext password in an env file or a leaked
repo is immediately usable by whoever reads it, a bcrypt digest is not.

The prompt uses getpass, so the password is not echoed to the terminal or kept
in shell history. To generate a hash non-interactively (for example in a
provisioning script), pipe it in on stdin:

    printf '%s' "$PASSWORD" | python backend/tools/hash_password.py
"""

import getpass
import sys


def main() -> int:
    try:
        import bcrypt
    except ImportError:
        print(
            "bcrypt is not installed. Install the backend requirements first, "
            "or run this inside the api image:\n"
            "  docker compose run --rm api python backend/tools/hash_password.py",
            file=sys.stderr,
        )
        return 1

    if sys.stdin.isatty():
        password = getpass.getpass("Admin password: ")
        if not password:
            print("No password entered.", file=sys.stderr)
            return 1
        # Catch a typo'd confirmation before it becomes an unloginable account.
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords did not match.", file=sys.stderr)
            return 1
    else:
        # Non-interactive: read a single line, strip only the trailing newline
        # so a password that legitimately contains a space still works.
        password = sys.stdin.readline().rstrip("\n")
        if not password:
            print("No password on stdin.", file=sys.stderr)
            return 1

    digest = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    print()
    print("ADMIN_USERNAME=admin")
    print(f"ADMIN_PASSWORD_HASH={digest}")
    print()
    print("Put those in your env/secret store. The password itself is not stored")
    print("anywhere. SESSION_SECRET should be a separate random value:")
    print("  openssl rand -hex 32")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
