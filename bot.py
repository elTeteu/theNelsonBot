import os
import threading
import requests
import yfinance as yf
import numpy as np
from datetime import datetime, timedelta
from fredapi import Fred
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
from groq import Groq
from duckduckgo_search import DDGS

groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
NEWS_API_KEY = os.environ["NEWS_API_KEY"]
FRED_API_KEY = os.environ["FRED_API_KEY"]
fred = Fred(api_key=FRED_API_KEY)

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

def run_dcf(ticker: str) -> str:
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        shares_outstanding = info.get("sharesOutstanding")
        free_cash_flow = info.get("freeCashflow")
        total_cash = info.get("totalCash", 0)
        total_debt = info.get("totalDebt", 0)
        revenue_growth = info.get("revenueGrowth") or 0.08
        beta = info.get("beta") or 1.0

        if not all([current_price, shares_outstanding, free_cash_flow]):
            return f"Insufficient financial data for DCF on {ticker.upper()}."

        risk_free_rate = 0.045
        equity_risk_premium = 0.055
        wacc = risk_free_rate + beta * equity_risk_premium
        growth_rate_5y = min(max(revenue_growth, 0.03), 0.25)
        terminal_growth = 0.025

        projected_fcf = []
        fcf = free_cash_flow
        for year in range(1, 11):
            growth = growth_rate_5y if year <= 5 else growth_rate_5y - (growth_rate_5y - terminal_growth) * ((year - 5) / 5)
            fcf = fcf * (1 + growth)
            projected_fcf.append(fcf)

        pv_fcfs = [cf / (1 + wacc) ** (i + 1) for i, cf in enumerate(projected_fcf)]
        terminal_value = projected_fcf[-1] * (1 + terminal_growth) / (wacc - terminal_growth)
        pv_terminal = terminal_value / (1 + wacc) ** 10

        enterprise_value = sum(pv_fcfs) + pv_terminal
        equity_value = enterprise_value + total_cash - total_debt
        intrinsic_value_per_share = equity_value / shares_outstanding
        upside = ((intrinsic_value_per_share - current_price) / current_price) * 100
        verdict = "UNDERVALUED 🟢" if upside > 10 else "OVERVALUED 🔴" if upside < -10 else "FAIRLY VALUED 🟡"

        return (
            f"DCF Valuation: {info.get('longName', ticker)} ({ticker.upper()})\n\n"
            f"📊 Inputs:\n"
            f"  Current FCF: ${free_cash_flow:,}\n"
            f"  5Y Growth Rate: {growth_rate_5y*100:.1f}%\n"
            f"  Terminal Growth: {terminal_growth*100:.1f}%\n"
            f"  WACC: {wacc*100:.1f}% (Beta: {beta:.2f})\n"
            f"  Cash: ${total_cash:,}\n"
            f"  Debt: ${total_debt:,}\n\n"
            f"📈 Results:\n"
            f"  PV of FCFs: ${sum(pv_fcfs):,.0f}\n"
            f"  PV of Terminal Value: ${pv_terminal:,.0f}\n"
            f"  Enterprise Value: ${enterprise_value:,.0f}\n"
            f"  Equity Value: ${equity_value:,.0f}\n\n"
            f"💡 Conclusion:\n"
            f"  Intrinsic Value/Share: ${intrinsic_value_per_share:.2f}\n"
            f"  Current Price: ${current_price:.2f}\n"
            f"  Upside/Downside: {upside:+.1f}%\n"
            f"  Verdict: {verdict}"
        )
    except Exception as e:
        return f"Error running DCF for {ticker}: {e}"

def search_coingecko_id(coin_name: str):
    try:
        url = f"https://api.coingecko.com/api/v3/search?query={coin_name}"
        data = requests.get(url, timeout=10).json()
        coins = data.get("coins", [])
        if coins:
            return coins[0]["id"]
        return None
    except Exception:
        return None

def get_crypto_price(coin_input: str) -> str:
    try:
        alias_map = {
            "btc": "bitcoin", "eth": "ethereum", "sol": "solana",
            "bnb": "binancecoin", "xrp": "ripple", "ada": "cardano",
            "doge": "dogecoin", "dot": "polkadot", "avax": "avalanche-2",
            "matic": "matic-network", "link": "chainlink", "uni": "uniswap",
            "ltc": "litecoin", "atom": "cosmos", "xlm": "stellar",
            "zec": "zcash", "zcash": "zcash",
            "chainlink": "chainlink",
            "bittensor": "bittensor", "tao": "bittensor",
            "hyperliquid": "hyperliquid", "hype": "hyperliquid",
            "sui": "sui", "monero": "monero", "xmr": "monero",
            "pax gold": "pax-gold", "paxg": "pax-gold",
        }
        coin_lower = coin_input.lower().strip()
        coin_id = alias_map.get(coin_lower) or search_coingecko_id(coin_lower)
        if not coin_id:
            return f"Could not find crypto: {coin_input}"
        url = (
            f"https://api.coingecko.com/api/v3/simple/price"
            f"?ids={coin_id}&vs_currencies=usd"
            f"&include_24hr_change=true&include_market_cap=true&include_24hr_vol=true"
        )
        data = requests.get(url, timeout=10).json()
        if coin_id in data:
            c = data[coin_id]
            change = c.get("usd_24h_change", 0) or 0
            return (
                f"Crypto: {coin_input.capitalize()} ({coin_id})\n"
                f"Price: ${c['usd']:,}\n"
                f"24h Change: {change:.2f}%\n"
                f"24h Volume: ${c.get('usd_24h_vol', 0):,.0f}\n"
                f"Market Cap: ${c.get('usd_market_cap', 0):,.0f}"
            )
        return f"Could not retrieve price for: {coin_input}"
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

def get_economic_calendar(when: str = "today") -> str:
    """Fetch US economic data releases from FRED + web search for forecasts."""
    try:
        today = datetime.utcnow().date()
        if "tomorrow" in when:
            start_date = end_date = today + timedelta(days=1)
            label = (today + timedelta(days=1)).strftime("%A, %B %d, %Y")
        elif "week" in when:
            start_date = today
            end_date = today + timedelta(days=7)
            label = f"this week ({start_date} to {end_date})"
        elif "yesterday" in when:
            start_date = end_date = today - timedelta(days=1)
            label = (today - timedelta(days=1)).strftime("%A, %B %d, %Y")
        else:
            start_date = end_date = today
            label = today.strftime("%A, %B %d, %Y")

        url = (
            f"https://api.stlouisfed.org/fred/releases/dates"
            f"?api_key={FRED_API_KEY}&file_type=json"
            f"&realtime_start={start_date.strftime('%Y-%m-%d')}"
            f"&realtime_end={end_date.strftime('%Y-%m-%d')}"
            f"&include_release_dates_with_no_data=false&limit=1000"
        )
        response = requests.get(url, timeout=15)
        data = response.json()

        if "error_message" in data:
            return f"FRED API error: {data['error_message']}"

        release_dates = data.get("release_dates", [])

        output = f"📅 US Economic Data Releases — {label}:\n\n"

        if release_dates:
            seen = set()
            for r in release_dates[:30]:
                rname = r.get("release_name", "Unknown")
                rdate = r.get("date", "")
                key = (rname, rdate)
                if key not in seen:
                    seen.add(key)
                    output += f"• {rdate} — {rname}\n"
        else:
            output += "(No FRED releases found for this date range — may be weekend or holiday)\n"

        output += "\n--- Forecast, consensus & actual values from the web ---\n"
        if "tomorrow" in when:
            output += web_search("US economic calendar tomorrow high impact forecast consensus")
        elif "week" in when:
            output += web_search("US economic calendar this week high impact forecast consensus")
        else:
            output += web_search(f"US economic data released today {today.strftime('%B %d %Y')} forecast consensus actual")

        return output
    except Exception as e:
        return f"Error fetching economic calendar: {e}"

def get_fred_data(indicator: str) -> str:
    fred_map = {
        "cpi": "CPIAUCSL", "inflation": "CPIAUCSL",
        "pce": "PCEPI", "core cpi": "CPILFESL", "core pce": "PCEPILFE",
        "gdp": "GDP", "real gdp": "GDPC1", "gdp growth": "A191RL1Q225SBEA",
        "unemployment": "UNRATE", "unemployment rate": "UNRATE",
        "nonfarm payroll": "PAYEMS", "nfp": "PAYEMS", "jobless claims": "ICSA",
        "fed funds rate": "FEDFUNDS", "federal funds rate": "FEDFUNDS",
        "interest rate": "FEDFUNDS",
        "10 year": "DGS10", "10y yield": "DGS10",
        "2 year": "DGS2", "2y yield": "DGS2", "yield curve": "T10Y2Y",
        "housing starts": "HOUST", "house prices": "CSUSHPISA",
        "home prices": "CSUSHPISA",
        "m2": "M2SL", "money supply": "M2SL",
        "trade balance": "BOPGSTB", "retail sales": "RSAFS",
        "consumer sentiment": "UMCSENT",
        "consumer confidence": "CSCICP03USM665S",
    }
    series_id = None
    indicator_lower = indicator.lower()
    for key, sid in fred_map.items():
        if key in indicator_lower:
            series_id = sid
            break
    if not series_id:
        try:
            search_results = fred.search(indicator, limit=1)
            if not search_results.empty:
                series_id = search_results.index[0]
            else:
                return f"Could not find FRED data for: {indicator}"
        except Exception as e:
            return f"FRED search error: {e}"
    try:
        series = fred.get_series(series_id).dropna().tail(6)
        info = fred.get_series_info(series_id)
        name = info.get("title", series_id)
        units = info.get("units", "")
        latest_date = series.index[-1].strftime("%Y-%m-%d")
        latest_val = series.iloc[-1]
        history = "\n".join(
            [f"  {d.strftime('%Y-%m')}: {v:.2f}" for d, v in series.items()]
        )
        return (
            f"FRED: {name}\n"
            f"Series ID: {series_id}\n"
            f"Units: {units}\n"
            f"Latest ({latest_date}): {latest_val:.2f}\n"
            f"Recent history:\n{history}"
        )
    except Exception as e:
        return f"Error fetching FRED series {series_id}: {e}"

def detect_crypto_in_message(message: str):
    import re
    msg = message.lower()
    known = [
        "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
        "bnb", "xrp", "cardano", "ada", "dogecoin", "doge",
        "chainlink", "link", "bittensor", "tao", "hyperliquid", "hype",
        "sui", "monero", "xmr", "zcash", "zec", "pax gold", "paxg",
        "polkadot", "dot", "avalanche", "avax", "uniswap", "uni",
        "litecoin", "ltc", "cosmos", "atom", "stellar", "xlm",
    ]
    for name in known:
        if name in msg:
            return name
    patterns = [
        r"price of ([a-z]+)", r"([a-z]+) price",
        r"([a-z]+) coin", r"([a-z]+) token",
        r"how much is ([a-z]+)", r"what is ([a-z]+) trading at",
    ]
    for pattern in patterns:
        match = re.search(pattern, msg)
        if match:
            candidate = match.group(1)
            ignore = {"the", "a", "an", "this", "that", "what", "how", "is", "are", "stock"}
            if candidate not in ignore and len(candidate) >= 2:
                return candidate
    return None

def detect_and_fetch(message: str) -> str:
    import re
    msg = message.lower()
    context = ""

    # Economic calendar detection
    calendar_keywords = [
        "economic calendar", "data releases", "data release",
        "released today", "release today", "today's data",
        "this week's data", "data this week", "data tomorrow",
        "economic events", "upcoming data", "scheduled releases",
        "data being released", "what data", "economic data today",
        "economic data this week", "economic data tomorrow",
        "us economic releases", "us data releases"
    ]
    if any(k in msg for k in calendar_keywords):
        if "tomorrow" in msg:
            context += get_economic_calendar("tomorrow") + "\n\n"
        elif "week" in msg:
            context += get_economic_calendar("week") + "\n\n"
        elif "yesterday" in msg:
            context += get_economic_calendar("yesterday") + "\n\n"
        else:
            context += get_economic_calendar("today") + "\n\n"

    # DCF detection
    dcf_keywords = ["dcf", "discounted cash flow", "intrinsic value", "fair value",
                    "valuation of", "value of", "is it overvalued", "is it undervalued",
                    "worth buying", "margin of safety"]
    if any(k in msg for k in dcf_keywords):
        words = message.split()
        for word in words:
            clean = word.strip("$?,.")
            if clean.isupper() and 1 <= len(clean) <= 5:
                context += run_dcf(clean) + "\n\n"
                break
        if not context:
            match = re.search(r'\b([A-Z]{1,5})\b', message)
            if match:
                context += run_dcf(match.group(1)) + "\n\n"

    # Stock detection
    stock_keywords = ["stock", "share", "ticker", "price of", "p/e",
                      "market cap", "eps", "revenue"]
    if any(k in msg for k in stock_keywords):
        words = message.split()
        for word in words:
            clean = word.strip("$?,.")
            if clean.isupper() and 1 <= len(clean) <= 5:
                context += get_stock_data(clean) + "\n\n"
                break

    # Crypto detection
    crypto_keywords = ["crypto", "coin", "token", "bitcoin", "ethereum", "solana",
                       "zcash", "chainlink", "bittensor", "hyperliquid", "sui",
                       "monero", "pax gold", "btc", "eth", "bnb", "xrp", "doge",
                       "sol", "link", "tao", "hype", "xmr", "zec", "paxg", "ada"]
    if any(k in msg for k in crypto_keywords):
        coin = detect_crypto_in_message(message)
        if coin:
            context += get_crypto_price(coin) + "\n\n"

    # ISM PMI special handling
    pmi_keywords = ["pmi", "ism", "purchasing managers", "manufacturing index", "services index"]
    if any(k in msg for k in pmi_keywords):
        if "service" in msg:
            context += web_search("ISM Services PMI latest actual previous month") + "\n\n"
            context += web_search("ISM Services PMI next release date forecast consensus") + "\n\n"
        elif "manufactur" in msg:
            context += web_search("ISM Manufacturing PMI latest actual previous month") + "\n\n"
            context += web_search("ISM Manufacturing PMI next release date forecast consensus") + "\n\n"
        else:
            context += web_search("ISM Manufacturing PMI latest value previous month") + "\n\n"
            context += web_search("ISM Services PMI latest value previous month") + "\n\n"
            context += web_search("ISM PMI next release date forecast consensus") + "\n\n"

    # FRED / Economic data detection
    fred_keywords = [
        "cpi", "inflation", "gdp", "unemployment", "fed funds", "interest rate",
        "payroll", "nfp", "jobs", "housing", "retail sales", "yield", "pce",
        "m2", "money supply", "trade balance", "consumer sentiment",
        "macro", "federal reserve", "recession", "10 year", "2 year", "treasury"
    ]
    if any(k in msg for k in fred_keywords):
        context += get_fred_data(message) + "\n\n"

    # News detection
    news_keywords = ["news", "latest", "headline", "update", "report",
                     "announced", "today", "recently"]
    if any(k in msg for k in news_keywords) and "calendar" not in msg and "release" not in msg:
        context += get_news(message) + "\n\n"

    # Web search fallback
    search_keywords = ["what is", "who is", "how", "why", "when", "where",
                       "explain", "tell me about", "search"]
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

    await context.bot.send_chat_action(chat_id=msg.chat_id, action="typing")

    live_data = detect_and_fetch(user_text)

    system_prompt = (
        "You are a powerful financial and economic assistant with access to live market data. "
        "When live data is provided, use it to give accurate, up-to-date answers. "
        "For DCF valuations, the Python code has already calculated the intrinsic value — "
        "present the results clearly, explain the assumptions, and give a buy/hold/sell opinion. "
        "For economic calendar questions, list the scheduled releases clearly with names and dates. "
        "For ISM PMI, always mention actual, forecast, consensus and previous values if available. "
        "For crypto, always mention price, 24h change and market cap. "
        "Be concise but thorough. Use bullet points where helpful."
    )

    user_prompt = user_text
    if live_data:
        user_prompt = f"Live data fetched:\n{live_data}\n\nUser question: {user_text}"

    response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        max_tokens=1500
    )

    await msg.reply_text(response.choices[0].message.content)

app = ApplicationBuilder().token(os.environ["TELEGRAM_TOKEN"]).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling(drop_pending_updates=True)
