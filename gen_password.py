import hashlib
import os
pw = input("Введи пароль администратора: ")
salt = os.urandom(16)
key = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 260_000)
print("\nСкопируй эту строку в .env:\n")
print(f"SETTINGS_PASSWORD_HASH=pbkdf2:{salt.hex()}:{key.hex()}")
