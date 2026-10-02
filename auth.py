"""Local accounts for ISA Terminal: username + password. There is NO password recovery.

Each person's data lives in users/<username>/ (portfolio.json, config.json). Passwords are
never stored: only a salted scrypt hash is kept in users/users.json.

Limits (be honest about them): this separates people inside the app. It does not encrypt the
files, so anyone who can read this computer's folder can read the data. A hosted, multi-user
product needs a real database, HTTPS and server-side security instead of local files.
"""

import hashlib
import hmac
import os
import re
import secrets
import shutil
import time

import isa_core as core

USERS_DIR = os.environ.get("ISA_USERS_DIR") or os.path.join(core.BASE_DIR, "users")
USERS_FILE = os.path.join(USERS_DIR, "users.json")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,19}$")
MIN_PASSWORD, MAX_PASSWORD = 8, 128
MAX_FAILS, LOCK_SECONDS = 5, 60
NO_RECOVERY = ("There is no password recovery. If you forget your password, this account and "
               "its data can't be reopened. Write it down somewhere safe.")


def _hash(password, salt, algo):
    """Derive a 32-byte key from the password (scrypt, or PBKDF2 if scrypt is unavailable)."""
    data = password.encode("utf-8")
    if algo == "scrypt":
        return hashlib.scrypt(data, salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return hashlib.pbkdf2_hmac("sha256", data, salt, 600_000, dklen=32)


def _best_algo():
    """scrypt when this Python supports it, else PBKDF2."""
    try:
        hashlib.scrypt(b"x", salt=b"y" * 16, n=2 ** 4, r=8, p=1, dklen=8)
        return "scrypt"
    except (AttributeError, ValueError):
        return "pbkdf2"


def load_users():
    """{username: record}. Empty if nobody has signed up yet."""
    data = core.read_json(USERS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_users(users):
    """Write the accounts file."""
    return core.write_json(USERS_FILE, users)


def clean_username(name):
    """Lower-case, trimmed username."""
    return (name or "").strip().lower()


def user_dir(username):
    """Folder for a (valid) username. Raises ValueError for anything that isn't a clean name,
    so a crafted username can never point outside the users folder."""
    username = clean_username(username)
    if not USERNAME_RE.match(username):
        raise ValueError("invalid username")
    return os.path.join(USERS_DIR, username)


def validate_new(username, password):
    """Message describing what's wrong with a new username/password, or None if fine."""
    if not USERNAME_RE.match(username):
        return ("Username must be 3-20 characters: lower-case letters, numbers, '.', '_' or '-' "
                "(starting with a letter or number).")
    if len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    if len(password) > MAX_PASSWORD:
        return f"Password must be at most {MAX_PASSWORD} characters."
    if password.lower() == username:
        return "Password can't be the same as the username."
    return None


def create_account(username, password, display_name=""):
    """Create an account with an empty portfolio. Returns (ok, message)."""
    username = clean_username(username)
    problem = validate_new(username, password)
    if problem:
        return False, problem
    users = load_users()
    if username in users:
        return False, "That username is taken."
    algo, salt = _best_algo(), secrets.token_bytes(16)
    users[username] = {"algo": algo, "salt": salt.hex(), "hash": _hash(password, salt, algo).hex(),
                       "created": core.iso_now(), "fails": 0, "locked_until": 0}
    folder = user_dir(username)
    os.makedirs(folder, exist_ok=True)
    config = core.load_config(folder)
    config["display_name"] = display_name.strip()[:40]
    core.save_config(config, folder)
    core.load_portfolio(folder)  # creates an empty portfolio.json
    if not save_users(users):
        return False, "Could not save the account (check folder permissions)."
    return True, "Account created."


def verify_login(username, password):
    """Check a login. Returns (ok, message). Locks the account for a minute after 5 wrong
    guesses. The error message is the same for unknown users and wrong passwords."""
    username = clean_username(username)
    users = load_users()
    record = users.get(username)
    if record is None:
        _hash(password, b"0" * 16, _best_algo())  # similar timing whether or not the user exists
        return False, "Incorrect username or password."
    wait = record.get("locked_until", 0) - time.time()
    if wait > 0:
        return False, f"Too many attempts. Try again in {int(wait) + 1} seconds."
    expected = bytes.fromhex(record["hash"])
    actual = _hash(password, bytes.fromhex(record["salt"]), record.get("algo", "scrypt"))
    if hmac.compare_digest(expected, actual):
        record["fails"], record["locked_until"] = 0, 0
        save_users(users)
        return True, "Logged in."
    record["fails"] = record.get("fails", 0) + 1
    if record["fails"] >= MAX_FAILS:
        record["fails"], record["locked_until"] = 0, time.time() + LOCK_SECONDS
    save_users(users)
    return False, "Incorrect username or password."


def change_password(username, old_password, new_password):
    """Change a password (needs the current one). Returns (ok, message)."""
    ok, message = verify_login(username, old_password)
    if not ok:
        return False, "Current password is incorrect." if "Incorrect" in message else message
    problem = validate_new(clean_username(username), new_password)
    if problem:
        return False, problem
    users = load_users()
    algo, salt = _best_algo(), secrets.token_bytes(16)
    users[clean_username(username)].update(
        {"algo": algo, "salt": salt.hex(), "hash": _hash(new_password, salt, algo).hex()})
    save_users(users)
    return True, "Password changed."


def delete_account(username, password):
    """Permanently delete an account and all of its data (needs the password).
    Returns (ok, message)."""
    ok, message = verify_login(username, password)
    if not ok:
        return False, message
    username = clean_username(username)
    folder = os.path.realpath(user_dir(username))
    if os.path.dirname(folder) != os.path.realpath(USERS_DIR):  # safety: stay inside users/
        return False, "Refusing to delete outside the users folder."
    users = load_users()
    users.pop(username, None)
    save_users(users)
    shutil.rmtree(folder, ignore_errors=True)
    return True, "Account and data deleted."
