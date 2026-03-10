import streamlit as st
import requests
from bs4 import BeautifulSoup
from groq import Groq
from dotenv import load_dotenv
import os
import time
import threading
import json

load_dotenv()

# ── CLIENTS ─────────────────────────────────────────────────
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ── PERSISTENCE ──────────────────────────────────────────────
DATA_FILE = "watchlist.json"

def save_data():
    """Save watchlist and alerts log to file"""
    data = {
        "watched_stocks": st.session_state.watched_stocks,
        "alerts_log": st.session_state.alerts_log[-50:],
    }
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

def load_data():
    """Load saved watchlist from file"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

# ── SESSION STATE ───────────────────────────────────────────
_saved = load_data()

if "sent_news" not in st.session_state:
    st.session_state.sent_news = set()
if "watched_stocks" not in st.session_state:
    st.session_state.watched_stocks = _saved.get("watched_stocks", [])
if "alerts_log" not in st.session_state:
    st.session_state.alerts_log = _saved.get("alerts_log", [])
if "auto_monitor" not in st.session_state:
    st.session_state.auto_monitor = False
if "scanner_running" not in st.session_state:
    st.session_state.scanner_running = False
if "sent_promoter_alerts" not in st.session_state:
    st.session_state.sent_promoter_alerts = set()
if "stock_input_key" not in st.session_state:
    st.session_state.stock_input_key = 0
if "selected_stock" not in st.session_state:
    st.session_state.selected_stock = st.session_state.watched_stocks[0] if st.session_state.watched_stocks else None


# ── TELEGRAM ─────────────────────────────────────────────────
def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except:
        return False


# ── SCRAPERS ─────────────────────────────────────────────────
def get_screener_data(stock_name):
    try:
        query = stock_name.replace(" ", "%20")
        search_url = f"https://www.screener.in/api/company/search/?q={query}&v=3&fts=1"
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        results = requests.get(search_url, headers=headers, timeout=10).json()
        if not results:
            return None
        slug = results[0].get("url", "")
        company_url = f"https://www.screener.in{slug}"
        soup = BeautifulSoup(requests.get(company_url, headers=headers, timeout=10).text, "html.parser")
        ratios = {}
        for item in soup.select("#top-ratios li"):
            name = item.select_one(".name")
            value = item.select_one(".value, .number")
            if name and value:
                ratios[name.text.strip()] = value.text.strip()
        pros = [li.text.strip() for li in soup.select(".pros li")][:3]
        cons = [li.text.strip() for li in soup.select(".cons li")][:3]
        return {"url": company_url, "ratios": ratios, "pros": pros, "cons": cons}
    except Exception as e:
        return {"error": str(e)}


def get_nse_data(stock_symbol):
    try:
        symbol = stock_symbol.upper().replace(" ", "")
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Referer": "https://www.nseindia.com/",
            "Accept-Language": "en-US,en;q=0.9",
        }
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        data = session.get(f"https://www.nseindia.com/api/quote-equity?symbol={symbol}", headers=headers, timeout=10).json()
        pd = data.get("priceInfo", {})
        info = data.get("info", {})
        return {
            "symbol": info.get("symbol", symbol),
            "company_name": info.get("companyName", ""),
            "last_price": pd.get("lastPrice", "N/A"),
            "change": pd.get("change", "N/A"),
            "pChange": round(float(pd.get("pChange", 0)), 2),
            "day_high": pd.get("intraDayHighLow", {}).get("max", "N/A"),
            "day_low": pd.get("intraDayHighLow", {}).get("min", "N/A"),
            "week_high": pd.get("weekHighLow", {}).get("max", "N/A"),
            "week_low": pd.get("weekHighLow", {}).get("min", "N/A"),
            "open": pd.get("open", "N/A"),
            "close": pd.get("close", "N/A"),
        }
    except Exception as e:
        return {"error": str(e)}


def get_bse_data(stock_symbol):
    try:
        symbol = stock_symbol.upper().replace(" ", "")
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        results = requests.get(f"https://www.screener.in/api/company/search/?q={symbol}&v=3&fts=1", headers=headers, timeout=10).json()
        if not results:
            return {"error": "Not found"}
        slug = results[0].get("url", "")
        soup = BeautifulSoup(requests.get(f"https://www.screener.in{slug}", headers=headers, timeout=10).text, "html.parser")
        bse_code = None
        for a in soup.select("a[href*='bseindia']"):
            parts = [p for p in a.get("href", "").split("/") if p.isdigit() and len(p) == 6]
            if parts:
                bse_code = parts[0]
                break
        if not bse_code:
            import re
            for tag in soup.find_all(["a", "span", "div"]):
                match = re.search(r'BSE:\s*(\d{6})', tag.get_text())
                if match:
                    bse_code = match.group(1)
                    break
        if not bse_code:
            return {"error": "BSE code not found"}
        raw = requests.get(
            f"https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w?Debtflag=&scripcode={bse_code}&seriesid=",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bseindia.com/"},
            timeout=10
        ).json()
        curr = raw.get("CurrRate", {})
        cmp = raw.get("Cmpname", {})
        header = raw.get("Header", {})
        return {
            "scrip_code": bse_code,
            "company_name": cmp.get("FullN", results[0].get("name", "N/A")),
            "last_price": curr.get("LTP", "N/A"),
            "change": curr.get("Chg", "N/A"),
            "pChange": curr.get("PcChg", "N/A"),
            "open": header.get("Open", "N/A"),
            "day_high": header.get("High", "N/A"),
            "day_low": header.get("Low", "N/A"),
            "prev_close": header.get("PrevClose", "N/A"),
        }
    except Exception as e:
        return {"error": str(e)}


def get_stock_news(stock_name):
    try:
        query = stock_name.replace(" ", "+")
        url = f"https://news.google.com/rss/search?q={query}+NSE+BSE+stock&hl=en-IN&gl=IN&ceid=IN:en"
        soup = BeautifulSoup(requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}).content, "html.parser")
        return [{"title": i.title.text, "date": i.pubDate.text if i.pubDate else ""} for i in soup.findAll("item")[:5] if i.title]
    except:
        return []


# ── PROMOTER SCANNER ─────────────────────────────────────────
def scan_promoter_buying():
    try:
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com/", "Accept-Language": "en-US,en;q=0.9"}
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        bulk_data = session.get("https://www.nseindia.com/api/bulk-deals", headers=headers, timeout=10).json()
        findings = []
        for deal in bulk_data.get("data", [])[:50]:
            client = deal.get("clientName", "").upper()
            symbol = deal.get("symbol", "")
            deal_type = deal.get("buySell", "")
            qty = deal.get("quantity", 0)
            price = deal.get("price", 0)
            if any(kw in client for kw in ["PROMOTER", "FOUNDER", "DIRECTOR", "MANAGING", "CHAIRMAN"]) and deal_type == "BUY":
                key = f"{symbol}_{client}_{qty}"
                if key not in st.session_state.sent_promoter_alerts:
                    findings.append({"symbol": symbol, "buyer": client, "qty": qty, "price": price})
                    st.session_state.sent_promoter_alerts.add(key)
        return findings
    except:
        return []


def scan_nse_announcements():
    try:
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com/"}
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        data = session.get("https://www.nseindia.com/api/corporate-announcements?index=equities", headers=headers, timeout=10).json()
        findings = []
        for item in data[:100]:
            subject = item.get("subject", "").lower()
            symbol = item.get("symbol", "")
            if any(kw in subject for kw in ["promoter", "insider", "director", "bulk deal", "acquisition"]):
                key = f"{symbol}_{subject[:50]}"
                if key not in st.session_state.sent_promoter_alerts:
                    findings.append({"symbol": symbol, "subject": item.get("subject", ""), "date": item.get("an_dt", "")})
                    st.session_state.sent_promoter_alerts.add(key)
        return findings
    except:
        return []


def run_market_scanner():
    all_findings = []
    for f in scan_promoter_buying():
        send_telegram(f"🚨 *PROMOTER BUYING ALERT*\n\n📊 *Stock:* {f['symbol']}\n👤 *Buyer:* {f['buyer']}\n🔢 *Qty:* {f['qty']:,} shares\n💰 *Price:* ₹{f['price']}\n\n_Stock Alert Bot_")
        all_findings.append(f"🚨 Promoter buying: {f['symbol']} by {f['buyer']}")
    for f in scan_nse_announcements():
        send_telegram(f"📢 *NSE ANNOUNCEMENT*\n\n📊 *Stock:* {f['symbol']}\n📋 *Subject:* {f['subject']}\n📅 *Date:* {f['date']}\n\n_Stock Alert Bot_")
        all_findings.append(f"📢 {f['symbol']} — {f['subject'][:60]}")
    return all_findings


# ── AI ANALYSIS ──────────────────────────────────────────────
def analyse_with_ai(stock_name, news_list, nse_data, bse_data, screener_data):
    news_text = "\n".join([f"- {n['title']}" for n in news_list]) if news_list else "No news"
    nse_text = f"Price: ₹{nse_data.get('last_price')} | Change: {nse_data.get('pChange')}% | High: ₹{nse_data.get('day_high')} | Low: ₹{nse_data.get('day_low')}" if nse_data and "error" not in nse_data else "Unavailable"
    bse_text = f"Price: ₹{bse_data.get('last_price')} | Change: {bse_data.get('pChange')}%" if bse_data and "error" not in bse_data else "Unavailable"
    screener_text = " | ".join([f"{k}: {v}" for k, v in list(screener_data.get("ratios", {}).items())[:6]]) if screener_data and "error" not in screener_data else "Unavailable"
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": f"You are an expert Indian stock analyst.\nStock: {stock_name}\nNSE: {nse_text}\nBSE: {bse_text}\nScreener: {screener_text}\nNews:\n{news_text}\n\nGive a Telegram-friendly analysis (max 200 words). Verdict: Buy/Hold/Sell. Use emojis."}],
        max_tokens=400
    )
    return response.choices[0].message.content


def check_and_alert(stock_name, silent=False):
    news_list = get_stock_news(stock_name)
    nse_data = get_nse_data(stock_name.split()[0])
    bse_data = get_bse_data(stock_name.split()[0])
    screener_data = get_screener_data(stock_name)
    new_news = [n for n in news_list if n["title"] not in st.session_state.sent_news]
    for n in new_news:
        st.session_state.sent_news.add(n["title"])
    if not new_news and not silent:
        return "✅ No new news", nse_data, bse_data, screener_data
    if new_news:
        analysis = analyse_with_ai(stock_name, new_news, nse_data, bse_data, screener_data)
        nse_line = f"₹{nse_data.get('last_price', 'N/A')} ({nse_data.get('pChange', '')}%)" if nse_data and "error" not in nse_data else "N/A"
        bse_line = f"₹{bse_data.get('last_price', 'N/A')} ({bse_data.get('pChange', '')}%)" if bse_data and "error" not in bse_data else "N/A"
        headlines = "\n".join([f"• {n['title']}" for n in new_news[:3]])
        send_telegram(f"📈 *Stock Alert: {stock_name}*\n\n💹 *NSE:* {nse_line}\n🏛️ *BSE:* {bse_line}\n\n🗞️ *Headlines:*\n{headlines}\n\n🤖 *AI Analysis:*\n{analysis}\n\n_Stock Alert Bot_")
        log = f"✅ Telegram alert sent for {stock_name} — {len(new_news)} new articles"
        st.session_state.alerts_log.append(log)
        save_data()
        return log, nse_data, bse_data, screener_data
    return "✅ No new news to alert", nse_data, bse_data, screener_data


# ── BACKGROUND LOOPS ─────────────────────────────────────────
def auto_monitor_loop():
    while st.session_state.get("auto_monitor", False):
        for stock in st.session_state.watched_stocks:
            try:
                check_and_alert(stock, silent=True)
            except:
                pass
        time.sleep(1800)


def scanner_loop():
    while st.session_state.get("scanner_running", False):
        try:
            run_market_scanner()
        except:
            pass
        time.sleep(3600)


# ── UI ───────────────────────────────────────────────────────
st.set_page_config(page_title="Stock Alert System", page_icon="📈", layout="wide")
st.title("📈 Stock Alert System")
st.caption("Live data from NSE · BSE · Screener.in — Telegram alerts — Promoter Scanner")

# ── SIDEBAR ──────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")

    st.subheader("📬 Telegram")
    if st.button("🔔 Send Test Message", use_container_width=True):
        ok = send_telegram("✅ *Stock Alert Bot is connected!*\n\nYou will receive alerts here. 📈")
        st.success("✅ Telegram working!") if ok else st.error("❌ Telegram failed")

    st.divider()

    st.subheader("➕ Add Stocks")
    st.caption("Use NSE symbol e.g. RELIANCE, HDFCBANK")

    new_stocks_input = st.text_input(
        "Stock symbol(s)",
        placeholder="RELIANCE, TCS, HDFCBANK",
        key=f"stock_input_{st.session_state.stock_input_key}"
    )

    if st.button("Add Stocks", use_container_width=True):
        if new_stocks_input:
            stocks_to_add = [s.strip().upper() for s in new_stocks_input.split(",") if s.strip()]
            added, skipped = [], []
            for stock in stocks_to_add:
                if stock not in st.session_state.watched_stocks:
                    st.session_state.watched_stocks.append(stock)
                    added.append(stock)
                else:
                    skipped.append(stock)
            if added:
                st.success(f"✅ Added: {', '.join(added)}")
                st.session_state.selected_stock = added[-1]
                st.session_state.stock_input_key += 1
                save_data()  # 💾 Save immediately
            if skipped:
                st.warning(f"Already watching: {', '.join(skipped)}")
            st.rerun()

    st.divider()

    st.subheader("🔔 Auto Monitor")
    st.caption("Checks watchlist every 30 mins for new news")
    if not st.session_state.auto_monitor:
        if st.button("▶️ Start Auto Monitor", use_container_width=True):
            st.session_state.auto_monitor = True
            threading.Thread(target=auto_monitor_loop, daemon=True).start()
            st.rerun()
    else:
        st.success("🟢 Auto monitor is ON")
        if st.button("⏹️ Stop Auto Monitor", use_container_width=True):
            st.session_state.auto_monitor = False
            st.rerun()

    st.divider()

    st.subheader("🔍 Promoter Scanner")
    st.caption("Scans ALL NSE stocks every hour for promoter buying")
    if not st.session_state.scanner_running:
        if st.button("▶️ Start Scanner", use_container_width=True):
            st.session_state.scanner_running = True
            threading.Thread(target=scanner_loop, daemon=True).start()
            st.rerun()
    else:
        st.success("🟢 Promoter Scanner is ON")
        if st.button("⏹️ Stop Scanner", use_container_width=True):
            st.session_state.scanner_running = False
            st.rerun()
    st.divider()

    st.subheader("👀 Watchlist")
    if st.session_state.watched_stocks:
        for stock in st.session_state.watched_stocks:
            col1, col2 = st.columns([3, 1])
            with col1:
                if st.button(f"📊 {stock}", key=f"select_{stock}", use_container_width=True):
                    st.session_state.selected_stock = stock
                    st.rerun()
            with col2:
                if st.button("✕", key=f"rm_{stock}"):
                    st.session_state.watched_stocks.remove(stock)
                    if st.session_state.selected_stock == stock:
                        st.session_state.selected_stock = st.session_state.watched_stocks[0] if st.session_state.watched_stocks else None
                    save_data()  # 💾 Save after removal
                    st.rerun()
    else:
        st.info("No stocks added yet")


# ── MAIN TABS ────────────────────────────────────────────────
main_tab1, main_tab2 = st.tabs(["📊 My Watchlist", "🔍 Promoter Scanner"])

with main_tab1:
    if not st.session_state.watched_stocks:
        st.info("👈 Add stock symbols from the sidebar to get started!")
        st.markdown("""
        **Common NSE Symbols:**
        | Company | Symbol |
        |---------|--------|
        | Reliance Industries | RELIANCE |
        | HDFC Bank | HDFCBANK |
        | TCS | TCS |
        | Infosys | INFY |
        | ICICI Bank | ICICIBANK |
        | IDFC First Bank | IDFCFIRSTB |
        | SBI | SBIN |
        | Wipro | WIPRO |
        | Bajaj Finance | BAJFINANCE |
        | Asian Paints | ASIANPAINT |
        """)
    else:
        col_select, col_checkall = st.columns([3, 1])
        with col_select:
            current_options = st.session_state.watched_stocks
            default_idx = 0
            if st.session_state.selected_stock in current_options:
                default_idx = current_options.index(st.session_state.selected_stock)

            chosen = st.selectbox(
                "📊 Select Stock",
                options=current_options,
                index=default_idx,
                key="stock_selector"
            )
            st.session_state.selected_stock = chosen

        with col_checkall:
            st.write("")
            if st.button("🔍 Check All", use_container_width=True):
                for stock in st.session_state.watched_stocks:
                    with st.spinner(f"Fetching {stock}..."):
                        result, *_ = check_and_alert(stock)
                    st.write(result)

        st.divider()

        stock = st.session_state.selected_stock

        if stock:
            st.subheader(f"📊 {stock}")

            if st.button(f"🔍 Check Now — {stock}", use_container_width=True):
                with st.spinner(f"Fetching live data for {stock}..."):
                    result, nse_data, bse_data, screener_data = check_and_alert(stock)

                st.success(result)
                c1, c2, c3 = st.columns(3)

                with c1:
                    st.subheader("📈 NSE")
                    if nse_data and "error" not in nse_data:
                        st.metric("Price", f"₹{nse_data.get('last_price', 'N/A')}", f"{nse_data.get('pChange', 0)}%")
                        st.write(f"**Open:** ₹{nse_data.get('open', 'N/A')}")
                        st.write(f"**Day High:** ₹{nse_data.get('day_high', 'N/A')}")
                        st.write(f"**Day Low:** ₹{nse_data.get('day_low', 'N/A')}")
                        st.write(f"**52W High:** ₹{nse_data.get('week_high', 'N/A')}")
                        st.write(f"**52W Low:** ₹{nse_data.get('week_low', 'N/A')}")
                    else:
                        st.warning(f"NSE unavailable: {nse_data.get('error', '')}")

                with c2:
                    st.subheader("🏛️ BSE")
                    if bse_data and "error" not in bse_data:
                        st.metric("Price", f"₹{bse_data.get('last_price', 'N/A')}", f"{bse_data.get('pChange', '')}%")
                        st.write(f"**BSE Code:** {bse_data.get('scrip_code', 'N/A')}")
                        st.write(f"**Company:** {bse_data.get('company_name', 'N/A')}")
                        st.write(f"**Open:** ₹{bse_data.get('open', 'N/A')}")
                        st.write(f"**Day High:** ₹{bse_data.get('day_high', 'N/A')}")
                        st.write(f"**Day Low:** ₹{bse_data.get('day_low', 'N/A')}")
                        st.write(f"**Prev Close:** ₹{bse_data.get('prev_close', 'N/A')}")
                    else:
                        st.warning(f"BSE unavailable: {bse_data.get('error', '')}")

                with c3:
                    st.subheader("📊 Screener.in")
                    if screener_data and "error" not in screener_data:
                        for k, v in list(screener_data.get("ratios", {}).items())[:7]:
                            st.write(f"**{k}:** {v}")
                        if screener_data.get("url"):
                            st.markdown(f"[🔗 View on Screener]({screener_data['url']})")
                    else:
                        st.warning("Screener unavailable")

                if screener_data and "error" not in screener_data:
                    st.divider()
                    pc1, pc2 = st.columns(2)
                    with pc1:
                        st.subheader("✅ Pros")
                        for p in screener_data.get("pros", []) or ["None listed"]:
                            st.write(f"• {p}")
                    with pc2:
                        st.subheader("❌ Cons")
                        for c in screener_data.get("cons", []) or ["None listed"]:
                            st.write(f"• {c}")

                st.divider()
                st.subheader("🗞️ Latest News")
                news = get_stock_news(stock)
                if news:
                    for n in news:
                        st.write(f"• {n['title']}")
                        if n.get("date"):
                            st.caption(n["date"])
                else:
                    st.info("No recent news found")
            else:
                st.info(f"Click **Check Now — {stock}** above to fetch live data from NSE, BSE & Screener.in")


with main_tab2:
    st.subheader("🔍 NSE Promoter Buying Scanner")
    st.caption("Scans NSE bulk deals and corporate announcements for promoter buying activity")
    col1, col2 = st.columns(2)
    with col1:
        st.info("""
        **What this scanner detects:**
        - 📢 Promoter / Director / Founder buying shares
        - 📋 NSE bulk deals with insider activity
        - 🏢 Corporate announcements about acquisitions
        - 🔔 Sends Telegram alert instantly when found
        """)
    with col2:
        st.info("""
        **How to use:**
        1. Click **Start Scanner** in sidebar
        2. Runs every 1 hour automatically
        3. Get instant Telegram notification
        4. Or click **Scan Now** below
        """)
    if st.button("🔍 Scan Now for Promoter Buying", use_container_width=True):
        with st.spinner("Scanning NSE..."):
            findings = run_market_scanner()
        if findings:
            st.success(f"✅ Found {len(findings)} alerts! Telegram notifications sent.")
            for f in findings:
                st.write(f"• {f}")
        else:
            st.info("No new promoter buying detected right now. Try during market hours.")
    if st.session_state.sent_promoter_alerts:
        st.divider()
        st.subheader("📋 Promoter Alerts This Session")
        for alert in list(st.session_state.sent_promoter_alerts)[-20:]:
            st.write(f"• {alert}")

if st.session_state.alerts_log:
    st.divider()
    st.subheader("📋 Recent Telegram Alerts Sent")
    for log in reversed(st.session_state.alerts_log[-10:]):
        st.write(log)
