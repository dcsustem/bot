import asyncio
import json
import logging
import re
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import BusinessConnection, Message

# ====== НАСТРОЙКИ ======
BOT_TOKEN = "8922898637:AAH4ew8WJFu3FwZKd9qOqPAsNhDXFgUMNvA"
ADMIN_ID = 6388581040
DENY_TEXT = "чел ты не админ иди на#уй"
DATA_FILE = Path(__file__).with_name("data.json")
# ========================

logging.basicConfig(level=logging.INFO)

biz = Router()    # события бизнес-подключений
admin = Router()  # команды админа
user = Router()   # все остальные
admin.message.filter(F.from_user.id == ADMIN_ID)

HELP = (
    "Команды (qzer — @имя или ссылка t.me/имя):\n\n"
    "/group qzer — проверить группу и дать ссылку, чтобы добавить бота\n"
    "/leave qzer — бот выходит из группы\n"
    "/sms qzer сообщение — написать от имени подключённого аккаунта\n"
    "/accounts — список подключённых аккаунтов\n"
    "/use номер — выбрать аккаунт для /sms"
)
NO_ACC = (
    "Нет подключённых аккаунтов.\n"
    "Подключить: Настройки → Автоматизация чатов (или Настройки → Telegram "
    "для бизнеса → Автоматизация чатов) → выбрать этого бота."
)


# ---------- хранилище ----------
def load():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"connections": {}, "contacts": {}, "current": None}


def save(data):
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------- помощники ----------
def parse_target(raw):
    """qzer / @qzer / t.me/qzer / https://t.me/qzer / числовой id -> '@qzer' или int"""
    raw = (raw or "").strip()
    if re.fullmatch(r"-?\d{5,}", raw):
        return int(raw)
    m = re.fullmatch(
        r"(?:https?://)?(?:www\.)?(?:t\.me/|telegram\.me/)?@?([A-Za-z0-9_]{4,32})/?",
        raw,
    )
    return "@" + m.group(1) if m else None


def pick_connection(data):
    conns = data["connections"]
    if not conns:
        return None, NO_ACC
    cur = data.get("current")
    if cur in conns:
        return cur, None
    if len(conns) == 1:
        return next(iter(conns)), None
    return None, "Подключено несколько аккаунтов. Смотри /accounts и выбери через /use номер"


def find_chat_id(data, conn_id, target):
    if isinstance(target, int):
        return target
    return data["contacts"].get(conn_id, {}).get(target.lower())


# ---------- бизнес-события ----------
@biz.business_connection()
async def on_connection(conn: BusinessConnection, bot: Bot):
    data = load()
    u = conn.user
    uname = f"@{u.username}" if u.username else "без username"

    if conn.is_enabled:
        data["connections"][conn.id] = {
            "user_id": u.id,
            "name": u.full_name,
            "username": uname,
        }
        text = f"✅ Подключён аккаунт {u.full_name} ({uname}), id {u.id}"
        # честно сообщаем владельцу аккаунта, что именно подключено
        try:
            await bot.send_message(
                conn.user_chat_id,
                "Бот подключён к вашему аккаунту и может отправлять сообщения "
                "от вашего имени. Отключить: Настройки → Автоматизация чатов.",
            )
        except TelegramAPIError:
            pass
    else:
        data["connections"].pop(conn.id, None)
        data["contacts"].pop(conn.id, None)
        if data.get("current") == conn.id:
            data["current"] = None
        text = f"❌ Аккаунт {u.full_name} ({uname}) отключил бота"

    save(data)
    try:
        await bot.send_message(ADMIN_ID, text)
    except TelegramAPIError:
        pass


@biz.business_message()
async def on_biz_message(m: Message):
    """Запоминаем @username собеседников, чтобы потом писать им через /sms."""
    c = m.chat
    if not c.username:
        return
    data = load()
    book = data["contacts"].setdefault(m.business_connection_id, {})
    key = "@" + c.username.lower()
    if book.get(key) != c.id:
        book[key] = c.id
        save(data)


# ---------- команды админа ----------
@admin.message(Command("start", "help"))
async def cmd_start(m: Message):
    await m.answer(HELP)


@admin.message(Command("accounts"))
async def cmd_accounts(m: Message):
    data = load()
    if not data["connections"]:
        return await m.answer(NO_ACC)
    lines = []
    for i, (cid, c) in enumerate(data["connections"].items(), 1):
        mark = "👉 " if cid == data.get("current") else ""
        lines.append(f"{mark}{i}. {c['name']} ({c['username']})")
    await m.answer("\n".join(lines))


@admin.message(Command("use"))
async def cmd_use(m: Message, command: CommandObject):
    data = load()
    ids = list(data["connections"])
    try:
        n = int(command.args)
        if n < 1:
            raise IndexError
        cid = ids[n - 1]
    except (TypeError, ValueError, IndexError):
        return await m.answer("Использование: /use 1 (номер из /accounts)")
    data["current"] = cid
    save(data)
    await m.answer(f"Выбран аккаунт: {data['connections'][cid]['name']}")


@admin.message(Command("group"))
async def cmd_group(m: Message, command: CommandObject, bot: Bot):
    target = parse_target(command.args)
    if target is None:
        return await m.answer("Использование: /group @qzer")
    try:
        chat = await bot.get_chat(target)
    except TelegramAPIError:
        return await m.answer("Не нашёл такую группу. Она должна быть публичной (@имя).")

    me = await bot.me()
    try:
        member = await bot.get_chat_member(chat.id, me.id)
        if member.status in ("member", "administrator", "creator"):
            return await m.answer(f"Бот уже в «{chat.title}».")
    except TelegramAPIError:
        pass

    await m.answer(
        f"Нашёл «{chat.title}». Сам вступить бот не может (ограничение Telegram), "
        f"его нужно добавить вручную:\nhttps://t.me/{me.username}?startgroup=true"
    )


@admin.message(Command("leave"))
async def cmd_leave(m: Message, command: CommandObject, bot: Bot):
    target = parse_target(command.args)
    if target is None:
        return await m.answer("Использование: /leave @qzer")
    try:
        chat = await bot.get_chat(target)
        await bot.leave_chat(chat.id)
    except TelegramAPIError as e:
        return await m.answer(f"Не получилось: {e.message}")
    await m.answer(f"Вышел из «{chat.title}».")


@admin.message(Command("sms"))
async def cmd_sms(m: Message, command: CommandObject, bot: Bot):
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) < 2:
        return await m.answer("Использование: /sms @qzer текст сообщения")
    target = parse_target(parts[0])
    if target is None:
        return await m.answer("Не понял, кому писать. Пример: @qzer или t.me/qzer")
    text = parts[1]

    data = load()
    conn_id, err = pick_connection(data)
    if err:
        return await m.answer(err)

    chat_id = find_chat_id(data, conn_id, target)
    if chat_id is None:
        return await m.answer(
            "Этого человека нет в чатах аккаунта. Telegram позволяет писать только "
            "в личные чаты, где недавно была переписка. Пусть он сначала напишет "
            "аккаунту, или укажи числовой id."
        )

    try:
        await bot.send_message(
            chat_id=chat_id, text=text, business_connection_id=conn_id
        )
    except TelegramAPIError as e:
        return await m.answer(f"Не отправилось: {e.message}")
    await m.answer(f"✅ Отправлено от имени {data['connections'][conn_id]['name']}")


@admin.message(F.chat.type == "private")
async def admin_other(m: Message):
    await m.answer(HELP)


# ---------- не админ ----------
@user.message(
    (F.from_user.id != ADMIN_ID)
    & ((F.chat.type == "private") | F.text.startswith("/"))
)
async def deny(m: Message):
    await m.answer(DENY_TEXT)


async def main():
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_routers(biz, admin, user)
    print("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
