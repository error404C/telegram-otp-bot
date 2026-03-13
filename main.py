import os
import asyncio
import logging
from datetime import datetime
from flask import Flask, jsonify, request, render_template
from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from scraper import create_scraper
from otp_filter import otp_filter
from utils import format_otp_message, format_multiple_otps, get_status_message
import threading
import time

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Flask app
app = Flask(__name__)

# Bot configuration
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GROUP_ID = os.getenv('TELEGRAM_GROUP_ID')
IVASMS_EMAIL = os.getenv('IVASMS_EMAIL')
IVASMS_PASSWORD = os.getenv('IVASMS_PASSWORD')

# Bot statistics
bot_stats = {
    'start_time': datetime.now(),
    'total_otps_sent': 0,
    'last_check': 'Never',
    'last_error': None,
    'is_running': False
}

# Global instances
bot = None
scraper = None

# Dedicated event loop for all async Telegram calls
_loop = asyncio.new_event_loop()

def run_async(coro):
    """Run an async coroutine safely from any thread using the dedicated loop."""
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=30)

def start_event_loop():
    """Start the dedicated asyncio event loop in a background thread."""
    asyncio.set_event_loop(_loop)
    _loop.run_forever()

# Telegram Command Handlers
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = """🤖 <b>Telegram OTP Bot</b>

🎯 <b>Available Commands:</b>
/start - Show this help message
/status - Show bot status and statistics
/check - Manually check for new OTPs
/test - Send a test OTP message
/stats - Show detailed statistics

🔐 <b>What I do:</b>
• Monitor IVASMS.com for new OTPs
• Send formatted OTPs to the group
• Prevent duplicate notifications
• Run 24/7 with automatic monitoring

📊 <b>Current Status:</b>
Bot is running and monitoring every 60 seconds."""
    await update.message.reply_text(welcome_message, parse_mode='HTML')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = datetime.now() - bot_stats['start_time']
    uptime_str = str(uptime).split('.')[0]
    cache_stats = otp_filter.get_cache_stats()
    status_data = {
        'uptime': uptime_str,
        'total_otps_sent': bot_stats['total_otps_sent'],
        'last_check': bot_stats['last_check'],
        'cache_size': cache_stats['total_cached'],
        'monitor_running': bot_stats['is_running']
    }
    status_msg = get_status_message(status_data)
    await update.message.reply_text(status_msg, parse_mode='HTML')

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 <b>Checking for new OTPs...</b>", parse_mode='HTML')
    try:
        check_and_send_otps()
        await update.message.reply_text(
            f"✅ <b>OTP check completed!</b>\n\nLast check: {bot_stats['last_check']}\nTotal OTPs sent: {bot_stats['total_otps_sent']}",
            parse_mode='HTML'
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ <b>Error during OTP check:</b>\n<code>{str(e)}</code>",
            parse_mode='HTML'
        )

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    test_otp = {
        'otp': '123456',
        'phone': '+8801234567890',
        'service': 'Test Service',
        'timestamp': datetime.now().strftime('%H:%M:%S'),
        'raw_message': 'This is a test OTP message from the bot'
    }
    try:
        test_message = format_otp_message(test_otp)
        await context.bot.send_message(chat_id=GROUP_ID, text=test_message, parse_mode='HTML')
        await update.message.reply_text("✅ <b>Test message sent to the group!</b>", parse_mode='HTML')
    except Exception as e:
        await update.message.reply_text(
            f"❌ <b>Failed to send test message:</b>\n<code>{str(e)}</code>",
            parse_mode='HTML'
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = datetime.now() - bot_stats['start_time']
    uptime_str = str(uptime).split('.')[0]
    cache_stats = otp_filter.get_cache_stats()
    stats_message = f"""📊 <b>Detailed Bot Statistics</b>

⏱️ <b>Runtime Information:</b>
• Uptime: {uptime_str}
• Started: {bot_stats['start_time'].strftime('%Y-%m-%d %H:%M:%S')}
• Status: {'🟢 Running' if bot_stats['is_running'] else '🔴 Stopped'}

📨 <b>OTP Statistics:</b>
• Total OTPs Sent: {bot_stats['total_otps_sent']}
• Last Check: {bot_stats['last_check']}
• Cache Size: {cache_stats['total_cached']} items
• Cache Expiry: {cache_stats['expire_minutes']} minutes

🔧 <b>System Information:</b>
• IVASMS Account: {IVASMS_EMAIL[:20] if IVASMS_EMAIL else 'N/A'}...
• Target Group: {GROUP_ID}
• Check Interval: 60 seconds
• Last Error: {bot_stats['last_error'] or 'None'}"""
    await update.message.reply_text(stats_message, parse_mode='HTML')

def initialize_bot():
    """Initialize Telegram bot and scraper"""
    global bot, scraper

    try:
        if not BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables")
        if not GROUP_ID:
            raise ValueError("TELEGRAM_GROUP_ID not found in environment variables")
        if not IVASMS_EMAIL or not IVASMS_PASSWORD:
            raise ValueError("IVASMS credentials not found in environment variables")

        bot = Bot(token=BOT_TOKEN)
        logger.info("Telegram bot initialized successfully")

        scraper = create_scraper(IVASMS_EMAIL, IVASMS_PASSWORD)
        if scraper:
            logger.info("IVASMS scraper initialized successfully")
        else:
            logger.warning("Failed to initialize IVASMS scraper")

        return True

    except Exception as e:
        logger.error(f"Failed to initialize bot: {e}")
        bot_stats['last_error'] = str(e)
        return False

def send_telegram_message(message, parse_mode='HTML'):
    """Send message to Telegram group using the shared event loop."""
    try:
        if not bot or not GROUP_ID:
            logger.error("Bot or Group ID not configured")
            return False

        run_async(bot.send_message(chat_id=GROUP_ID, text=message, parse_mode=parse_mode))
        logger.info("Message sent to Telegram successfully")
        return True

    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        bot_stats['last_error'] = str(e)
        return False

def start_telegram_polling():
    """Start Telegram command polling in a dedicated thread with its own event loop."""
    def run_polling():
        poll_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(poll_loop)

        async def polling_main():
            telegram_app = Application.builder().token(BOT_TOKEN).build()
            telegram_app.add_handler(CommandHandler("start", start_command))
            telegram_app.add_handler(CommandHandler("status", status_command))
            telegram_app.add_handler(CommandHandler("check", check_command))
            telegram_app.add_handler(CommandHandler("test", test_command))
            telegram_app.add_handler(CommandHandler("stats", stats_command))

            await telegram_app.initialize()
            await telegram_app.start()
            await telegram_app.updater.start_polling(drop_pending_updates=True)
            logger.info("Telegram command polling started ✅")

            # Keep running until stopped
            while True:
                await asyncio.sleep(3600)

        try:
            poll_loop.run_until_complete(polling_main())
        except Exception as e:
            logger.error(f"Polling error: {e}")

    t = threading.Thread(target=run_polling, daemon=True)
    t.start()

def check_and_send_otps():
    """Check for new OTPs and send to Telegram"""
    try:
        if not scraper:
            logger.error("Scraper not initialized")
            return

        logger.info("Checking for new OTPs...")
        messages = scraper.fetch_messages()
        bot_stats['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if not messages:
            logger.info("No messages found")
            return

        new_messages = otp_filter.filter_new_otps(messages)

        if not new_messages:
            logger.info("No new OTPs found (all were duplicates)")
            return

        logger.info(f"Found {len(new_messages)} new OTPs")

        if len(new_messages) == 1:
            message = format_otp_message(new_messages[0])
        else:
            message = format_multiple_otps(new_messages)

        if send_telegram_message(message):
            bot_stats['total_otps_sent'] += len(new_messages)
            logger.info(f"Successfully sent {len(new_messages)} OTPs to Telegram")
        else:
            logger.error("Failed to send OTPs to Telegram")

    except Exception as e:
        logger.error(f"Error in check_and_send_otps: {e}")
        bot_stats['last_error'] = str(e)

def background_monitor():
    """Background thread to monitor for OTPs"""
    bot_stats['is_running'] = True
    logger.info("Background OTP monitor started")

    while bot_stats['is_running']:
        try:
            check_and_send_otps()
            time.sleep(60)
        except Exception as e:
            logger.error(f"Error in background monitor: {e}")
            bot_stats['last_error'] = str(e)
            time.sleep(120)

# Flask routes
@app.route('/')
def home():
    if 'text/html' in request.headers.get('Accept', ''):
        return render_template('dashboard.html')
    uptime = datetime.now() - bot_stats['start_time']
    return jsonify({
        'status': 'running',
        'uptime': str(uptime).split('.')[0],
        'total_otps_sent': bot_stats['total_otps_sent'],
        'last_check': bot_stats['last_check'],
        'last_error': bot_stats['last_error'],
        'monitor_running': bot_stats['is_running']
    })

@app.route('/check-otp')
def manual_check():
    try:
        check_and_send_otps()
        return jsonify({'status': 'success', 'message': 'OTP check completed', 'timestamp': datetime.now().isoformat()})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/status')
def bot_status():
    uptime = datetime.now() - bot_stats['start_time']
    cache_stats = otp_filter.get_cache_stats()
    status = {
        'uptime': str(uptime).split('.')[0],
        'total_otps_sent': bot_stats['total_otps_sent'],
        'last_check': bot_stats['last_check'],
        'cache_size': cache_stats['total_cached'],
        'monitor_running': bot_stats['is_running']
    }
    if request.args.get('send') == 'true':
        message = get_status_message(status)
        if send_telegram_message(message):
            return jsonify({'status': 'success', 'message': 'Status sent to Telegram'})
        return jsonify({'status': 'error', 'message': 'Failed to send status'}), 500
    return jsonify(status)

@app.route('/test-message')
def test_message():
    test_msg = """🧪 <b>Test Message</b>

🔢 OTP: <code>123456</code>
📱 Number: <code>+1234567890</code>
🌐 Service: <b>Test Service</b>
⏰ Time: Test Time

<i>This is a test message from the bot!</i>"""
    if send_telegram_message(test_msg):
        return jsonify({'status': 'success', 'message': 'Test message sent'})
    return jsonify({'status': 'error', 'message': 'Failed to send test message'}), 500

@app.route('/clear-cache')
def clear_cache():
    try:
        result = otp_filter.clear_cache()
        return jsonify({'status': 'success', 'message': result})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/start-monitor')
def start_monitor():
    if bot_stats['is_running']:
        return jsonify({'status': 'info', 'message': 'Monitor already running'})
    try:
        t = threading.Thread(target=background_monitor, daemon=True)
        t.start()
        return jsonify({'status': 'success', 'message': 'Background monitor started'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/stop-monitor')
def stop_monitor():
    bot_stats['is_running'] = False
    return jsonify({'status': 'success', 'message': 'Background monitor stopped'})

@app.errorhandler(404)
def not_found(error):
    return jsonify({'status': 'error', 'message': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

def main():
    logger.info("Starting Telegram OTP Bot...")

    # Start the shared async event loop in background
    loop_thread = threading.Thread(target=start_event_loop, daemon=True)
    loop_thread.start()

    if not initialize_bot():
        logger.error("Failed to initialize bot. Check your configuration.")
        return

    # Start Telegram command polling
    start_telegram_polling()

    # Send startup message
    startup_message = """🚀 <b>Bot Started Successfully!</b>

✅ IVASMS scraper initialized
✅ Telegram bot connected
✅ Command handlers active
🔍 Monitoring for new OTPs...

📋 <b>Available Commands:</b>
/start - Show help and commands
/status - Bot status
/check - Manual OTP check
/test - Send test message
/stats - Detailed statistics

<i>Bot is now running and will automatically send new OTPs to this group.</i>"""

    time.sleep(2)  # Give polling time to start
    send_telegram_message(startup_message)

    # Start background OTP monitor
    monitor_thread = threading.Thread(target=background_monitor, daemon=True)
    monitor_thread.start()

    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting Flask server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == '__main__':
    main()
    
