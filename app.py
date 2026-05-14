import streamlit as st
import pandas as pd
import requests, time, re, xml.etree.ElementTree as ET
import yfinance as yf
from datetime import datetime
from io import StringIO

# Your existing API keys from Colab
ALPHA_API_KEY = "CL7CLVX9TY4D516K"
CMC_API_KEY = "7cec832d4dec4441a6e09a4aab94e815"

st.set_page_config(page_title="Daily Coin + Market Picker", layout="wide")
st.title("Daily Coin + Stock / Forex Picker")
st.caption("Editor only writes ignored names, then clicks Generate.")

DEFAULT_CRYPTO_IGNORE = """ONDO, PEAQ, HYPE, AKT, TRUMP, OPG, USELESS, SAHARA, PRL, OSMO, GIGA, AERO, PIEVERSE, MON, ZEC"""
DEFAULT_ASSET_IGNORE = """TDIC, BZFD, CREG, CPHI, EZGO, GBPUSD, EURUSD, AUDGBP, COPPER, WHEAT"""

MAJOR_IGNORE = {
    'BTC','ETH','BNB','SOL','XRP','ADA','DOT','MATIC','LTC','TRX',
    'DOGE','SHIB','AVAX','LINK','UNI','ATOM','FIL','ICP','APT','SUI',
    'NEAR','TON','OP','ARB','INJ','HBAR','VET','ALGO','ETC','BCH',
    'PEPE','FLOKI','BONK','WIF','RENDER','FET','GRT','SAND','MANA',
    'USDT','USDC','BUSD','DAI','TUSD','USDP','USDD','GUSD','FRAX',
    'FDUSD','PYUSD','CRVUSD','USDE','EURC','EURT','LUSD',
    'WBTC','WETH','STETH','CBETH','RETH','WBNB','WMATIC',
    'PAXG','XAUT','LEO','OKB','HT','KCS','MX','NIGHT',
}

MIN_VOLUME_USD = 1_000_000
MIN_MARKET_CAP = 5_000_000


def parse_ignore(text):
    return {x.strip().upper().replace("/", "") for x in re.split(r"[,\n]+", text or "") if x.strip()}


def calc_vol_mcap(volume, market_cap):
    return round((volume / market_cap) * 100, 2) if market_cap else 0


def safe_get(url, **kwargs):
    return requests.get(url, timeout=25, **kwargs)


def fetch_cmc(ignore_coins):
    headers = {'Accepts': 'application/json', 'X-CMC_PRO_API_KEY': CMC_API_KEY}
    url = 'https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest'
    params = {'limit': 500, 'convert': 'USD'}
    r = safe_get(url, headers=headers, params=params)
    resp = r.json()
    if 'data' not in resp:
        st.warning(f"CMC error: {resp.get('status',{}).get('error_message','unknown')}")
        return pd.DataFrame()
    rows = []
    for coin in resp['data']:
        symbol = (coin.get('symbol') or '').upper()
        if symbol in ignore_coins: continue
        q = coin.get('quote', {}).get('USD', {})
        volume = q.get('volume_24h', 0) or 0
        mcap = q.get('market_cap', 0) or 0
        change = q.get('percent_change_24h', 0) or 0
        if volume < MIN_VOLUME_USD or mcap < MIN_MARKET_CAP: continue
        rows.append({
            'symbol': symbol, 'name': coin.get('name',''), 'price_usd': round(q.get('price',0),6),
            'market_cap': round(mcap,2), 'volume_24h': round(volume,2), 'change_24h': round(change,4),
            'vol_mcap_pct': calc_vol_mcap(volume,mcap), 'cmc_gainer': change > 5, 'cmc_loser': change < -5
        })
    df = pd.DataFrame(rows)
    if df.empty: return df
    df['cmc_trending_rank'] = df['change_24h'].abs().rank(method='dense', ascending=False)
    df['cmc_most_visited_rank'] = (df['volume_24h'] * df['change_24h'].abs()).rank(method='dense', ascending=False)
    df.loc[df['cmc_trending_rank'] > 30, 'cmc_trending_rank'] = 0
    df.loc[df['cmc_most_visited_rank'] > 30, 'cmc_most_visited_rank'] = 0
    return df


def cg_get(url, params=None):
    try:
        r = safe_get(url, params=params, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code != 200: return None
        return r.json()
    except Exception:
        return None


def fetch_coingecko(ignore_coins):
    base = 'https://api.coingecko.com/api/v3'
    trending = {}
    data = cg_get(f"{base}/search/trending")
    if data and 'coins' in data:
        for i, coin in enumerate(data['coins']):
            trending[(coin['item'].get('symbol') or '').upper()] = i + 1
    rows = []
    for page in range(1,3):
        data = cg_get(f'{base}/coins/markets', params={
            'vs_currency':'usd','order':'market_cap_desc','per_page':250,'page':page,
            'sparkline':False,'price_change_percentage':'24h'
        })
        if not isinstance(data, list): continue
        for coin in data:
            symbol = (coin.get('symbol') or '').upper()
            if symbol in ignore_coins: continue
            volume = coin.get('total_volume',0) or 0
            mcap = coin.get('market_cap',0) or 0
            change = coin.get('price_change_percentage_24h',0) or 0
            if volume < MIN_VOLUME_USD or mcap < MIN_MARKET_CAP: continue
            rank = trending.get(symbol,0)
            rows.append({
                'symbol': symbol, 'name': coin.get('name',''), 'price_usd': round(coin.get('current_price',0),6),
                'market_cap': round(mcap,2), 'volume_24h': round(volume,2), 'change_24h': round(change,4),
                'vol_mcap_pct': calc_vol_mcap(volume,mcap), 'cg_trending_rank': rank, 'cg_is_popular': 'Yes' if rank else 'No'
            })
        time.sleep(1)
    return pd.DataFrame(rows)

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
    "https://bitcoinmagazine.com/.rss/full/",
    "https://cryptopotato.com/feed/",
]

def fetch_news_mentions(coins):
    titles = []
    for url in RSS_FEEDS:
        try:
            r = safe_get(url, headers={"User-Agent":"Mozilla/5.0"})
            if r.status_code != 200: continue
            root = ET.fromstring(r.content)
            titles.extend([i.text.strip() for i in root.findall('.//item/title') if i.text])
        except Exception:
            pass
    rows = []
    for coin in coins:
        total = sum(bool(re.search(rf"\b{re.escape(coin)}\b", title, re.I)) for title in titles)
        if total: rows.append({'symbol': coin, 'news_mentions': min(total,3)})
    return pd.DataFrame(rows)


def build_crypto(ignore_text, top_n):
    ignore = MAJOR_IGNORE | parse_ignore(ignore_text)
    cmc = fetch_cmc(ignore)
    cg = fetch_coingecko(ignore)
    if cmc.empty and cg.empty: return pd.DataFrame()
    df = pd.merge(cmc, cg, on='symbol', how='outer', suffixes=('_cmc','_cg'))
    df['symbol'] = df['symbol'].astype(str).str.upper()
    df['name'] = df.get('name_cmc', pd.Series(index=df.index)).combine_first(df.get('name_cg', pd.Series(index=df.index)))
    for col in ['price_usd','market_cap','volume_24h','change_24h','vol_mcap_pct']:
        df[col] = df.get(f'{col}_cmc', pd.Series(index=df.index)).combine_first(df.get(f'{col}_cg', pd.Series(index=df.index)))
    df['cmc_most_visited_rank'] = df.get('cmc_most_visited_rank', 0).fillna(0)
    df['cmc_trending_rank'] = df.get('cmc_trending_rank', 0).fillna(0)
    df['cg_trending_rank'] = df.get('cg_trending_rank', 0).fillna(0)
    df['cg_is_popular'] = df.get('cg_is_popular', 'No').fillna('No')
    news = fetch_news_mentions(set(df['symbol']) - ignore)
    df = df.merge(news, how='left', on='symbol') if not news.empty else df.assign(news_mentions=0)
    df['news_mentions'] = df['news_mentions'].fillna(0)
    df['Directional_Points'] = df['change_24h'].apply(lambda x: 2 if abs(x) > 5 else 0)
    df['Activity_Bonus'] = df['vol_mcap_pct'].apply(lambda x: 3 if x > 50 else 0)
    df['Viral_Points'] = df.apply(lambda r: (5 if r['cmc_most_visited_rank']>0 else 0) + (5 if r['cg_trending_rank']>0 else 0) + (4 if r['cg_is_popular']=='Yes' else 0), axis=1)
    df['News_Points'] = df['news_mentions'].astype(int)
    df['Total_Master_Score'] = df['Directional_Points'] + df['Activity_Bonus'] + df['Viral_Points'] + df['News_Points']
    df['Video_Priority'] = df['Total_Master_Score'].apply(lambda s: '🔥 ULTRA VIRAL' if s>=15 else '🚀 HIGH INTEREST' if s>=10 else '🟡 TRENDING' if s>=5 else '💤 SKIP')
    df['Content_Type'] = df['change_24h'].apply(lambda c: 'PUMP ALERT' if c > 0 else 'CRASH ALERT')
    out = df.rename(columns={'symbol':'Symbol','name':'Name','price_usd':'Price_USD','change_24h':'Change_24h_%','vol_mcap_pct':'Activity_Ratio_%','market_cap':'Market_Cap'})
    cols = ['Symbol','Name','Price_USD','Change_24h_%','Activity_Ratio_%','Market_Cap','news_mentions','Total_Master_Score','Video_Priority','Content_Type']
    return out[cols].sort_values('Total_Master_Score', ascending=False).head(top_n).reset_index(drop=True)


def get_company_name(ticker):
    try:
        info = yf.Ticker(ticker).get_info()
        return info.get('longName') or info.get('shortName') or ticker
    except Exception:
        return ticker


def fetch_stocks():
    url = f"https://www.alphavantage.co/query?function=TOP_GAINERS_LOSERS&apikey={ALPHA_API_KEY}"
    data = safe_get(url).json()
    rows = []
    for section in ['top_gainers','top_losers','most_actively_traded']:
        for item in data.get(section, []):
            rows.append({'ticker':item.get('ticker'), 'price':item.get('price'), 'change_percentage':item.get('change_percentage'), 'volume':item.get('volume'), 'source':section})
    df = pd.DataFrame(rows)
    if df.empty: return df
    df['ticker'] = df['ticker'].astype(str).str.upper().str.strip()
    df['price'] = pd.to_numeric(df['price'], errors='coerce')
    df['change_%'] = pd.to_numeric(df['change_percentage'].astype(str).str.replace('%','', regex=False), errors='coerce')
    df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
    df = df[~df['ticker'].str.contains(r"\+|/|\^", regex=True)]
    df = df[~df['ticker'].str.endswith(('W','WS','WT','WW','R','U'))]
    df = df.dropna(subset=['ticker','price','change_%']).drop_duplicates('ticker')
    df['is_penny_under_1'] = df['price'] < 1
    df['asset_type'] = df['is_penny_under_1'].apply(lambda x: 'USA Penny Stock Under $1' if x else 'USA Big Stock')
    df['full_name'] = df['ticker'].apply(get_company_name)
    df['tradingview_search'] = df['ticker']
    df['abs_change_%'] = df['change_%'].abs()
    df['movement_score'] = df['abs_change_%'].rank(pct=True) * 100
    df['volume_score'] = df['volume'].rank(pct=True) * 100
    df['penny_bonus'] = df['is_penny_under_1'].apply(lambda x: 100 if x else 0)
    df['source_bonus'] = df['source'].map({'top_gainers':100,'top_losers':90,'most_actively_traded':75}).fillna(50)
    df['attention_score'] = df['movement_score']*.40 + df['volume_score']*.25 + df['penny_bonus']*.20 + df['source_bonus']*.15
    def reason(r):
        parts = ['under $1 penny stock' if r['is_penny_under_1'] else 'big USA stock']
        if abs(r['change_%']) >= 20: parts.append('big price movement')
        if r['volume'] >= 10_000_000: parts.append('high volume')
        parts.append({'top_gainers':'top gainer','top_losers':'top loser','most_actively_traded':'most active'}.get(r['source'],'market mover'))
        return ' + '.join(parts)
    df['reason'] = df.apply(reason, axis=1)
    return df.sort_values('attention_score', ascending=False)

FOREX_LIST = [
    ('AUDGBP=X','Australian Dollar / British Pound','AUD/GBP','AUDGBP','Currency'),('GBPJPY=X','British Pound / Japanese Yen','GBP/JPY','GBPJPY','Currency'),
    ('EURUSD=X','Euro / US Dollar','EUR/USD','EURUSD','Currency'),('GBPUSD=X','British Pound / US Dollar','GBP/USD','GBPUSD','Currency'),
    ('USDJPY=X','US Dollar / Japanese Yen','USD/JPY','USDJPY','Currency'),('EURJPY=X','Euro / Japanese Yen','EUR/JPY','EURJPY','Currency'),
    ('AUDJPY=X','Australian Dollar / Japanese Yen','AUD/JPY','AUDJPY','Currency'),('NZDJPY=X','New Zealand Dollar / Japanese Yen','NZD/JPY','NZDJPY','Currency'),
    ('USDCAD=X','US Dollar / Canadian Dollar','USD/CAD','USDCAD','Currency'),('USDCHF=X','US Dollar / Swiss Franc','USD/CHF','USDCHF','Currency'),
    ('GC=F','Gold','Gold','XAUUSD','Metal'),('SI=F','Silver','Silver','XAGUSD','Metal'),('HG=F','Copper','Copper','COPPER','Metal'),
    ('CL=F','Crude Oil WTI','Crude Oil WTI','USOIL','Commodity'),('BZ=F','Brent Oil','Brent Oil','UKOIL','Commodity'),('NG=F','Natural Gas','Natural Gas','NATGAS','Commodity'),
    ('ZC=F','Corn','Corn','CORN','Commodity'),('ZS=F','Soybeans','Soybeans','SOYBEANS','Commodity'),('ZW=F','Wheat','Wheat','WHEAT','Commodity'),
    ('KC=F','Coffee','Coffee','COFFEE','Commodity'),('CC=F','Cocoa','Cocoa','COCOA','Commodity'),('SB=F','Sugar','Sugar','SUGAR','Commodity'),('CT=F','Cotton','Cotton','COTTON','Commodity')]


def fetch_forex():
    rows=[]
    for ticker, name, short, tv, group in FOREX_LIST:
        try:
            hist = yf.Ticker(ticker).history(period='5d')
            if hist.empty or len(hist)<2: continue
            last, prev = hist['Close'].iloc[-1], hist['Close'].iloc[-2]
            change = ((last-prev)/prev)*100
            vol = hist['Volume'].iloc[-1] if 'Volume' in hist.columns else 0
            rows.append({'ticker':ticker,'full_name':name,'short_name':short,'tradingview_search':tv,'asset_type':group,'price':round(last,4),'change_%':round(change,2),'volume':int(vol),'source':'yfinance_movement'})
        except Exception: pass
    df = pd.DataFrame(rows)
    if df.empty: return df
    df['abs_change_%'] = df['change_%'].abs()
    df['movement_score'] = df['abs_change_%'].rank(pct=True)*100
    df['volume_score'] = df['volume'].rank(pct=True)*100
    df['asset_bonus'] = df['asset_type'].map({'Metal':100,'Commodity':90,'Currency':70}).fillna(50)
    df['attention_score'] = df['movement_score']*.65 + df['volume_score']*.15 + df['asset_bonus']*.20
    df['reason'] = df.apply(lambda r: f"{r['asset_type'].lower()} + " + ('strong daily movement' if abs(r['change_%'])>=1 else 'moderate daily movement' if abs(r['change_%'])>=.5 else 'market movement'), axis=1)
    return df.sort_values('attention_score', ascending=False)


def apply_ignore_filter(df, ignore):
    if df.empty or not ignore: return df
    mask = pd.Series(True, index=df.index)
    for col in ['ticker','full_name','short_name','tradingview_search','asset_type']:
        if col in df.columns:
            mask &= ~df[col].astype(str).str.upper().str.replace('/','', regex=False).isin(ignore)
    return df[mask]


def build_assets(ignore_text):
    ignore = parse_ignore(ignore_text)
    stocks = apply_ignore_filter(fetch_stocks(), ignore)
    forex = apply_ignore_filter(fetch_forex(), ignore)
    big = stocks[stocks['asset_type']=='USA Big Stock'].head(2)
    penny = stocks[stocks['asset_type']=='USA Penny Stock Under $1'].head(3)
    if len(big)<2: big = pd.concat([big, stocks[~stocks['ticker'].isin(big['ticker'])].head(2-len(big))])
    if len(penny)<3: penny = pd.concat([penny, stocks[~stocks['ticker'].isin(penny['ticker'])].head(3-len(penny))])
    currencies = forex[forex['asset_type']=='Currency'].head(3)
    metal = forex[forex['asset_type']=='Metal'].head(1)
    commodity = forex[forex['asset_type']=='Commodity'].head(1)
    out = pd.concat([big, penny, currencies, metal, commodity], ignore_index=True)
    if not out.empty:
        out.insert(0, 'date', datetime.now().strftime('%Y-%m-%d'))
    return out


def format_assets_text(df):
    if df.empty: return "No assets found."
    sections = [('USA BIG STOCKS — 2','USA Big Stock'),('USA PENNY STOCKS UNDER $1 — 3','USA Penny Stock Under $1'),('CURRENCIES — 3','Currency'),('METAL — 1','Metal'),('COMMODITY — 1','Commodity')]
    lines = ['FINAL 10 ATTENTION ASSETS', '='*30]
    for title, kind in sections:
        lines += ['', title, '-'*30]
        for _, r in df[df['asset_type']==kind].iterrows():
            name = r['full_name'] if kind not in ['Currency'] else f"{r['full_name']} ({r.get('short_name','')})"
            lines.append(str(name))
            lines.append(f"TradingView: {r['tradingview_search']} | Price: {r['price']} | Move: {r['change_%']}%")
            lines.append(f"Reason: {r['reason']}")
            lines.append('')
    return '\n'.join(lines)

col1, col2 = st.columns(2)
with col1:
    crypto_ignore = st.text_area("Ignored coins", DEFAULT_CRYPTO_IGNORE, height=130)
    top_n = st.number_input("How many crypto picks?", min_value=5, max_value=100, value=20)
with col2:
    asset_ignore = st.text_area("Ignored stocks / forex / commodities", DEFAULT_ASSET_IGNORE, height=130)

run_crypto = st.checkbox("Generate crypto list", value=True)
run_assets = st.checkbox("Generate stock / forex / commodity list", value=True)

if st.button("Generate Today's List", type="primary"):
    if run_crypto:
        with st.spinner("Generating crypto picks..."):
            crypto_df = build_crypto(crypto_ignore, int(top_n))
        st.subheader("Crypto Picks")
        st.dataframe(crypto_df, use_container_width=True)
        st.download_button("Download crypto CSV", crypto_df.to_csv(index=False), f"crypto_picks_{datetime.now().strftime('%Y-%m-%d')}.csv", "text/csv")
        st.text_area("Copy crypto symbols", ", ".join(crypto_df['Symbol'].tolist()) if not crypto_df.empty else "", height=80)
    if run_assets:
        with st.spinner("Generating stock / forex / commodity picks..."):
            assets_df = build_assets(asset_ignore)
        st.subheader("Stock / Forex / Commodity Picks")
        show_cols = [c for c in ['date','asset_type','ticker','full_name','tradingview_search','price','change_%','volume','attention_score','reason'] if c in assets_df.columns]
        st.dataframe(assets_df[show_cols], use_container_width=True)
        st.download_button("Download assets CSV", assets_df.to_csv(index=False), f"daily_attention_assets_{datetime.now().strftime('%Y-%m-%d')}.csv", "text/csv")
        st.text_area("Copy WhatsApp format", format_assets_text(assets_df), height=450)
