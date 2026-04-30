import os
import threading
import requests
import yfinance as yf
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
from groq import Groq
from duckduckgo_search import DDGS

groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
NEWS_API_KEY = os.environ["NEWS_API_KEY"]

# Dummy web server to keep Render happy
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# --- Tool functions ---

def get_stock_data(ticker: str) -> str:
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        hist = stock.history(period="1d")
        price = hist["Close"].iloc[-1] if not hist.empty else info.get("currentPrice", "N/A")
        return (
            f"Stock: {info.get('longName', ticker)} ({ticker.upper()})\n"
            f"Price: ${price:.2f}\n"
            f"Market Cap: ${info.get('marketCap', 0):,}\n"
            f"P/E Ratio: {info.get('trailingPE', 'N/A')}\n"
            f"52W High: ${info.get('fiftyTwoWeekHigh', 'N/A')}\n"
            f"52W Low: ${info.get('fiftyTwoWeekLow', 'N/A')}\n"
            f"Revenue (TTM): ${info.get('totalRevenue', 0):,}\n"
            f"Free Cash Flow: ${info.get('freeCashflow', 0):,}\n"
            f"EPS: {info.get('trailingEps', 'N/A')}\n"
            f"Dividend Yield: {info.get('dividendYield', 'N/A')}"
        )
    except Exception as e:
        return f"Error fetching stock data: {e}"

def get_crypto_price(coin: str) -> str:
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd&include_24hr_change=true&include_market_cap=true"
        data = requests.get(url).json()
        if coin in data:
            c = data[coin]
            return (
                f"Crypto: {coin.capitalize()}\n"
                f"Price: ${c['usd']:,}\n"
                f"24h Change: {c.get('usd_24h_change', 'N/A'):.2f}%\n"
                f"Market Cap: ${c.get('usd_market_cap', 0):,}"
            )
        return f"Could not find crypto: {coin}"
    except Exception as e:
        return f"Error fetching crypto data: {e}"

def get_news(query: str) -> str:
    try:
        url = f"https://newsapi.org/v2/everything?q={query}&sortBy=publishedAt&pageSize=5&apiKey={NEWS_API_KEY}"
        articles = requests.get(url).json().get("articles", [])
        if not articles:
            return "No news found."
        result = f"Latest news for '{query}':\n\n"
        for i, a in enumerate(articles[:5], 1):
            result += f"{i}. {a['title']}\n   {a['source']['name']} — {a['publishedAt'][:10]}\n\n"
        return result
    except Exception as e:
        return f"Error fetching news: {e}"

def web_search(query: str) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            return "No results found."
        output = f"Web search results for '{query}':\n\n"
        for i, r in enumerate(results, 1):
            output += f"{i}. {r['title']}\n   {r['body'][:200]}\n\n"
        return output
    except Exception as e:
        return f"Error searching web: {e}"

def detect_and_fetch(message: str) -> str:
    msg = message.lower()
    context = ""

    # Stock detection
    stock_keywords = ["stock", "share", "ticker", "price of", "p/e", "dcf", "valuation", "market cap"]
    words = message.split()
    if any(k in msg for k in stock_keywords):
        # Try to find ticker symbol (uppercase word 1-5 chars)
        for word in words:
            clean = word.strip("$?,.")
            if clean.isupper() and 1 <= len(clean) <= 5:
                context += get_stock_data(clean) + "\n\n"
                break

    # Crypto detection
    crypto_names = ["bitcoin", "ethereum", "solana", "bnb", "xrp", "cardano", "dogecoin", "crypto"]
    crypto_map = {
        "bitcoin": "bitcoin", "btc": "bitcoin",
        "ethereum": "ethereum", "eth": "ethereum",
        "solana": "solana", "sol": "solana",
        "bnb": "binancecoin", "xrp": "ripple",
        "cardano": "cardano", "ada": "cardano",
        "dogecoin": "dogecoin", "doge": "dogecoin"
    }
    for key, coin_id in crypto_map.items():
        if key in msg:
            context += get_crypto_price(coin_id) + "\n\n"
            break

    # News detection
    news_keywords = ["news", "latest", "headline", "update", "report", "announced", "today"]
    if any(k in msg for k in news_keywords):
        context += get_news(message) + "\n\n"

    # Web search for everything else or general questions
    search_keywords = ["what is", "who is", "how", "why", "when", "where", "explain", "tell me about", "search"]
    if not context or any(k in msg for k in search_keywords):
        context += web_search(message) + "\n\n"

    return context.strip()

# --- Bot handler ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    bot_username = context.bot.username
    is_private = msg.chat.type == "private"
    is_mentioned = f"@{bot_username}" in msg.text
    is_reply_to_bot = (
        msg.reply_to_message and
        msg.reply_to_message.from_user.username == bot_username
    )

    if not (is_private or is_mentioned or is_reply_to_bot):
        return

    user_text = msg.text.replace(f"@{bot_username}", "").strip()

    # Fetch live data if needed
    live_data = detect_and_fetch(user_text)

    # Build prompt
    system_prompt = (
        "You are a helpful financial and general assistant with access to live data. "
        "When live data is provided below, use it to answer accurately. "
        "For DCF valuations, use the financial data provided and standard assumptions. "
        "Be concise but thorough."
    )

    user_prompt = user_text
    if live_data:
        user_prompt = f"Live data fetched:\n{live_data}\n\nUser question: {user_text}"

    response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    )

    await msg.reply_text(response.choices[0].message.content)

app = ApplicationBuilder().token(os.environ["TELEGRAM_TOKEN"]).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling(drop_pending_updates=True)
