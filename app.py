import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# =========================================================
# DAILY COIN + STOCK / FOREX / COMMODITY PICKER
# Editor only writes ignored names and clicks Generate.
# =========================================================

ALPHA_API_KEY = "CL7CLVX9TY4D516K"
CMC_API_KEY = "7cec832d4dec4441a6e09a4aab94e815"

st.set_page_config(page_title="Daily Coin + Market Picker", page_icon="📈", layout="wide")

DEFAULT_CRYPTO_IGNORE = """ONDO, PEAQ, HYPE, AKT, TRUMP, OPG, USELESS, SAHARA, PRL, OSMO, GIGA, AERO, PIEVERSE, MON, ZEC"""
DEFAULT_ASSET_IGNORE = """TDIC, BZFD, CREG, CPHI, EZGO, GBPUSD, EURUSD, AUDGBP, COPPER, WHEAT"""

MIN_VOLUME_USD = 1_000_000
MIN_MARKET_CAP = 5_000_000

MAJOR_CRYPTO_IGNORE = {
    "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOT", "MATIC", "LTC", "TRX",
    "DOGE", "SHIB", "AVAX", "LINK", "UNI", "ATOM", "FIL", "ICP", "APT", "SUI",
    "NEAR", "TON", "OP", "ARB", "INJ", "HBAR", "VET", "ALGO", "ETC", "BCH",
    "PEPE", "FLOKI", "BONK", "WIF", "RENDER", "FET", "GRT", "SAND", "MANA",
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "USDD", "GUSD", "FRAX",
    "FDUSD", "PYUSD", "CRVUSD", "USDE", "EURC", "EURT", "LUSD",
    "WBTC", "WETH", "STETH", "CBETH", "RETH", "WBNB", "WMATIC",
    "PAXG", "XAUT", "LEO", "OKB", "HT", "KCS", "MX"
}

FOREX_LIST = [
    ("AUDGBP=X", "Australian Dollar / British Pound", "AUD/GBP", "AUDGBP", "Currency"),
    ("GBPJPY=X", "British Pound / Japanese Yen", "GBP/JPY", "GBPJPY", "Currency"),
    ("EURUSD=X", "Euro / US Dollar", "EUR/USD", "EURUSD", "Currency"),
    ("GBPUSD=X", "British Pound / US Dollar", "GBP/USD", "GBPUSD", "Currency"),
    ("USDJPY=X", "US Dollar / Japanese Yen", "USD/JPY", "USDJPY", "Currency"),
    ("EURJPY=X", "Euro / Japanese Yen", "EUR/JPY", "EURJPY", "Currency"),
    ("AUDJPY=X", "Australian Dollar / Japanese Yen", "AUD/JPY", "AUDJPY", "Currency"),
    ("NZDJPY=X", "New Zealand Dollar / Japanese Yen", "NZD/JPY", "NZDJPY", "Currency"),
    ("USDCAD=X", "US Dollar / Canadian Dollar", "USD/CAD", "USDCAD", "Currency"),
    ("USDCHF=X", "US Dollar / Swiss Franc", "USD/CHF", "USDCHF", "Currency"),
    ("GC=F", "Gold", "Gold", "XAUUSD", "Metal"),
    ("SI=F", "Silver", "Silver", "XAGUSD", "Metal"),
    ("HG=F", "Copper", "Copper", "COPPER", "Metal"),
    ("CL=F", "Crude Oil WTI", "Crude Oil WTI", "USOIL", "Commodity"),
    ("BZ=F", "Brent Oil", "Brent Oil", "UKOIL", "Commodity"),
    ("NG=F", "Natural Gas", "Natural Gas", "NATGAS", "Commodity"),
    ("ZC=F", "Corn", "Corn", "CORN", "Commodity"),
    ("ZS=F", "Soybeans", "Soybeans", "SOYBEANS", "Commodity"),
    ("ZW=F", "Wheat", "Wheat", "WHEAT", "Commodity"),
    ("KC=F", "Coffee", "Coffee", "COFFEE", "Commodity"),
    ("CC=F", "Cocoa", "Cocoa", "COCOA", "Commodity"),
    ("SB=F", "Sugar", "Sugar", "SUGAR", "Commodity"),
    ("CT=F", "Cotton", "Cotton", "COTTON", "Commodity"),
]

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
    "https://bitcoinmagazine.com/.rss/full/",
    "https://cryptopotato.com/feed/",
]

# ------------------------- Helpers -------------------------

def parse_ignore(text: str) -> set[str]:
    items = re.split(r"[,\n]+", text or "")
    cleaned = set()
    for x in items:
        x = x.strip().upper().replace("/", "").replace(" ", "")
        if x:
            cleaned.add(x)
    return cleaned


def normalize_symbol(x) -> str:
    return str(x or "").upper().replace("/", "").replace("=X", "").replace("=F", "").strip()


def safe_float(x, default=0.0):
    try:
        if x is None:
            return default
        return float(str(x).replace("%", "").replace(",", ""))
    except Exception:
        return default


def safe_int(x, default=0):
    try:
        return int(float(str(x).replace(",", "")))
    except Exception:
        return default


def safe_get_json(url, headers=None, params=None, timeout=30):
    try:
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        return r.json(), None
    except Exception as e:
        return None, str(e)


def empty_stock_df():
    return pd.DataFrame(columns=[
        "ticker", "full_name", "short_name", "tradingview_search", "asset_type", "price",
        "change_%", "volume", "source", "attention_score", "reason"
    ])


def empty_crypto_df():
    return pd.DataFrame(columns=[
        "Symbol", "Name", "Price_USD", "Change_24h_%", "Activity_Ratio_%", "Market_Cap",
        "Volume_24h", "news_mentions", "Total_Master_Score", "Video_Priority", "Content_Type"
    ])

# ------------------------- Crypto -------------------------

@st.cache_data(ttl=600, show_spinner=False)
def fetch_cmc(ignore_tuple):
    ignore = set(ignore_tuple)
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": CMC_API_KEY}
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
    params = {"limit": 500, "convert": "USD"}
    data, err = safe_get_json(url, headers=headers, params=params)
    if err or not data or "data" not in data:
        return pd.DataFrame(), f"CoinMarketCap error: {err or data.get('status', {}).get('error_message', 'unknown')}"

    rows = []
    for coin in data["data"]:
        symbol = normalize_symbol(coin.get("symbol"))
        if not symbol or symbol in ignore:
            continue
        q = coin.get("quote", {}).get("USD", {})
        volume = safe_float(q.get("volume_24h"))
        mcap = safe_float(q.get("market_cap"))
        change = safe_float(q.get("percent_change_24h"))
        price = safe_float(q.get("price"))
        if volume < MIN_VOLUME_USD or mcap < MIN_MARKET_CAP:
            continue
        rows.append({
            "symbol": symbol,
            "name": coin.get("name", ""),
            "price_usd": price,
            "market_cap": mcap,
            "volume_24h": volume,
            "change_24h": change,
            "vol_mcap_pct": round((volume / mcap) * 100, 2) if mcap else 0,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df, None
    df["cmc_most_visited_rank"] = (df["volume_24h"] * df["change_24h"].abs()).rank(method="dense", ascending=False)
    df["cmc_trending_rank"] = df["change_24h"].abs().rank(method="dense", ascending=False)
    df.loc[df["cmc_most_visited_rank"] > 30, "cmc_most_visited_rank"] = 0
    df.loc[df["cmc_trending_rank"] > 30, "cmc_trending_rank"] = 0
    return df, None


@st.cache_data(ttl=600, show_spinner=False)
def fetch_coingecko(ignore_tuple):
    ignore = set(ignore_tuple)
    base = "https://api.coingecko.com/api/v3"
    headers = {"User-Agent": "Mozilla/5.0"}
    trending = {}

    trend, _ = safe_get_json(f"{base}/search/trending", headers=headers)
    if trend and isinstance(trend.get("coins"), list):
        for i, coin in enumerate(trend["coins"]):
            sym = normalize_symbol(coin.get("item", {}).get("symbol"))
            if sym:
                trending[sym] = i + 1

    rows = []
    for page in [1, 2]:
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 250,
            "page": page,
            "sparkline": "false",
            "price_change_percentage": "24h",
        }
        data, err = safe_get_json(f"{base}/coins/markets", headers=headers, params=params)
        if err or not isinstance(data, list):
            continue
        for coin in data:
            symbol = normalize_symbol(coin.get("symbol"))
            if not symbol or symbol in ignore:
                continue
            volume = safe_float(coin.get("total_volume"))
            mcap = safe_float(coin.get("market_cap"))
            change = safe_float(coin.get("price_change_percentage_24h"))
            price = safe_float(coin.get("current_price"))
            if volume < MIN_VOLUME_USD or mcap < MIN_MARKET_CAP:
                continue
            rows.append({
                "symbol": symbol,
                "name": coin.get("name", ""),
                "price_usd": price,
                "market_cap": mcap,
                "volume_24h": volume,
                "change_24h": change,
                "vol_mcap_pct": round((volume / mcap) * 100, 2) if mcap else 0,
                "cg_trending_rank": trending.get(symbol, 0),
                "cg_is_popular": "Yes" if trending.get(symbol, 0) else "No",
            })
        time.sleep(0.3)
    return pd.DataFrame(rows), None


@st.cache_data(ttl=900, show_spinner=False)
def fetch_news_titles():
    titles = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for url in RSS_FEEDS:
        try:
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            titles.extend([i.text.strip() for i in root.findall(".//item/title") if i.text])
        except Exception:
            pass
    return titles


def build_crypto(ignore_text: str, top_n: int, include_major_ignore: bool = True):
    ignore = parse_ignore(ignore_text)
    if include_major_ignore:
        ignore = ignore | MAJOR_CRYPTO_IGNORE

    cmc, cmc_err = fetch_cmc(tuple(sorted(ignore)))
    cg, cg_err = fetch_coingecko(tuple(sorted(ignore)))

    if cmc.empty and cg.empty:
        return empty_crypto_df(), [x for x in [cmc_err, cg_err] if x]

    df = pd.merge(cmc, cg, on="symbol", how="outer", suffixes=("_cmc", "_cg"))
    df["symbol"] = df["symbol"].astype(str).str.upper()
    df = df[~df["symbol"].isin(ignore)]

    for col in ["name", "price_usd", "market_cap", "volume_24h", "change_24h", "vol_mcap_pct"]:
        left = f"{col}_cmc"
        right = f"{col}_cg"
        if left in df.columns and right in df.columns:
            df[col] = df[left].combine_first(df[right])
        elif left in df.columns:
            df[col] = df[left]
        elif right in df.columns:
            df[col] = df[right]
        else:
            df[col] = "" if col == "name" else 0

    for col in ["cmc_most_visited_rank", "cmc_trending_rank", "cg_trending_rank"]:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    if "cg_is_popular" not in df.columns:
        df["cg_is_popular"] = "No"
    df["cg_is_popular"] = df["cg_is_popular"].fillna("No")

    titles = fetch_news_titles()
    def count_mentions(symbol):
        return min(sum(bool(re.search(rf"\b{re.escape(symbol)}\b", t, re.I)) for t in titles), 3)

    df["news_mentions"] = df["symbol"].apply(count_mentions)
    df["Directional_Points"] = df["change_24h"].abs().apply(lambda x: 4 if x >= 15 else 3 if x >= 8 else 2 if x >= 5 else 0)
    df["Activity_Bonus"] = df["vol_mcap_pct"].apply(lambda x: 4 if x >= 80 else 3 if x >= 50 else 2 if x >= 25 else 0)
    df["Viral_Points"] = df.apply(
        lambda r: (5 if r["cmc_most_visited_rank"] > 0 else 0)
        + (4 if r["cmc_trending_rank"] > 0 else 0)
        + (5 if r["cg_trending_rank"] > 0 else 0)
        + (3 if r["cg_is_popular"] == "Yes" else 0),
        axis=1,
    )
    df["News_Points"] = df["news_mentions"].astype(int)
    df["Total_Master_Score"] = df["Directional_Points"] + df["Activity_Bonus"] + df["Viral_Points"] + df["News_Points"]
    df["Video_Priority"] = df["Total_Master_Score"].apply(
        lambda s: "🔥 ULTRA VIRAL" if s >= 15 else "🚀 HIGH INTEREST" if s >= 10 else "🟡 TRENDING" if s >= 5 else "💤 SKIP"
    )
    df["Content_Type"] = df["change_24h"].apply(lambda c: "PUMP ALERT" if c >= 0 else "CRASH ALERT")

    out = df.rename(columns={
        "symbol": "Symbol",
        "name": "Name",
        "price_usd": "Price_USD",
        "change_24h": "Change_24h_%",
        "vol_mcap_pct": "Activity_Ratio_%",
        "market_cap": "Market_Cap",
        "volume_24h": "Volume_24h",
    })
    cols = ["Symbol", "Name", "Price_USD", "Change_24h_%", "Activity_Ratio_%", "Market_Cap", "Volume_24h", "news_mentions", "Total_Master_Score", "Video_Priority", "Content_Type"]
    for c in cols:
        if c not in out.columns:
            out[c] = None
    out = out[cols].sort_values(["Total_Master_Score", "Change_24h_%"], ascending=[False, False]).head(int(top_n)).reset_index(drop=True)
    return out, [x for x in [cmc_err, cg_err] if x]


def format_crypto_text(df: pd.DataFrame) -> str:
    if df.empty:
        return "No crypto picks found."
    lines = ["FINAL CRYPTO PICKS", "=" * 30]
    for i, r in df.iterrows():
        lines.append(f"{i+1}. {r['Name']} ({r['Symbol']})")
        lines.append(f"Price: {r['Price_USD']:.8g} | 24h Move: {r['Change_24h_%']:.2f}% | Score: {r['Total_Master_Score']}")
        lines.append(f"Priority: {r['Video_Priority']} | Type: {r['Content_Type']}")
        lines.append("")
    return "\n".join(lines).strip()

# ------------------------- Stocks / Forex -------------------------

@st.cache_data(ttl=600, show_spinner=False)
def get_company_name(ticker: str) -> str:
    try:
        info = yf.Ticker(ticker).get_info()
        return info.get("longName") or info.get("shortName") or ticker
    except Exception:
        return ticker


@st.cache_data(ttl=600, show_spinner=False)
def fetch_stocks():
    url = "https://www.alphavantage.co/query"
    params = {"function": "TOP_GAINERS_LOSERS", "apikey": ALPHA_API_KEY}
    data, err = safe_get_json(url, params=params)
    if err or not isinstance(data, dict):
        return empty_stock_df(), f"Alpha Vantage error: {err or 'unknown'}"
    if "Information" in data or "Note" in data:
        return empty_stock_df(), data.get("Information") or data.get("Note")

    rows = []
    section_map = {
        "top_gainers": "top gainer",
        "top_losers": "top loser",
        "most_actively_traded": "most active",
    }
    for section, label in section_map.items():
        for item in data.get(section, []) or []:
            ticker = normalize_symbol(item.get("ticker"))
            if not ticker:
                continue
            rows.append({
                "ticker": ticker,
                "price": safe_float(item.get("price")),
                "change_%": safe_float(item.get("change_percentage")),
                "volume": safe_int(item.get("volume")),
                "source": section,
                "source_label": label,
            })

    df = pd.DataFrame(rows)
    if df.empty:
        return empty_stock_df(), "No stock data returned from Alpha Vantage."

    df = df[~df["ticker"].str.contains(r"\+|/|\^|\.", regex=True, na=False)]
    df = df[~df["ticker"].str.endswith(("W", "WS", "WT", "WW", "R", "U"), na=False)]
    df = df.drop_duplicates("ticker")
    df = df[(df["price"] > 0) & (df["change_%"].notna())]

    if df.empty:
        return empty_stock_df(), "All stock rows were filtered."

    df["is_penny_under_1"] = df["price"] < 1
    df["asset_type"] = df["is_penny_under_1"].map({True: "USA Penny Stock Under $1", False: "USA Big Stock"})
    df["full_name"] = df["ticker"].apply(get_company_name)
    df["short_name"] = ""
    df["tradingview_search"] = df["ticker"]
    df["abs_change_%"] = df["change_%"].abs()
    df["movement_score"] = df["abs_change_%"].rank(pct=True) * 100
    df["volume_score"] = df["volume"].rank(pct=True) * 100
    df["penny_bonus"] = df["is_penny_under_1"].map({True: 100, False: 0})
    df["source_bonus"] = df["source"].map({"top_gainers": 100, "top_losers": 90, "most_actively_traded": 75}).fillna(50)
    df["attention_score"] = df["movement_score"] * 0.40 + df["volume_score"] * 0.25 + df["penny_bonus"] * 0.20 + df["source_bonus"] * 0.15

    def make_reason(r):
        parts = ["under $1 penny stock" if r["is_penny_under_1"] else "big USA stock"]
        if abs(r["change_%"]) >= 20:
            parts.append("big price movement")
        elif abs(r["change_%"]) >= 8:
            parts.append("strong price movement")
        if r["volume"] >= 10_000_000:
            parts.append("high volume")
        parts.append(r.get("source_label", "market mover"))
        return " + ".join(parts)

    df["reason"] = df.apply(make_reason, axis=1)
    return df.sort_values("attention_score", ascending=False).reset_index(drop=True), None


@st.cache_data(ttl=600, show_spinner=False)
def fetch_forex():
    rows = []
    for ticker, name, short, tv, group in FOREX_LIST:
        try:
            hist = yf.Ticker(ticker).history(period="5d", interval="1d")
            if hist.empty or len(hist) < 2:
                continue
            last = safe_float(hist["Close"].iloc[-1])
            prev = safe_float(hist["Close"].iloc[-2])
            if prev == 0:
                continue
            change = ((last - prev) / prev) * 100
            vol = safe_int(hist["Volume"].iloc[-1]) if "Volume" in hist.columns else 0
            rows.append({
                "ticker": ticker,
                "full_name": name,
                "short_name": short,
                "tradingview_search": tv,
                "asset_type": group,
                "price": round(last, 4),
                "change_%": round(change, 2),
                "volume": vol,
                "source": "yfinance",
            })
        except Exception:
            continue

    df = pd.DataFrame(rows)
    if df.empty:
        return empty_stock_df(), "No forex/commodity data returned from yfinance."

    df["abs_change_%"] = df["change_%"].abs()
    df["movement_score"] = df["abs_change_%"].rank(pct=True) * 100
    df["volume_score"] = df["volume"].rank(pct=True) * 100
    df["asset_bonus"] = df["asset_type"].map({"Metal": 100, "Commodity": 90, "Currency": 70}).fillna(50)
    df["attention_score"] = df["movement_score"] * 0.65 + df["volume_score"] * 0.15 + df["asset_bonus"] * 0.20
    df["reason"] = df.apply(
        lambda r: f"{str(r['asset_type']).lower()} + "
        + ("strong daily movement" if abs(r["change_%"]) >= 1 else "moderate daily movement" if abs(r["change_%"]) >= 0.5 else "market movement"),
        axis=1,
    )
    return df.sort_values("attention_score", ascending=False).reset_index(drop=True), None


def apply_ignore_filter(df: pd.DataFrame, ignore: set[str]) -> pd.DataFrame:
    if df is None or df.empty or not ignore:
        return df if df is not None else pd.DataFrame()
    mask = pd.Series(True, index=df.index)
    for col in ["ticker", "full_name", "short_name", "tradingview_search", "asset_type"]:
        if col in df.columns:
            normalized = df[col].astype(str).apply(normalize_symbol)
            mask &= ~normalized.isin(ignore)
    return df[mask].copy()


def top_with_fallback(df: pd.DataFrame, kind: str, n: int, already_tickers: set[str] | None = None):
    if df is None or df.empty:
        return empty_stock_df()
    already_tickers = already_tickers or set()
    if "asset_type" not in df.columns or "ticker" not in df.columns:
        return empty_stock_df()
    primary = df[(df["asset_type"] == kind) & (~df["ticker"].isin(already_tickers))].head(n)
    if len(primary) < n:
        fill = df[(~df["ticker"].isin(set(primary.get("ticker", [])) | already_tickers))].head(n - len(primary))
        primary = pd.concat([primary, fill], ignore_index=True)
    return primary.head(n)


def build_assets(ignore_text: str):
    ignore = parse_ignore(ignore_text)
    stocks, stock_err = fetch_stocks()
    forex, forex_err = fetch_forex()

    stocks = apply_ignore_filter(stocks, ignore)
    forex = apply_ignore_filter(forex, ignore)

    if stocks is None or stocks.empty:
        stocks = empty_stock_df()
    if forex is None or forex.empty:
        forex = empty_stock_df()

    big = top_with_fallback(stocks, "USA Big Stock", 2)
    used = set(big.get("ticker", []))
    penny = top_with_fallback(stocks, "USA Penny Stock Under $1", 3, used)

    currencies = top_with_fallback(forex, "Currency", 3)
    metal = top_with_fallback(forex, "Metal", 1, set(currencies.get("ticker", [])))
    commodity = top_with_fallback(forex, "Commodity", 1, set(currencies.get("ticker", [])) | set(metal.get("ticker", [])))

    out = pd.concat([big, penny, currencies, metal, commodity], ignore_index=True)
    if not out.empty:
        out.insert(0, "date", datetime.now().strftime("%Y-%m-%d"))
        preferred = ["date", "asset_type", "ticker", "full_name", "short_name", "tradingview_search", "price", "change_%", "volume", "attention_score", "reason"]
        out = out[[c for c in preferred if c in out.columns]]
    return out, [x for x in [stock_err, forex_err] if x]


def format_assets_text(df: pd.DataFrame) -> str:
    if df.empty:
        return "No assets found."
    sections = [
        ("USA BIG STOCKS — 2", "USA Big Stock"),
        ("USA PENNY STOCKS UNDER $1 — 3", "USA Penny Stock Under $1"),
        ("CURRENCIES — 3", "Currency"),
        ("METAL — 1", "Metal"),
        ("COMMODITY — 1", "Commodity"),
    ]
    lines = ["FINAL 10 ATTENTION ASSETS", "=" * 30]
    for title, kind in sections:
        part = df[df["asset_type"] == kind] if "asset_type" in df.columns else pd.DataFrame()
        lines += ["", f"*{title}*", "-" * 30]
        if part.empty:
            lines.append("No pick found.")
            continue
        for _, r in part.iterrows():
            name = r.get("full_name", r.get("ticker", ""))
            if kind == "Currency" and r.get("short_name", ""):
                name = f"{name} ({r.get('short_name')})"
            lines.append(f"*{name}*")
            lines.append(f"TradingView: {r.get('tradingview_search', r.get('ticker', ''))} | Price: {r.get('price', '')} | Move: {r.get('change_%', '')}%")
            lines.append(f"Reason: {r.get('reason', '')}")
            lines.append("")
    return "\n".join(lines).strip()

# ------------------------- UI -------------------------

st.title("Daily Coin + Stock / Forex Picker")
st.caption("Editor only writes ignored names, then clicks Generate. Public app link can be shared with editor.")

with st.sidebar:
    st.header("Settings")
    include_major_ignore = st.checkbox("Auto-ignore major/stable coins", value=True)
    top_n = st.number_input("How many crypto picks?", min_value=5, max_value=100, value=20, step=1)
    st.divider()
    st.caption("Refresh data if app is showing old result.")
    if st.button("Clear cache / refresh data"):
        st.cache_data.clear()
        st.success("Cache cleared. Click Generate again.")

col1, col2 = st.columns(2)
with col1:
    crypto_ignore = st.text_area("Ignored coins", DEFAULT_CRYPTO_IGNORE, height=130, help="Write symbols or names separated by comma. Example: BTC, ETH, SOL")
with col2:
    asset_ignore = st.text_area("Ignored stocks / forex / commodities", DEFAULT_ASSET_IGNORE, height=130, help="Example: AAPL, TSLA, GBPUSD, GOLD, WHEAT")

run_crypto = st.checkbox("Generate crypto list", value=True)
run_assets = st.checkbox("Generate stock / forex / commodity list", value=True)

if st.button("Generate Today's List", type="primary"):
    if not run_crypto and not run_assets:
        st.warning("Please select at least one list to generate.")
        st.stop()

    if run_crypto:
        st.subheader("Crypto Picks")
        with st.spinner("Generating crypto picks..."):
            crypto_df, crypto_errors = build_crypto(crypto_ignore, int(top_n), include_major_ignore)
        for e in crypto_errors:
            st.warning(e)
        if crypto_df.empty:
            st.error("No crypto picks found. Try removing some ignored coins or try again later.")
        else:
            st.dataframe(crypto_df, use_container_width=True)
            c1, c2 = st.columns(2)
            with c1:
                st.download_button("Download crypto CSV", crypto_df.to_csv(index=False), f"crypto_picks_{datetime.now().strftime('%Y-%m-%d')}.csv", "text/csv")
            with c2:
                st.text_area("Copy crypto symbols", ", ".join(crypto_df["Symbol"].astype(str).tolist()), height=90)
            st.text_area("Copy crypto WhatsApp format", format_crypto_text(crypto_df), height=350)

    if run_assets:
        st.subheader("Stock / Forex / Commodity Picks")
        with st.spinner("Generating stock / forex / commodity picks..."):
            assets_df, asset_errors = build_assets(asset_ignore)
        for e in asset_errors:
            st.warning(e)
        if assets_df.empty:
            st.error("No stock/forex/commodity picks found. Try again later or check APIs.")
        else:
            st.dataframe(assets_df, use_container_width=True)
            st.download_button("Download assets CSV", assets_df.to_csv(index=False), f"daily_attention_assets_{datetime.now().strftime('%Y-%m-%d')}.csv", "text/csv")
            st.text_area("Copy WhatsApp format", format_assets_text(assets_df), height=450)
else:
    st.info("Write ignored names, then click Generate Today's List.")
