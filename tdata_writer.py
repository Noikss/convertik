"""
tdata_writer.py — конвертация .session (Telethon) -> tdata (Telegram Desktop)
Без PyQt5, без opentele. Чистый Python + tgcrypto + telethon.
"""

import os, io, struct, hashlib, sqlite3, json, asyncio
from pathlib import Path
import tgcrypto

TDF_MAGIC   = b"TDF$"
APP_VERSION = 3004000
dbi_MtpAuthorization = 0x4B
dbi_User             = 0x70


class DataStream:
    """Эмуляция QDataStream (Qt_5_1)"""
    def __init__(self):
        self._buf = io.BytesIO()

    def write_uint32(self, v: int):
        self._buf.write(struct.pack(">I", v))

    def write_int32(self, v: int):
        self._buf.write(struct.pack(">i", v))

    def write_bytes(self, data: bytes):
        if data is None or len(data) == 0:
            self._buf.write(struct.pack(">I", 0xFFFFFFFF))
        else:
            self._buf.write(struct.pack(">I", len(data)))
            self._buf.write(data)

    def write_raw(self, data: bytes):
        self._buf.write(data)

    def getvalue(self) -> bytes:
        return self._buf.getvalue()


def _prepare_aes(auth_key: bytes, key128: bytes) -> tuple:
    pos = 88  # receive mode
    sha1_a = hashlib.sha1(key128[:16] + auth_key[pos:pos+32]).digest()
    sha1_b = hashlib.sha1(auth_key[pos+32:pos+48] + key128[16:] + auth_key[pos+48:pos+64]).digest()
    sha1_c = hashlib.sha1(auth_key[pos+64:pos+96] + key128[:16]).digest()
    sha1_d = hashlib.sha1(key128[16:] + auth_key[pos+96:pos+128]).digest()
    aes_key = sha1_a[:8] + sha1_b[8:20] + sha1_c[4:16]
    aes_iv  = sha1_a[8:20] + sha1_b[:8] + sha1_c[16:20] + sha1_d[:8]
    return bytes(aes_key), bytes(aes_iv)


def encrypt_local(plaintext: bytes, auth_key: bytes) -> bytes:
    """PrepareEncrypted аналог"""
    size = len(plaintext)
    full_size = size + 4
    if full_size & 0x0F:
        full_size += 0x10 - (full_size & 0x0F)
    padded = size.to_bytes(4, "little") + plaintext + os.urandom(full_size - size - 4)
    sha1_hash = hashlib.sha1(padded).digest()
    key128 = sha1_hash[:16]
    aes_key, aes_iv = _prepare_aes(auth_key, key128)
    return key128 + tgcrypto.ige256_encrypt(padded, aes_key, aes_iv)


def create_local_key(salt: bytes, passcode: bytes = b"") -> bytes:
    h = hashlib.sha512(salt + passcode + salt).digest()
    iters = 1 if not passcode else 100000
    return hashlib.pbkdf2_hmac("sha512", h, salt, iters, 256)


def write_tdf(path: Path, data: bytes):
    ver = APP_VERSION.to_bytes(4, "little")
    md5_src = data + len(data).to_bytes(4, "little") + ver + TDF_MAGIC
    checksum = hashlib.md5(md5_src).digest()
    content = TDF_MAGIC + ver + data + checksum
    path.write_bytes(content)
    (path.parent / (path.name + "s")).write_bytes(content)


def read_session(path: str) -> dict:
    conn = sqlite3.connect(path)
    row  = conn.execute("SELECT dc_id, server_address, port, auth_key FROM sessions LIMIT 1").fetchone()
    conn.close()
    if not row:
        raise ValueError("sessions таблица пустая")
    dc_id, srv, port, key = row
    return {"dc_id": dc_id, "server_address": srv, "port": port,
            "auth_key": bytes(key) if isinstance(key, memoryview) else key}


def build_tdata(session_data: dict, user_id: int, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    auth_key = session_data["auth_key"]
    if isinstance(auth_key, str):
        auth_key = bytes.fromhex(auth_key)
    auth_key = (auth_key + bytes(256))[:256]
    dc_id = session_data["dc_id"]

    salt      = os.urandom(32)
    local_key = create_local_key(salt)

    # ── MTP авторизация ───────────────────────────────────────────────────────
    mtp = DataStream()
    mtp.write_uint32(user_id & 0xFFFFFFFF)
    mtp.write_uint32(dc_id)
    mtp.write_uint32(1)          # кол-во ключей
    mtp.write_int32(dc_id)
    mtp.write_raw(auth_key)
    mtp_bytes = mtp.getvalue()

    # ── map файл ─────────────────────────────────────────────────────────────
    map_inner = DataStream()
    map_inner.write_uint32(dbi_MtpAuthorization)
    map_inner.write_bytes(mtp_bytes)
    enc_map = encrypt_local(map_inner.getvalue(), local_key)

    map_stream = DataStream()
    map_stream.write_bytes(salt)
    map_stream.write_bytes(b"\xff\xff\xff\xff")  # пустой legacyKey placeholder
    map_stream.write_bytes(enc_map)
    write_tdf(out / "map", map_stream.getvalue())

    # ── key_data файл ────────────────────────────────────────────────────────
    passcode_key = create_local_key(salt)  # без пароля = тот же ключ
    enc_key      = encrypt_local(local_key, passcode_key)

    key_stream = DataStream()
    key_stream.write_bytes(salt)
    key_stream.write_bytes(enc_key)
    write_tdf(out / "key_data", key_stream.getvalue())

    # ── settings ──────────────────────────────────────────────────────────────
    sett = DataStream()
    sett.write_uint32(dbi_User)
    sett.write_int32(user_id & 0x7FFFFFFF)
    sett.write_uint32(dc_id)
    write_tdf(out / "settings", sett.getvalue())


async def convert_session_to_tdata(session_path, output_dir, api_id, api_hash, proxy=None):
    from telethon import TelegramClient
    session_base = str(session_path).replace(".session", "")
    try:
        session_data = read_session(str(session_path))
    except Exception as e:
        return {"ok": False, "error": f"Ошибка чтения session: {e}"}
    try:
        kw = dict(session=session_base, api_id=api_id, api_hash=api_hash)
        if proxy:
            kw["proxy"] = proxy
        client = TelegramClient(**kw)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return {"ok": False, "error": "Аккаунт не авторизован"}
        me = await client.get_me()
        await client.disconnect()
        session_data = read_session(str(session_path))
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        build_tdata(session_data, me.id, Path(output_dir))
        return {"ok": True, "user": f"@{me.username}" if me.username else f"id{me.id}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
