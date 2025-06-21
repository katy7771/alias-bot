import telebot
import os
import random
import threading
import time
import flask
from telebot import types
from telebot.apihelper import ApiTelegramException
from typing import Dict, List, Set, Optional, Any

# --- Bot and Global Variables Initialization ---
print("INFO: Starting bot initialization...")
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    print("CRITICAL: BOT_TOKEN is not set in Secrets. Exiting.")
    raise ValueError("BOT_TOKEN is not set in Secrets")

# In webhook mode, WEBHOOK_URL is where Telegram sends updates.
# In polling mode, WEBHOOK_URL is not strictly needed for operation but can be useful for initial webhook setup removal.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEBHOOK_PATH = f"/{TOKEN}" # This path is what Telegram will send updates to.

bot = telebot.TeleBot(TOKEN)
app = flask.Flask(__name__)
print("INFO: Flask app and TeleBot instance created.")

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
    print(f"INFO: Loaded {len(all_words)} words from words.txt.")
except FileNotFoundError:
    all_words = ["чат", "дзвінок", "підтримка", "запит", "email"]
    print("WARNING: words.txt not found. Using default words.")

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
    print(f"INFO: Finishing game in chat {chat_id}, silent={silent}.")
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
        try:
            bot.send_message(target_chat_id, summary, parse_mode="Markdown")
        except ApiTelegramException as e:
            print(f"ERROR: Could not send game summary to {target_chat_id}: {e}")
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🎉 Розпочати нову гру", callback_data="setup_new_game"))
        try:
            bot.send_message(target_chat_id, "Дякуємо за гру! Бажаєте зіграти ще раз?", reply_markup=markup)
        except ApiTelegramException as e:
            print(f"ERROR: Could not send new game prompt to {target_chat_id}: {e}")
    print("INFO: Game state reset.")
    teams, user_teams, teams_score, teams_order, team_emojis = {}, {}, {}, [], {}
    played_teams, used_words, available_words, player_states = set(), [], [], {}
    game_active, round_in_progress, active_player_id = False, False, None
    current_turn_index = 0
    group_timer_message_id = None
def show_score():
    print("INFO: Displaying score.")
    summary = "📊 *Поточний рахунок:*\n"
    for team_name in teams_order:
        score = teams_score.get(team_name, 0)
        summary += f"{_get_team_display_name(team_name)}: *{score}* балів\n"
    if current_chat_id != 0:
        try:
            bot.send_message(current_chat_id, summary, parse_mode="Markdown")
        except ApiTelegramException as e:
            print(f"ERROR: Could not send score to {current_chat_id}: {e}")
def update_timer_thread(user_id: int, start_time: float):
    print(f"INFO: Timer thread started for user {user_id}.")
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
    print(f"INFO: Timer thread finished for user {user_id}.")

def send_word_to_player(user_id: int, is_initial: bool = False):
    print(f"INFO: Sending word to player {user_id}, initial={is_initial}.")
    state = player_states.get(user_id)
    if not state:
        print(f"WARNING: No player state for user {user_id}.")
        return
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
        print(f"INFO: Sent photo word '{word}' to {user_id}.")
    except (FileNotFoundError, ApiTelegramException) as e:
        print(f"WARNING: Photo for '{word}' not found or API error: {e}. Sending as text.")
        try:
            if is_initial:
                msg = bot.send_message(user_id, caption, parse_mode="Markdown", reply_markup=markup)
                if msg: state["player_message_id"] = msg.message_id; state["message_type"] = "text"
            else:
                if state.get("player_message_id"): bot.edit_message_text(text=caption, chat_id=user_id, message_id=state["player_message_id"], reply_markup=markup); state["message_type"] = "text"
            print(f"INFO: Sent text word '{word}' to {user_id}.")
        except ApiTelegramException as e:
            print(f"ERROR: Failed to send word to user {user_id}: {e}")
            bot.send_message(user_id, f"Помилка! {e}. Раунд завершено достроково.")
            if user_id in player_states: end_round(user_id, player_states[user_id]['score'])
def end_round(user_id: int, score: int):
    print(f"INFO: Ending round for user {user_id} with score {score}.")
    global round_in_progress, active_player_id, current_turn_index, group_timer_message_id
    if not round_in_progress:
        print("WARNING: end_round called but no round in progress.")
        return
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
        try:
            bot.send_message(current_chat_id, result_message, parse_mode="Markdown")
        except ApiTelegramException as e:
            print(f"ERROR: Could not send round result to {current_chat_id}: {e}")
        try: bot.send_message(user_id, result_message, parse_mode="Markdown")
        except ApiTelegramException: pass
        show_score()
        is_circle_complete = (current_turn_index + 1) >= len(teams_order)
        if is_circle_complete:
            current_turn_index = 0
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔄 Нове коло", callback_data="new_circle"), types.InlineKeyboardButton("🏁 Завершити гру", callback_data="finish_game"))
            try:
                bot.send_message(current_chat_id, "Круг завершено! Що робимо далі?", reply_markup=markup)
            except ApiTelegramException as e:
                print(f"ERROR: Could not send circle end message to {current_chat_id}: {e}")
        else:
            current_turn_index += 1
            next_team = teams_order[current_turn_index]
            display_next_team = _get_team_display_name(next_team)
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("▶️ Почати раунд", callback_data="start_game"))
            try:
                bot.send_message(current_chat_id, f"Хід переходить до команди *{display_next_team}*! Гравець з цієї команди має натиснути кнопку:", reply_markup=markup)
            except ApiTelegramException as e:
                print(f"ERROR: Could not send next team prompt to {current_chat_id}: {e}")
def start_round_for_player(user_id: int):
    print(f"INFO: Starting round for player {user_id}.")
    team = user_teams.get(user_id, "Без команди")
    if not available_words:
        if current_chat_id != 0:
            try:
                bot.send_message(current_chat_id, "⚠️ Слова закінчились! Завершення гри.")
            except ApiTelegramException as e:
                print(f"ERROR: Could not send 'words ended' message to {current_chat_id}: {e}")
        finish_game(); return
    player_states[user_id] = {"score": 0, "team": team, "word_count": 1, "player_message_id": None, "current_word": available_words.pop(), "timer_active": True, "message_type": "text"}
    if team != "Без команди": played_teams.add(team)
    send_word_to_player(user_id, is_initial=True)
    threading.Thread(target=update_timer_thread, args=(user_id, time.time())).start()

# (All handlers are unchanged)
@bot.message_handler(commands=['setup'])
def setup_command(message: types.Message):
    print(f"INFO: Received /setup command from user {message.from_user.id} in chat {message.chat.id}")
    finish_game(message.chat.id, silent=True)
    global current_chat_id; current_chat_id = message.chat.id
    try:
        msg = bot.send_message(message.chat.id, "Скільки команд буде грати? (введіть число)")
        bot.register_next_step_handler(msg, process_team_count)
    except ApiTelegramException as e:
        print(f"ERROR: Could not send setup prompt to {message.chat.id}: {e}")
def process_team_count(message: types.Message):
    print(f"INFO: Processing team count from user {message.from_user.id}.")
    try:
        if not message.text: raise ValueError("Input is not text")
        count = int(message.text)
        if not 2 <= count <= 10: raise ValueError("Invalid number of teams")
        try:
            msg = bot.send_message(message.chat.id, f"Введіть назву для Команди 1:")
            bot.register_next_step_handler(msg, process_team_name, 1, count, [])
        except ApiTelegramException as e:
            print(f"ERROR: Could not send team name prompt to {message.chat.id}: {e}")
    except (ValueError, TypeError) as e:
        print(f"ERROR: Invalid team count input from {message.from_user.id}: {e}")
        try:
            msg = bot.send_message(message.chat.id, "Будь ласка, введіть число від 2 до 10."); bot.register_next_step_handler(msg, process_team_count)
        except ApiTelegramException as e:
            print(f"ERROR: Could not send retry message for team count to {message.chat.id}: {e}")
def process_team_name(message: types.Message, current_num: int, total_teams: int, collected_names: List[str]):
    print(f"INFO: Processing team name {current_num}/{total_teams} from user {message.from_user.id}.")
    if not message.text:
        try:
            msg = bot.send_message(message.chat.id, "Будь ласка, надішліть текстову назву для команди.")
            bot.register_next_step_handler(msg, process_team_name, current_num, total_teams, collected_names); return
        except ApiTelegramException as e:
            print(f"ERROR: Could not send 'text name' prompt to {message.chat.id}: {e}")
    team_name = message.text.strip()
    if not team_name:
        try:
            msg = bot.send_message(message.chat.id, "Назва команди не може бути порожньою. Спробуйте ще раз:")
            bot.register_next_step_handler(msg, process_team_name, current_num, total_teams, collected_names); return
        except ApiTelegramException as e:
            print(f"ERROR: Could not send 'empty name' prompt to {message.chat.id}: {e}")
    collected_names.append(team_name)
    if current_num < total_teams:
        try:
            msg = bot.send_message(message.chat.id, f"Дякую! Тепер введіть назву для Команди {current_num + 1}:")
            bot.register_next_step_handler(msg, process_team_name, current_num + 1, total_teams, collected_names)
        except ApiTelegramException as e:
            print(f"ERROR: Could not send next team name prompt to {message.chat.id}: {e}")
    else:
        global teams, teams_score, teams_order, team_emojis
        for i, name in enumerate(collected_names):
            teams[name] = []; teams_score[name] = 0; teams_order.append(name)
            team_emojis[name] = TEAM_EMOJIS[i % len(TEAM_EMOJIS)]
        try:
            bot.send_message(message.chat.id, "Чудово! Команди налаштовано. Можна починати гру, надіславши команду /start.")
        except ApiTelegramException as e:
            print(f"ERROR: Could not send teams configured message to {message.chat.id}: {e}")

@bot.message_handler(commands=["start"])
def start(message: types.Message):
    print(f"INFO: Received /start command from user {message.from_user.id} in chat {message.chat.id}")
    global current_chat_id; current_chat_id = message.chat.id
    if not teams:
        try:
            bot.send_message(message.chat.id, "Доброго дня! 👋\n\nЩоб почати грати, адміністратор чату має спершу налаштувати команди за допомогою команди /setup"); return
        except ApiTelegramException as e:
            print(f"ERROR: Could not send 'no teams' message to {message.chat.id}: {e}")
    rules = ("👋 *Вітаємо в Alias! Гра налаштована, можна починати.*\n\n" "📌 *Правила гри:*\n" "1. Усі гравці мають приєднатись до своїх команд, натиснувши на кнопку нижче.\n" "2. Бот автоматично визначить, яка команда ходить першою.\n" "3. Коли настане черга вашої команди, один гравець має натиснути 'Почати гру' або 'Почати раунд'.\n" "4. **Тільки гравець з команди, чия черга, може почати раунд.**\n" f"5. У вас є {ROUND_TIME} секунд або {ROUND_LIMIT} слів, щоб пояснити якомога більше.\n" "6. Вгадане слово — это +1 бал для вашей команды.\n\n" "🏆 *Приз для переможців: кожен гравець команди-переможця отримає +30 хв до перерви!*")
    try:
        bot.send_message(current_chat_id, rules, parse_mode="Markdown")
    except ApiTelegramException as e:
        print(f"ERROR: Could not send rules message to {current_chat_id}: {e}")
    markup = types.InlineKeyboardMarkup()
    for name in teams_order:
        display_name = _get_team_display_name(name)
        markup.add(types.InlineKeyboardButton(display_name, callback_data=f"team_{name}"))
    try:
        bot.send_message(current_chat_id, "✏️ **Оберіть свою команду:**", reply_markup=markup)
    except ApiTelegramException as e:
        print(f"ERROR: Could not send team selection message to {current_chat_id}: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("team_"))
def join_team(call: types.CallbackQuery):
    print(f"INFO: Received join_team callback from user {call.from_user.id} for team {call.data}.")
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
        # Added try-except around bot.get_chat for member_names to prevent crashes
        member_names = []
        for m_id in members:
            try:
                chat_info = bot.get_chat(m_id)
                member_names.append(f"@{chat_info.username or chat_info.first_name}")
            except ApiTelegramException as e:
                print(f"WARNING: Could not get chat info for user {m_id}: {e}")
                member_names.append(f"- User {m_id} (error)")

        display_name = _get_team_display_name(name)
        full_team_list += f"\n*{display_name}:*\n"
        full_team_list += "\n".join(member_names) if member_names else "-\n"
    try:
        display_team_name = _get_team_display_name(team_name)
        bot.edit_message_text(f"✅ @{username} приєднався до команди *{display_team_name}*!\n\n*Склад команд:*{full_team_list}", call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=call.message.reply_markup)
    except ApiTelegramException as e:
        print(f"ERROR: Could not edit message after join_team for user {uid}: {e}")
        pass
    if not game_active:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("▶️ Почати гру", callback_data="start_game"))
        try:
            bot.send_message(call.message.chat.id, "Коли всі приєднаються, перший гравець може починати гру!", reply_markup=markup)
        except ApiTelegramException as e:
            print(f"ERROR: Could not send 'start game' prompt to {call.message.chat.id}: {e}")

def start_round_handler(message_or_call: types.Message | types.CallbackQuery):
    print(f"INFO: Received start_round_handler from user {message_or_call.from_user.id}.")
    global game_active, round_in_progress, active_player_id, available_words, current_turn_index, group_timer_message_id
    user = message_or_call.from_user
    if not user:
        print("WARNING: start_round_handler called without user info.")
        return
    chat_id = message_or_call.message.chat.id if isinstance(message_or_call, types.CallbackQuery) else message_or_call.chat.id
    if not chat_id:
        print("WARNING: start_round_handler called without chat ID.")
        return
    if isinstance(message_or_call, types.CallbackQuery): bot.answer_callback_query(message_or_call.id)
    if round_in_progress:
        if isinstance(message_or_call, types.CallbackQuery): bot.answer_callback_query(message_or_call.id, "⏳ Зачекайте, раунд еще не завершен.", show_alert=True)
        print("WARNING: Round already in progress.")
        return
    if not teams:
        try:
            return bot.send_message(chat_id, "Спочатку налаштуйте команди за допомогою /setup")
        except ApiTelegramException as e:
            print(f"ERROR: Could not send 'no teams' message (start_round_handler) to {chat_id}: {e}")
            return
    expected_team = teams_order[current_turn_index]
    player_team = user_teams.get(user.id)
    if not player_team:
        if isinstance(message_or_call, types.CallbackQuery): bot.answer_callback_query(message_or_call.id, "Будь ласка, спершу приєднайтесь до команди.", show_alert=True)
        print(f"WARNING: User {user.id} not in a team.")
        return
    if player_team != expected_team and game_active:
        if isinstance(message_or_call, types.CallbackQuery):
            display_expected_team = _get_team_display_name(expected_team)
            bot.answer_callback_query(message_or_call.id, f"За сейчас черга команди '{display_expected_team}', а не вашої.", show_alert=True)
        print(f"WARNING: User {user.id} from team {player_team} tried to start, but it's {expected_team}'s turn.")
        return
    if not game_active:
        available_words = all_words.copy(); random.shuffle(available_words)
        random.shuffle(teams_order)
        current_turn_index = 0
        expected_team = teams_order[current_turn_index]
        game_active = True
        try:
            bot.send_message(current_chat_id, f"🚀 Гра почалась! Першою ходить команда *{_get_team_display_name(expected_team)}*.")
        except ApiTelegramException as e:
            print(f"ERROR: Could not send 'game started' message to {current_chat_id}: {e}")
    timer_msg = None
    try:
        timer_msg = bot.send_message(current_chat_id, f"⏳ Залишилось часу: *{ROUND_TIME}* сек", parse_mode="Markdown")
    except ApiTelegramException as e:
        print(f"ERROR: Could not send timer message to {current_chat_id}: {e}")
    if timer_msg: group_timer_message_id = timer_msg.message_id
    active_player_id = user.id
    try:
        username = user.username or user.first_name
        if player_team:
            display_player_team = _get_team_display_name(player_team)
            bot.send_message(current_chat_id, f"Хід гравця @{username} з команди *{display_team_name}*! Повідомлення зі словом відправлено в особисті.", parse_mode="Markdown")
        else: bot.send_message(current_chat_id, f"Хід гравця @{username}! Повідомлення зі словом відправлено в особисті.", parse_mode="Markdown")
        print(f"INFO: Notified chat about active player @{username}.")
    except Exception as e:
        print(f"ERROR: Failed to notify chat about active player: {e}")
        pass
    round_in_progress = True
    start_round_for_player(active_player_id)

@bot.callback_query_handler(func=lambda call: call.data == "start_game")
def handle_start_round_callback(call: types.CallbackQuery):
    print(f"INFO: Callback 'start_game' from user {call.from_user.id}.")
    start_round_handler(call)

@bot.callback_query_handler(func=lambda call: call.data in ["right", "wrong", "skip"])
def handle_response(call: types.CallbackQuery):
    print(f"INFO: Received word response callback '{call.data}' from user {call.from_user.id}.")
    uid = call.from_user.id
    if not round_in_progress or uid != active_player_id:
        bot.answer_callback_query(call.id, "⏳ Зачекай свою чергу")
        print(f"WARNING: User {uid} tried to respond out of turn or round not in progress.")
        return
    state = player_states.get(uid)
    if not state:
        bot.answer_callback_query(call.id, "Помилка: не знайдено стан гри.")
        print(f"ERROR: No game state found for user {uid} during response handling.")
        return
    if call.data == "right":
        state["score"] += 1;
        bot.answer_callback_query(call.id, "✅ +1 бал")
        print(f"INFO: User {uid} got word right. Score: {state['score']}.")
    else:
        bot.answer_callback_query(call.id, "⏭️ Следующее слово")
        print(f"INFO: User {uid} skipped or got word wrong.")

    if state["word_count"] >= ROUND_LIMIT or not available_words:
        print(f"INFO: Round limit reached or no available words. Ending round for user {uid}.")
        end_round(uid, state["score"]); return
    state["current_word"] = available_words.pop()
    state["word_count"] += 1
    send_word_to_player(uid)

@bot.callback_query_handler(func=lambda call: call.data == "new_circle")
def handle_new_circle(call: types.CallbackQuery):
    print(f"INFO: Received new_circle callback from user {call.from_user.id}.")
    global current_turn_index
    if not call.message: return
    bot.answer_callback_query(call.id)
    current_turn_index = 0
    if not teams_order:
        try:
            bot.send_message(call.message.chat.id, "Помилка: не знайдено команд. Почніть з /setup."); return
        except ApiTelegramException as e:
            print(f"ERROR: Could not send 'no teams' message (new_circle) to {call.message.chat.id}: {e}")
            return
    next_team = teams_order[current_turn_index]
    display_next_team = _get_team_display_name(next_team)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔄 Нове коло", callback_data="new_circle"), types.InlineKeyboardButton("🏁 Завершити гру", callback_data="finish_game"))
    try:
        bot.edit_message_text(f"🔄 *Починаємо нове коло!*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
    except ApiTelegramException as e:
        print(f"WARNING: Could not edit 'new_circle' message in chat {call.message.chat.id}: {e}")
    try:
        bot.send_message(call.message.chat.id, f"Хід знову переходить до команди *{display_next_team}*! Гравець з цієї команди має натиснути кнопку:", reply_markup=markup)
    except ApiTelegramException as e:
        print(f"ERROR: Could not send 'next circle' prompt to {call.message.chat.id}: {e}")

@bot.callback_query_handler(func=lambda call: call.data == "finish_game")
def callback_finish_game(call: types.CallbackQuery):
    print(f"INFO: Received finish_game callback from user {call.from_user.id}.")
    if call.message: finish_game(call.message.chat.id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "setup_new_game")
def handle_setup_new_game(call: types.CallbackQuery):
    print(f"INFO: Received setup_new_game callback from user {call.from_user.id}.")
    bot.answer_callback_query(call.id)
    if isinstance(call.message, types.Message):
        try: bot.edit_message_text("Починаємо налаштування нової гри...", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except ApiTelegramException: pass
        setup_command(call.message)
    else:
        try:
            bot.send_message(call.from_user.id, "Помилка: не вдалося запустити налаштування з цього повідомлення. Будь ласка, використайте команду /setup.")
        except ApiTelegramException as e:
            print(f"ERROR: Could not send 'setup failed' message to user {call.from_user.id}: {e}")

# This is a generic handler that will catch ALL messages and print them.
# It helps confirm if messages are being dispatched to *any* handler.
@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'video', 'audio', 'document', 'sticker', 'voice', 'location', 'contact'])
def echo_all(message: types.Message):
    print(f"INFO: Generic handler received message from {message.from_user.id} in chat {message.chat.id}. Text: {message.text or 'non-text message'}")
    # You can temporarily add a simple response here to confirm the handler works
    # bot.send_message(message.chat.id, f"Echo: {message.text or 'Received non-text message'}")


# ===================================================================
# === 3. Webhook Server & Startup Logic ===
# ===================================================================

@app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    """Processes updates from Telegram."""
    print("INFO: Webhook endpoint received a request.")
    if flask.request.headers.get('content-type') == 'application/json':
        json_string = flask.request.get_data().decode('utf-8')
        try:
            update = telebot.types.Update.de_json(json_string)
            print(f"INFO: Received update from Telegram: {update.update_id}")
            # Ensure the bot's dispatcher processes this update
            bot.process_new_updates([update])
            print(f"INFO: Successfully processed update {update.update_id} and passed to handlers.")
            return '', 200
        except Exception as e:
            print(f"CRITICAL ERROR: Failed to parse or process Telegram update: {e}")
            import traceback
            print(traceback.format_exc())
            return '', 500
    else:
        print(f"WARNING: Webhook received non-JSON request with content-type: {flask.request.headers.get('content-type')}")
        flask.abort(403)

# This block runs once when Gunicorn starts the app.
# It handles webhook setup for both Replit and other platforms like Render.
# The 'if __name__ != "__main__":' condition ensures it runs when Gunicorn starts the Flask app.
if __name__ != "__main__":
    print("INFO: Starting production mode initialization (Gunicorn).")
    if not TOKEN:
        print("CRITICAL: BOT_TOKEN is not set. Cannot set webhook.")
    elif WEBHOOK_URL:
        print(f"INFO: Public WEBHOOK_URL found: {WEBHOOK_URL}")
        print("INFO: Attempting to set webhook...")
        try:
            # IMPORTANT: Delete any existing webhook before setting a new one
            # This avoids the Conflict: can't use getUpdates method while webhook is active error.
            bot.delete_webhook() # This is crucial
            time.sleep(0.1) # Small delay

            bot.set_webhook(url=WEBHOOK_URL + WEBHOOK_PATH)
            print("SUCCESS: Webhook is set successfully! Bot is live and ready.")
        except Exception as e:
            print(f"CRITICAL ERROR: Failed to set webhook: {e}")
            print("Please ensure WEBHOOK_URL is correct and accessible from Telegram.")
            import traceback
            print(traceback.format_exc())
    else:
        print("WARNING: WEBHOOK_URL is not set. Webhook was not set.")
        print("    Please ensure WEBHOOK_URL environment variable is configured on Render.")

    # In a pure webhook setup, you do NOT run bot.polling() or bot.infinity_polling().
    # The Flask app receives the webhook updates, and bot.process_new_updates() handles dispatching.
    print("INFO: Bot operating in webhook mode, relying on Flask to receive updates.")

# This part is only for running the Flask server locally for development.
if __name__ == "__main__":
    print("INFO: Running in local development mode (Polling).")
    bot.remove_webhook()
    bot.polling(none_stop=True)
    
