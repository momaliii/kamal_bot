import os
import sqlite3
from datetime import datetime
import csv
import matplotlib.pyplot as plt
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.constants import ParseMode
import logging
import re

# Setup logging for better debugging and monitoring
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Connect to the SQLite database
conn = sqlite3.connect('transactions.db')
c = conn.cursor()

# Add is_admin column to the users table if it doesn't exist
try:
    c.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0")
except sqlite3.OperationalError:
    # Ignore if the column already exists
    pass

# Ensure that the users and transactions tables exist to store user chat IDs, usernames, and transaction details
c.execute('''CREATE TABLE IF NOT EXISTS users
             (chat_id INTEGER PRIMARY KEY, 
              username TEXT, 
              is_admin BOOLEAN DEFAULT 0, 
              chat_type TEXT)''')

c.execute('''CREATE TABLE IF NOT EXISTS transactions
             (id INTEGER PRIMARY KEY, 
              amount REAL, 
              date TEXT, 
              category TEXT, 
              chat_id INTEGER)''')

c.execute('''CREATE TABLE IF NOT EXISTS report_times
             (chat_id INTEGER PRIMARY KEY, 
              hour INTEGER, 
              minute INTEGER)''')

conn.commit()

# Add chat_type column to the users table if it doesn't exist
try:
    c.execute("ALTER TABLE users ADD COLUMN chat_type TEXT DEFAULT 'private'")
except sqlite3.OperationalError:
    # Ignore if the column already exists
    pass

# Load Telegram bot token from environment variables for security
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

if not BOT_TOKEN:
    raise ValueError("No Telegram bot token found. Please set the TELEGRAM_BOT_TOKEN environment variable.")

# List of owner usernames (replace with actual usernames)
OWNER_USERNAMES = ['mada167', 'Nogitsuneiii', 'KanimaUC']  # Add all your owner usernames here

# Function to check if the user is an owner (by username)
def is_owner(update):
    user = update.message.from_user
    return user.username in OWNER_USERNAMES  # Checks if the sender's username is in the list of owners

# Function to check if the user is an admin
def is_admin(update):
    chat_id = update.message.chat.id
    c.execute("SELECT is_admin FROM users WHERE chat_id = ?", (chat_id,))
    result = c.fetchone()
    return result and result[0] == 1

# Add user to the users table, storing username and chat type
def add_user(chat_id, username, is_admin=False, chat_type="private"):
    c.execute("INSERT OR IGNORE INTO users (chat_id, username, is_admin, chat_type) VALUES (?, ?, ?, ?)", (chat_id, username, is_admin, chat_type))
    conn.commit()

# Helper function to split large messages
def split_message(message, max_length=4096):
    return [message[i:i + max_length] for i in range(0, len(message), max_length)]

# Command to add a new admin by username (only owner)
async def add_admin(update: Update, context):
    if not is_owner(update):
        return

    if len(context.args) != 1:
        await update.message.reply_text("Please provide the username to add as an admin.")
        return

    username = context.args[0].lstrip('@')  # Remove "@" if the username has it
    
    # Check if the user exists in the database
    c.execute("SELECT chat_id FROM users WHERE username = ?", (username,))
    result = c.fetchone()

    if result:
        # If user exists, update their admin status
        c.execute("UPDATE users SET is_admin = 1 WHERE username = ?", (username,))
        conn.commit()
        await update.message.reply_text(f"User with username @{username} has been added as an admin.")
    else:
        # If user doesn't exist, insert them into the database with is_admin = 1
        # You'll need to specify their chat ID here, or handle user additions elsewhere
        await update.message.reply_text(f"User @{username} not found in the database.")


# Command to remove an admin by username (only owner)
async def remove_admin(update: Update, context):
    if not is_owner(update):
        return

    if len(context.args) != 1:
        await update.message.reply_text("Please provide the username to remove as an admin.")
        return

    username = context.args[0].lstrip('@')
    c.execute("UPDATE users SET is_admin = 0 WHERE username = ?", (username,))
    conn.commit()

    await update.message.reply_text(f"User with username @{username} has been removed from the admin list.")

# Command to list all admins (only owner)
async def list_admins(update: Update, context):
    if not is_owner(update):
        return

    c.execute("SELECT username FROM users WHERE is_admin = 1")
    admins = c.fetchall()

    if admins:
        admin_list = "\n".join([f"@{admin[0]}" for admin in admins])
        await update.message.reply_text(f"List of Admins:\n{admin_list}")
    else:
        await update.message.reply_text("No admins found.")



# Start command handler with user tracking
async def start(update: Update, context):
    chat_id = update.message.chat.id
    user = update.message.from_user
    username = user.username if user.username else f"User_{chat_id}"  # Handle cases where username is missing
    chat_type = update.message.chat.type

    # Add user to the database
    add_user(chat_id, username, chat_type=chat_type)

    # Greet the owner or regular users
    if is_owner(update):
        await update.message.reply_text("Welcome, Owner! You can use the bot now.")
    else:
        await update.message.reply_text("Welcome! You can interact with this bot.")

# Handle regular messages with arithmetic operations
async def handle_message(update: Update, context):
    # Ensure that the update contains a message
    if update.message is None:
        return  # Ignore updates without messages (e.g., inline queries)

    chat_id = update.message.chat.id if update.message.chat else None
    user = update.message.from_user
    text = update.message.text.strip()

    # If there's no chat in the update, return
    if chat_id is None:
        return  # Ignore messages with no valid chat

    # Determine if the chat is private or a group
    chat_type = update.message.chat.type

    # Check if the user is an owner or an admin
    if is_owner(update):
        # Owners have full access in both private and group chats
        pass
    elif chat_type == 'private':
        if not is_admin(update):
            return  # Ignore messages from non-admins in private chat
    elif chat_type in ['group', 'supergroup']:
        # In a group, check admin status by the user's chat ID
        c.execute("SELECT is_admin FROM users WHERE chat_id = ?", (user.id,))
        result = c.fetchone()
        if not result or result[0] != 1:  # If the user is not an admin
            return  # Ignore messages from non-admins in group chats

    # Check if the input starts with + or -
    if text.startswith('+') or text.startswith('-'):
        try:
            # Clean the text to allow only numbers and basic arithmetic operators
            text = re.sub(r'[^0-9\.\+\-\*/\(\) ]', '', text)

            # Evaluate the arithmetic expression (e.g., +20*5 or -10/2)
            amount = eval(text)

            # Ensure the result is a valid number
            if isinstance(amount, (int, float)):
                date = datetime.now().strftime("%Y-%m-%d")
                category = "general"  # Default category

                # Insert the number along with the chat_id into the database
                c.execute('INSERT INTO transactions (amount, date, category, chat_id) VALUES (?, ?, ?, ?)', (amount, date, category, chat_id))
                conn.commit()

                # Get the total amount for the specific chat
                c.execute('SELECT SUM(amount) FROM transactions WHERE chat_id = ?', (chat_id,))
                total = c.fetchone()[0]

                await update.message.reply_text(f"Amount added: {amount}\nTotal: {total}")
            else:
                await update.message.reply_text("Invalid input. Please enter a valid number or arithmetic expression.")
        except (ValueError, SyntaxError):
            await update.message.reply_text("Invalid input. Please enter a valid arithmetic expression.")
        except Exception as e:
            await update.message.reply_text(f"An error occurred: {str(e)}")


# Set custom report time (only owner)
async def set_report_time(update: Update, context):
    if not is_owner(update):
        return  # Ignore requests from non-owners

    user_id = update.message.chat.id
    if len(context.args) == 1:
        try:
            time = context.args[0]
            hour, minute = map(int, time.split(":"))
            user_report_times[user_id] = (hour, minute)

            # Store the report time in the database
            c.execute("REPLACE INTO report_times (chat_id, hour, minute) VALUES (?, ?, ?)", (user_id, hour, minute))
            conn.commit()

            await update.message.reply_text(f"Your report time is set to {time}.")
        except:
            await update.message.reply_text("Invalid time format. Please use HH:MM.")
    else:
        await update.message.reply_text("Please provide the time in HH:MM format.")

# Initialize a dictionary to store user report times
user_report_times = {}

# Load custom report times from the database at startup
c.execute("SELECT chat_id, hour, minute FROM report_times")
rows = c.fetchall()
for row in rows:
    user_report_times[row[0]] = (row[1], row[2])

# Send daily report with totals for all chats (only owner)
async def send_daily_report(context):
    user_ids = list(user_report_times.keys())

    for user_id in user_ids:
        chat_report = []
        chats = c.execute("SELECT DISTINCT chat_id FROM transactions WHERE chat_id != ?", (user_id,)).fetchall()

        for chat in chats:
            chat_id = chat[0]
            c.execute("SELECT SUM(amount) FROM transactions WHERE chat_id = ?", (chat_id,))
            total = c.fetchone()[0]
            if total is None:
                total = 0

            try:
                chat_obj = await context.bot.get_chat(chat_id)
                chat_name = chat_obj.title or chat_obj.username or str(chat_id)
            except Exception as e:
                chat_name = f"Chat ID: {chat_id} (error fetching details: {str(e)})"

            chat_report.append(f"{chat_name} (ID: {chat_id}) - Total: {total}")

        if chat_report:
            report_message = "Daily Report of Transactions Across Chats:\n\n" + "\n.join(chat_report)"
        else:
            report_message = "No transactions found for today."

        # Split and send large reports if necessary
        for chunk in split_message(report_message):
            await context.bot.send_message(chat_id=user_id, text=chunk)

# Export transactions as CSV (only owner or admin)
async def export_transactions(update: Update, context):
    if not is_owner(update) and not is_admin(update):
        return  # Ignore requests from non-owners or non-admins

    user_id = update.message.chat.id
    transactions = c.execute("SELECT * FROM transactions WHERE chat_id = ?", (user_id,)).fetchall()

    file_path = os.path.join(os.getcwd(), f'transactions_{user_id}.csv')
    # Write to a CSV file
    with open(file_path, 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["ID", "Amount", "Date", "Category", "Chat ID"])
        writer.writerows(transactions)
    
    # Send the file to the user
    try:
        await context.bot.send_document(chat_id=user_id, document=open(file_path, 'rb'))
    except Exception as e:
        logging.error(f"Failed to send document to {user_id}: {str(e)}")

# Generate graphical report (only owner or admin)
async def send_graph(update: Update, context):
    if not is_owner(update) and not is_admin(update):
        return  # Ignore requests from non-owners or non-admins

    user_id = update.message.chat.id
    transactions = c.execute("SELECT date, SUM(amount) FROM transactions WHERE chat_id = ? GROUP BY date", (user_id,)).fetchall()

    dates = [row[0] for row in transactions]
    totals = [row[1] for row in transactions]

    plt.plot(dates, totals)
    plt.title('Transaction History')
    plt.xlabel('Date')
    plt.ylabel('Total Amount')

    plt.savefig('transaction_graph.png')
    try:
        await context.bot.send_photo(chat_id=user_id, photo=open('transaction_graph.png', 'rb'))
    except Exception as e:
        logging.error(f"Failed to send graph to {user_id}: {str(e)}")

# Reset user transactions (only owner or admin)
async def reset_transactions(update: Update, context):
    if not is_owner(update) and not is_admin(update):
        return  # Ignore requests from non-owners or non-admins

    user_id = update.message.chat.id
    c.execute('DELETE FROM transactions WHERE chat_id = ?', (user_id,))
    conn.commit()
    await update.message.reply_text("All your transactions have been reset.")

# Delete summary of all transactions (only owner)
async def delete_summary(update: Update, context):
    if not is_owner(update):
        return  # Ignore requests from non-owners

    # Delete all transaction data for all chats
    c.execute('DELETE FROM transactions')
    conn.commit()
    await update.message.reply_text("Summary of all chats has been deleted.")

# Command to remove a specific user (only owner)
async def remove_user(update: Update, context):
    if not is_owner(update):
        await update.message.reply_text("You are not authorized to use this command.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Please provide a username or chat ID to remove.")
        return

    identifier = context.args[0]
    
    # Check if the identifier is a chat ID (numeric) or a username
    if identifier.isdigit():
        # Remove by chat ID
        chat_id = int(identifier)
        c.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
    else:
        # Remove by username
        username = identifier.lstrip('@')  # Remove @ if provided
        c.execute("DELETE FROM users WHERE username = ?", (username,))

    conn.commit()
    await update.message.reply_text(f"User {identifier} has been removed.")

# Command to remove all users (only owner)
async def remove_all_users(update: Update, context):
    if not is_owner(update):
        await update.message.reply_text("You are not authorized to use this command.")
        return

    # Confirmation check
    if len(context.args) == 1 and context.args[0].lower() == 'confirm':
        # Remove all users from the users table
        c.execute("DELETE FROM users")
        conn.commit()
        await update.message.reply_text("All users have been removed.")
    else:
        # Ask for confirmation
        await update.message.reply_text("This will remove all users. Type `/removeallusers confirm` to proceed.")



# Send message to all users (admin and owner)
async def sendmsg(update: Update, context):
    if not is_owner(update) and not is_admin(update):
        await update.message.reply_text("You are not authorized to use this command.")
        return
    
    if context.args:
        message = " ".join(context.args).replace('\\n', '\n')
        MAX_MESSAGE_LENGTH = 4096
        # Fetch all unique chat IDs from users and transactions
        try:
            c.execute("SELECT DISTINCT chat_id FROM users UNION SELECT DISTINCT chat_id FROM transactions")
            all_chat_ids = c.fetchall()
            message_chunks = [message[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(message), MAX_MESSAGE_LENGTH)]

            for chat_id_tuple in all_chat_ids:
                chat_id = chat_id_tuple[0]
                for chunk in message_chunks:
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.MARKDOWN)
                    except Exception as e:
                        logging.error(f"Failed to send message to {chat_id}: {str(e)}")
                        await update.message.reply_text(f"Failed to send message to chat ID {chat_id}.")
        except Exception as e:
            await update.message.reply_text("Failed to fetch chat IDs or send messages.")
            logging.error(f"Error in sending messages: {e}")
    else:
        await update.message.reply_text("Please provide a message to send.")

# Show the owner who is using the bot (only owner)
async def show_users(update: Update, context):
    if not is_owner(update):
        return  # Ignore requests from non-owners

    # Fetch all users from the database
    users = c.execute("SELECT chat_id, username FROM users").fetchall()

    user_list = []
    for user in users:
        chat_id, username = user
        try:
            chat = await context.bot.get_chat(chat_id)
            chat_name = chat.title or chat.username or str(chat_id)
        except Exception as e:
            chat_name = f"Chat ID: {chat_id} (error fetching details: {str(e)})"
        
        user_list.append(f"@{username} (Chat ID: {chat_id})")

    if user_list:
        user_report = "Users currently using the bot:\n\n" + "\n".join(user_list)
    else:
        user_report = "No users found."

    await update.message.reply_text(user_report)

# Help command (admin and owner)
async def helpme(update: Update, context):
    if not is_owner(update) and not is_admin(update):
        return  # Ignore requests from non-owners or non-admins

    help_text = (
        "/start - Start the bot\n"
        "/setreporttime HH:MM - Set daily report time\n"
        "/export - Export your transactions as a CSV file\n"
        "/graph - Get a graphical report of your transactions\n"
        "/reset - Reset all your transactions\n"
        "/sendmsg [message] - Send a message to all users (admin only)\n"
        "/removeuser @username - removeuser username/chat_id\n"
        "/removeallusers - removeallusers confirm\n"
        "/summary - Get a summary of all transactions across all chats\n"
        "/addadmin @username - Add a new admin (owner only)\n"
        "/removeadmin @username - Remove an admin (owner only)\n"
        "/listadmins - List all current admins (owner only)\n"
        "/deletesummary - Delete summary of all transactions (owner only)\n"
        "/showusers - Show all users using the bot (owner only)\n"
        "/helpme - Display this help message"
    )
    await update.message.reply_text(help_text)

# Summary of all transactions across all chats (admin and owner)
async def summary(update: Update, context):
    if not is_owner(update) and not is_admin(update):
        return  # Ignore requests from non-owners or non-admins

    admin_chat_id = update.message.chat.id
    summary_report = []
    chats = c.execute("SELECT DISTINCT chat_id FROM transactions").fetchall()

    for chat in chats:
        chat_id = chat[0]
        c.execute("SELECT SUM(amount) FROM transactions WHERE chat_id = ?", (chat_id,))
        total = c.fetchone()[0]
        if total is None:
            total = 0

        try:
            chat_obj = await context.bot.get_chat(chat_id)
            chat_name = chat_obj.title or chat_obj.username or str(chat_id)
        except Exception as e:
            chat_name = f"Chat ID: {chat_id} (error fetching details: {str(e)})"

        summary_report.append(f"{chat_name} (ID: {chat_id}) - Total: {total}")

    if summary_report:
        summary_message = "Summary of Transactions Across All Chats:\n\n" + "\n".join(summary_report)
    else:
        summary_message = "No transactions found across all chats."

    # Split and send the summary report in case it's too long
    for chunk in split_message(summary_message):
        await context.bot.send_message(chat_id=admin_chat_id, text=chunk)

# Graceful shutdown to ensure proper cleanup
async def shutdown(application):
    await application.shutdown()
    scheduler.shutdown(wait=False)
    conn.close()

# Main function to start the bot
def main():
    # Create the application with the secure token
    application = Application.builder().token(BOT_TOKEN).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))  # /start
    application.add_handler(CommandHandler("setreporttime", set_report_time))  # /setreporttime HH:MM
    application.add_handler(CommandHandler("sendmsg", sendmsg))  # /sendmsg
    application.add_handler(CommandHandler("removeuser", remove_user))  # /removeuser username/chat_id
    application.add_handler(CommandHandler("removeallusers", remove_all_users))  # /removeallusers confirm
    application.add_handler(CommandHandler("helpme", helpme))  # /helpme
    application.add_handler(CommandHandler("summary", summary))  # /summary
    application.add_handler(CommandHandler("export", export_transactions))  # /export
    application.add_handler(CommandHandler("graph", send_graph))  # /graph
    application.add_handler(CommandHandler("reset", reset_transactions))  # /reset
    application.add_handler(CommandHandler("addadmin", add_admin))  # /addadmin
    application.add_handler(CommandHandler("removeadmin", remove_admin))  # /removeadmin
    application.add_handler(CommandHandler("listadmins", list_admins))  # /listadmins
    application.add_handler(CommandHandler("deletesummary", delete_summary))  # /deletesummary
    application.add_handler(CommandHandler("showusers", show_users))  # /showusers

    # Add a message handler for regular messages (only owner or admin)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Schedule daily report to be sent at custom times (for the owner or admins)
    scheduler = AsyncIOScheduler()

    # Schedule daily reports based on user-set times
    for user_id, (hour, minute) in user_report_times.items():
        scheduler.add_job(send_daily_report, 'cron', hour=hour, minute=minute, args=[application])

    scheduler.start()

    # Graceful shutdown handling (useful for production)
    try:
        application.run_polling()
    except KeyboardInterrupt:
        shutdown(application)

if __name__ == '__main__':
    main()


