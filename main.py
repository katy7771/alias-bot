import telebot
import os
import random
import threading
import time
from telebot import types
from telebot.apihelper import ApiTelegramException
from typing import Dict, List, Set, Optional, Any
import flask

# --- Bot and Global Variables Initialization ---
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN is not set in Secrets")

# CHANGED: We now read the webhook URL from a secret that you will set.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") 
WEBHOOK_PATH = f"/{TOKEN}"

bot = telebot.TeleBot(TOKEN)
app = flask.Flask(__name__)

# (The rest of the global variables and core functions are unchanged)
teams: Dict[str, List[int]] = {}
user_teams: Dict[int, str] = {}
teams_score: Dict[str, int] = {}
teams_order: List[str] = []
current_chat_id: int = 0
game_active: bool = False
round_in_progress: bool = False
active_player_id: Optional[int] = None
used_words: List[str] = []
available_words: List[str] = []
player_states: Dict[int, Dict[str, Any]] = {}
played_teams: Set[str] = set()
current_turn_index: int = 0
group_timer_message_id: Optional[int] = None
TEAM_EMOJIS = ['🚀', '🦅', '🔥', '⚡️', '🏆', '🎯', '🦁', '🐺', '🌟', '💎']
team_emojis: Dict[str, str] = {}
ROUND_TIME, ROUND_LIMIT = 60, 10
try:
    with open("words.txt", encoding="utf-8") as f:
        all_words = [line.strip() for line in f if line.strip()]
except FileNotFoundError:
    all_words = ["чат", "дзвінок", "підтримка", "запит", "email"]

# ===============================================================
# === 1. Core Game Logic Functions (No Changes Here) ===
# ===============================================================
def _get_team_display_name(team_name: str) -> str:
    emoji = team_emojis.get(team_name, "🔹")
    return f"{emoji} {team_name}"
def _create_word_buttons() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(types.InlineKeyboardButton("✅ Вгадано", callback_data="right"),
               types.InlineKeyboardButton("❌ Ні", callback_data="wrong"),
               types.InlineKeyboardButton("🔁 Пропустити", callback_data="skip"))
    return markup
def finish_game(chat_id: Optional[int] = None, silent: bool = False):
    global game_active, round_in_progress, active_player_id, teams, user_teams, teams_score, teams_order, played_teams, used_words, available_words, player_states, current_turn_index, group_timer_message_id, team_emojis
    target_chat_id = chat_id or current_chat_id
    if active_player_id and active_player_id in player_states:
        player_states[active_player_id]["timer_active"] = False
    if not silent and target_chat_id != 0 and any(teams_score.values()):
        summary = "🏁 *Гру завершено!*\n\n"
        winner = max(teams_score, key=lambda k: teams_score[k])
        for team, score in teams_score.items():
            summary += f"{_get_team_display_name(team)}: *{score}* балів\n"
        summary += f"\n🥇 Перемогла команда *{_get_team_display_name(winner)}*!\n🎁 Бонус +30 хв отримують:\n"
        if teams.get(winner):
            for uid in teams[winner]:
                try:
                    name = bot.get_chat(uid).username or bot.get_chat(uid).first_name
                    summary += f"- @{name}\n"
                except ApiTelegramException:
                    summary += f"- User {uid}\n"
        bot.send_message(target_chat_id, summary, parse_mode="Markdown")
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🎉 Розпочати нову гру", callback_data="setup_new_game"))
        bot.send_message(target_chat_id, "Дякуємо за гру! Бажаєте зіграти ще раз?", reply_markup=markup)
    teams, user_teams, teams_score, teams_order, team_emojis = {}, {}, {}, [], {}
    played_teams, used_words, available_words, player_states = set(), [], [], {}
    game_active, round_in_progress, active_player_id = False, False, None
    current_turn_index = 0
    group_timer_message_id = None
def show_score():
    summary = "📊 *Поточний рахунок:*\n"
    for team_name in teams_order:
        score = teams_score.get(team_name, 0)
        summary += f"{_get_team_display_name(team_name)}: *{score}* балів\n"
    if current_chat_id != 0: bot.send_message(current_chat_id, summary, parse_mode="Markdown")
def update_timer_thread(user_id: int, start_time: float):
    markup = _create_word_buttons()
    while time.time() < start_time + ROUND_TIME:
        state = player_states.get(user_id)
        if not state or not state.get("timer_active"): break
        remaining_time = int((start_time + ROUND_TIME) - time.time())
        if remaining_time < 0: remaining_time = 0
        new_text = f"🔤 Слово: *{state['current_word'].upper()}*\n⏱️ Залишилось: {remaining_time} сек"
        group_timer_text = f"⏳ Залишилось часу: *{remaining_time}* сек"
        try:
            if state.get("player_message_id"):
                if state.get("message_type") == "photo": bot.edit_message_caption(caption=new_text, chat_id=user_id, message_id=state["player_message_id"], parse_mode="Markdown", reply_markup=markup)
                else: bot.edit_message_text(text=new_text, chat_id=user_id, message_id=state["player_message_id"], parse_mode="Markdown", reply_markup=markup)
            if group_timer_message_id and current_chat_id: bot.edit_message_text(text=group_timer_text, chat_id=current_chat_id, message_id=group_timer_message_id, parse_mode="Markdown")
        except ApiTelegramException as e:
            if 'message is not modified' not in e.description: print(f"Timer update error: {e}")
        time.sleep(1)
    state = player_states.get(user_id)
    if state and state.get("timer_active"): end_round(user_id, state["score"])
def send_word_to_player(user_id: int, is_initial: bool = False):
    state = player_states.get(user_id)
    if not state: return
    word = state["current_word"]
    markup = _create_word_buttons()
    caption = f"🔤 Слово: *{word.upper()}*\n⏱️ Залишилось: {ROUND_TIME} сек"
    photo_path = f"images/{word.lower()}.png"
    try:
        with open(photo_path, "rb") as img:
            if is_initial:
                msg = bot.send_photo(user_id, img, caption=caption, parse_mode="Markdown", reply_markup=markup)
                if msg: state["player_message_id"] = msg.message_id; state["message_type"] = "photo"
            else:
                img.seek(0)
                media = types.InputMediaPhoto(img, caption=caption, parse_mode="Markdown") # type: ignore
                if state.get("player_message_id"): bot.edit_message_media(media=media, chat_id=user_id, message_id=state["player_message_id"], reply_markup=markup); state["message_type"] = "photo"
    except (FileNotFoundError, ApiTelegramException):
        try:
            if is_initial:
                msg = bot.send_message(user_id, caption, parse_mode="Markdown", reply_markup=markup)
                if msg: state["player_message_id"] = msg.message_id; state["message_type"] = "text"
            else:
                if state.get("player_message_id"): bot.edit_message_text(text=caption, chat_id=user_id, message_id=state["player_message_id"], reply_markup=markup); state["message_type"] = "text"
        except ApiTelegramException as e:
            bot.send_message(user_id, f"Помилка! {e}. Раунд завершено достроково.")
            if user_id in player_states: end_round(user_id, player_states[user_id]['score'])
def end_round(user_id: int, score: int):
    global round_in_progress, active_player_id, current_turn_index, group_timer_message_id
    if not round_in_progress: return
    if user_id in player_states: player_states[user_id]["timer_active"] = False
    round_in_progress, active_player_id = False, None
    team = player_states.get(user_id, {}).get("team")
    try: user_info = bot.get_chat(user_id); username = user_info.username or user_info.first_name
    except ApiTelegramException: username = f"Гравець {user_id}"
    result_message = f"✅ Раунд завершено! @{username} набрав *{score}* балів"
    if team:
        if team not in teams_score: teams_score[team] = 0
        teams_score[team] += score
        display_team_name = _get_team_display_name(team)
        result_message += f" для команди *{display_team_name}*"
    if current_chat_id != 0:
        if group_timer_message_id:
            try: bot.edit_message_text(text="⌛️ Час вийшов!", chat_id=current_chat_id, message_id=group_timer_message_id); group_timer_message_id = None
            except ApiTelegramException: pass
        bot.send_message(current_chat_id, result_message, parse_mode="Markdown")
        try: bot.send_message(user_id, result_message, parse_mode="Markdown")
        except ApiTelegramException: pass
        show_score()
        is_circle_complete = (current_turn_index + 1) >= len(teams_order)
        if is_circle_complete:
            current_turn_index = 0
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔄 Нове коло", callback_data="new_circle"), types.InlineKeyboardButton("🏁 Завершити гру", callback_data="finish_game"))
            bot.send_message(current_chat_id, "Круг завершено! Що робимо далі?", reply_markup=markup)
        else:
            current_turn_index += 1
            next_team = teams_order[current_turn_index]
            display_next_team = _get_team_display_name(next_team)
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("▶️ Почати раунд", callback_data="start_game"))
            bot.send_message(current_chat_id, f"Хід переходить до команди *{display_next_team}*! Гравець з цієї команди має натиснути кнопку:", reply_markup=markup)
def start_round_for_player(user_id: int):
    team = user_teams.get(user_id, "Без команди")
    if not available_words:
        if current_chat_id != 0: bot.send_message(current_chat_id, "⚠️ Слова закінчились! Завершення гри.")
        finish_game(); return
    player_states[user_id] = {"score": 0, "team": team, "word_count": 1, "player_message_id": None, "current_word": available_words.pop(), "timer_active": True, "message_type": "text"}
    if team != "Без команди": played_teams.add(team)
    send_word_to_player(user_id, is_initial=True)
    threading.Thread(target=update_timer_thread, args=(user_id, time.time())).start()

# --- Helper to handle global commands during next_step_handler ---
def handle_global_commands_in_step(message: types.Message) -> bool:
    """Checks if a message received during a next_step_handler is a global command and processes it."""
    if message.text and message.text.startswith('/'):
        # Strip the bot username if it's present (e.g., /finish@your_bot)
        command_text = message.text.split('@')[0]

        if command_text == '/finish':
            bot.send_message(message.chat.id, "🛑 Завершую гру за вашою командою!")
            finish_game(message.chat.id)
            return True # Indicates command was handled
        elif command_text == '/start':
            start(message)
            return True
        elif command_text == '/setup':
            setup_command(message)
            return True
        # Add other global commands here if needed
    return False # Indicates message was not a global command


# (All handlers are unchanged)
@bot.message_handler(commands=['setup'])
def setup_command(message: types.Message):
    finish_game(message.chat.id, silent=True)
    global current_chat_id; current_chat_id = message.chat.id
    msg = bot.send_message(message.chat.id, "Скільки команд буде грати? (введіть число)")
    bot.register_next_step_handler(msg, process_team_count)

# --- NEW /finish command handler ---
@bot.message_handler(commands=['finish'])
def finish_command(message: types.Message):
    """Handles the /finish command to stop the current game."""
    bot.send_message(message.chat.id, "🛑 Завершую гру за вашою командою!")
    finish_game(message.chat.id)

def process_team_count(message: types.Message):
    if handle_global_commands_in_step(message):
        # If a global command was handled, clear the current step handler
        bot.clear_step_handler_by_chat_id(chat_id=message.chat.id)
        return

    try:
        if not message.text: raise ValueError("Input is not text")
        count = int(message.text)
        if not 2 <= count <= 10: raise ValueError("Invalid number of teams")
        msg = bot.send_message(message.chat.id, f"Введіть назву для Команди 1:")
        bot.register_next_step_handler(msg, process_team_name, 1, count, [])
    except (ValueError, TypeError):
        msg = bot.send_message(message.chat.id, "Будь ласка, введіть число від 2 до 10.")
        bot.register_next_step_handler(msg, process_team_count) # Re-register for valid input

def process_team_name(message: types.Message, current_num: int, total_teams: int, collected_names: List[str]):
    if handle_global_commands_in_step(message):
        # If a global command was handled, clear the current step handler
        bot.clear_step_handler_by_chat_id(chat_id=message.chat.id)
        return

    if not message.text:
        msg = bot.send_message(message.chat.id, "Будь ласка, надішліть текстову назву для команди.")
        bot.register_next_step_handler(msg, process_team_name, current_num, total_teams, collected_names); return
    team_name = message.text.strip()
    if not team_name:
        msg = bot.send_message(message.chat.id, "Назва команди не може бути порожньою. Спробуйте ще раз:")
        bot.register_next_step_handler(msg, process_team_name, current_num, total_teams, collected_names); return
    collected_names.append(team_name)
    if current_num < total_teams:
        msg = bot.send_message(message.chat.id, f"Дякую! Тепер введіть назву для Команди {current_num + 1}:")
        bot.register_next_step_handler(msg, process_team_name, current_num + 1, total_teams, collected_names)
    else:
        global teams, teams_score, teams_order, team_emojis
        for i, name in enumerate(collected_names):
            teams[name] = []; teams_score[name] = 0; teams_order.append(name)
            team_emojis[name] = TEAM_EMOJIS[i % len(TEAM_EMOJIS)]
        bot.send_message(message.chat.id, "Чудово! Команди налаштовано. Можна починати гру, надіславши команду /start.")
@bot.message_handler(commands=["start"])
def start(message: types.Message):
    global current_chat_id; current_chat_id = message.chat.id
    if not teams:
        bot.send_message(message.chat.id, "Доброго дня! 👋\n\nЩоб почати грати, адміністратор чату має спершу налаштувати команди за допомогою команди /setup"); return
    rules = ("👋 *Вітаємо в Alias! Гра налаштована, можна починати.*\n\n" "📌 *Правила гри:*\n" "1. Усі гравці мають приєднатись до своїх команд, натиснувши на кнопку нижче.\n" "2. Бот автоматично визначить, яка команда ходить першою.\n" "3. Коли настане черга вашої команди, один гравець має натиснути 'Почати гру' або 'Почати раунд'.\n" "4. **Тільки гравець з команди, чия черга, може почати раунд.**\n" f"5. У вас є {ROUND_TIME} секунд або {ROUND_LIMIT} слів, щоб пояснити якомога більше.\n" "6. Вгадане слово — це +1 бал для вашої команди.\n\n" "🏆 *Приз для переможців: кожен гравець команди-переможця отримує +30 хв до перерви!*")
    bot.send_message(current_chat_id, rules, parse_mode="Markdown")
    markup = types.InlineKeyboardMarkup()
    for name in teams_order:
        display_name = _get_team_display_name(name)
        markup.add(types.InlineKeyboardButton(display_name, callback_data=f"team_{name}"))
    bot.send_message(current_chat_id, "✏️ **Оберіть свою команду:**", reply_markup=markup)
@bot.callback_query_handler(func=lambda call: call.data.startswith("team_"))
def join_team(call: types.CallbackQuery):
    if not call.data or not call.message: return
    team_name = call.data.replace("team_", "")
    user = call.from_user; uid = user.id; username = user.username or user.first_name
    for t_name, members in teams.items():
        if uid in members: members.remove(uid)
    teams[team_name].append(uid)
    user_teams[uid] = team_name
    full_team_list = ""
    for name in teams_order:
        members = teams.get(name, [])
        member_names = [f"@{bot.get_chat(m_id).username or bot.get_chat(m_id).first_name}" for m_id in members]
        display_name = _get_team_display_name(name)
        full_team_list += f"\n*{display_name}:*\n"
        full_team_list += "\n".join(member_names) if member_names else "-\n"
    try:
        display_team_name = _get_team_display_name(team_name)
        bot.edit_message_text(f"✅ @{username} приєднався до команди *{display_team_name}*!\n\n*Склад команд:*{full_team_list}", call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=call.message.reply_markup)
    except ApiTelegramException: pass
    if not game_active:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("▶️ Почати гру", callback_data="start_game"))
        bot.send_message(call.message.chat.id, "Коли всі приєднаються, перший гравець може починати гру!", reply_markup=markup)
def start_round_handler(message_or_call: types.Message | types.CallbackQuery):
    global game_active, round_in_progress, active_player_id, available_words, current_turn_index, group_timer_message_id
    user = message_or_call.from_user
    if not user: return
    chat_id = message_or_call.message.chat.id if isinstance(message_or_call, types.CallbackQuery) else message_or_call.chat.id
    if not chat_id: return
    if isinstance(message_or_call, types.CallbackQuery): bot.answer_callback_query(message_or_call.id)
    if round_in_progress:
        if isinstance(message_or_call, types.CallbackQuery): bot.answer_callback_query(message_or_call.id, "⏳ Зачекайте, раунд ще не завершено.", show_alert=True)
        return
    if not teams: return bot.send_message(chat_id, "Спочатку налаштуйте команди за допомогою /setup")
    expected_team = teams_order[current_turn_index]
    player_team = user_teams.get(user.id)
    if not player_team:
        if isinstance(message_or_call, types.CallbackQuery): bot.answer_callback_query(message_or_call.id, "Будь ласка, спершу приєднайтесь до команди.", show_alert=True)
        return
    if player_team != expected_team and game_active:
        if isinstance(message_or_call, types.CallbackQuery):
            display_expected_team = _get_team_display_name(expected_team)
            bot.answer_callback_query(message_or_call.id, f"Зараз черга команди '{display_expected_team}', а не вашої.", show_alert=True)
        return
    if not game_active:
        available_words = all_words.copy(); random.shuffle(available_words)
        random.shuffle(teams_order)
        current_turn_index = 0
        expected_team = teams_order[current_turn_index]
        game_active = True
        bot.send_message(current_chat_id, f"🚀 Гра почалась! Першою ходить команда *{_get_team_display_name(expected_team)}*.")
    timer_msg = bot.send_message(current_chat_id, f"⏳ Залишилось часу: *{ROUND_TIME}* сек", parse_mode="Markdown")
    if timer_msg: group_timer_message_id = timer_msg.message_id
    active_player_id = user.id
    try:
        username = user.username or user.first_name
        if player_team:
            display_player_team = _get_team_display_name(player_team)
            bot.send_message(current_chat_id, f"Хід гравця @{username} з команди *{display_player_team}*! Повідомлення зі словом відправлено в особисті.", parse_mode="Markdown")
        else: bot.send_message(current_chat_id, f"Хід гравця @{username}! Повідомлення зі словом відправлено в особисті.", parse_mode="Markdown")
    except Exception: pass
    round_in_progress = True
    start_round_for_player(active_player_id)
@bot.callback_query_handler(func=lambda call: call.data == "start_game")
def handle_start_round_callback(call: types.CallbackQuery): start_round_handler(call)
@bot.callback_query_handler(func=lambda call: call.data in ["right", "wrong", "skip"])
def handle_response(call: types.CallbackQuery):
    uid = call.from_user.id
    if not round_in_progress or uid != active_player_id: return bot.answer_callback_query(call.id, "⏳ Зачекай свою чергу")
    state = player_states.get(uid)
    if not state: return bot.answer_callback_query(call.id, "Помилка: не знайдено стан гри.")
    if call.data == "right": state["score"] += 1; bot.answer_callback_query(call.id, "✅ +1 бал")
    else: bot.answer_callback_query(call.id, "⏭️ Наступне слово")
    if state["word_count"] >= ROUND_LIMIT or not available_words: end_round(uid, state["score"]); return
    state["current_word"] = available_words.pop()
    state["word_count"] += 1
    send_word_to_player(uid)
@bot.callback_query_handler(func=lambda call: call.data == "new_circle")
def handle_new_circle(call: types.CallbackQuery):
    global current_turn_index
    if not call.message: return
    bot.answer_callback_query(call.id)
    current_turn_index = 0
    if not teams_order: bot.send_message(call.message.chat.id, "Помилка: не знайдено команд. Почніть з /setup."); return
    next_team = teams_order[current_turn_index]
    display_next_team = _get_team_display_name(next_team)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("▶️ Почати раунд", callback_data="start_game"))
    try: bot.edit_message_text(f"🔄 *Починаємо нове коло!*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
    except ApiTelegramException as e: print(f"Could not edit 'new_circle' message: {e}")
    bot.send_message(call.message.chat.id, f"Хід знову переходить до команди *{display_next_team}*! Гравець з цієї команди має натиснути кнопку:", reply_markup=markup)
@bot.callback_query_handler(func=lambda call: call.data == "finish_game")
def callback_finish_game(call: types.CallbackQuery):
    if call.message: finish_game(call.message.chat.id)
    bot.answer_callback_query(call.id)
@bot.callback_query_handler(func=lambda call: call.data == "setup_new_game")
def handle_setup_new_game(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    if isinstance(call.message, types.Message):
        try: bot.edit_message_text("Починаємо налаштування нової гри...", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except ApiTelegramException: pass
        setup_command(call.message)
    else: bot.send_message(call.from_user.id, "Помилка: не вдалося запустити налаштування з цього повідомлення. Будь ласка, використайте команду /setup.")

# ===================================================================
# === 3. Webhook Server & Startup Logic ===
# ===================================================================

@app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    """Processes updates from Telegram."""
    if flask.request.headers.get('content-type') == 'application/json':
        json_string = flask.request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        if update:
            bot.process_new_updates([update])
        return '', 200
    else:
        flask.abort(403)

# This block runs once when Gunicorn starts the app on Replit
if 'REPL_ID' in os.environ:
    print("Replit environment detected...")
    if WEBHOOK_URL:
        print(f"✅ Public URL found in Secrets: {WEBHOOK_URL}")
        print("⚙️  Setting webhook...")

        bot.remove_webhook()
        time.sleep(0.5)
        bot.set_webhook(url=WEBHOOK_URL + WEBHOOK_PATH)

        print("🚀 Webhook is set successfully! Bot is live.")
    else:
        print("⚠️ Could not find WEBHOOK_URL in Secrets. Webhook was not set.")
        print("   Please go to the 'Secrets' tab and set the WEBHOOK_URL variable.")

# This part is only for running the Flask server locally.
if __name__ == "__main__":
    print("Running in local mode. Bot will use polling.")
    bot.remove_webhook()
    bot.polling(none_stop=True)
