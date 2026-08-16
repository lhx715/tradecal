# -*- coding: utf-8 -*-
"""TradeCal cloud fetcher v2 - full S&P500 + Nasdaq-100 coverage.

Data sources:
  - Nasdaq-100 quotes: NASDAQ official API (102 symbols with live quotes)
  - S&P 500 list:      bundled sp500_list.json (491 symbols from Wikipedia)
  - S&P 500 quotes:    Yahoo chart API (batched, ~12 min for 490 symbols)
  - News:              Yahoo RSS for mega-caps + top movers
  - Index status:      Yahoo chart API (NDX/VIX/IRX)
  - Earnings/macro:    curated calendar (same as v1)

Outputs: data.json (for GitHub Pages) + daily email.
"""
import json
import os
import re
import smtplib
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}
opener = urllib.request.build_opener()

BASE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Curated data
# ---------------------------------------------------------------------------
EARNINGS = [
    ('2026-08-26', '英伟达', 'NVDA', 'Q2 FY2027', 'confirmed', 'AI芯片霸主，财报对纳指影响最大'),
    ('2026-10-27', '谷歌', 'GOOGL', 'Q3 2026', 'estimated', '云业务增速 + AI搜索竞争'),
    ('2026-10-28', '特斯拉', 'TSLA', 'Q3 2026', 'confirmed', 'PE 300+，财报是生死局'),
    ('2026-10-28', '微软', 'MSFT', 'Q3 FY2027', 'estimated', '云+AI收入是关键'),
    ('2026-10-28', 'Meta', 'META', 'Q3 2026', 'estimated', '广告 + AI资本开支指引'),
    ('2026-10-29', '苹果', 'AAPL', 'Q4 FY2026', 'estimated', 'iPhone销量 + 服务收入'),
    ('2026-10-29', '亚马逊', 'AMZN', 'Q3 2026', 'estimated', 'AWS增速 + 零售利润率'),
]

MACRO = [
    ('2026-09-04', '非农就业数据', 'high', '20:30', '就业降温=降息预期'),
    ('2026-09-10', 'CPI 通胀数据', 'high', '20:30', '关注同比是否回落'),
    ('2026-09-15', 'FOMC 利率决议', 'high', '09-17 02:00', '维持利率不变预期；附点阵图'),
    ('2026-10-02', '非农就业数据', 'high', '20:30', ''),
    ('2026-10-09', 'CPI 通胀数据', 'high', '20:30', 'Q3 通胀走势'),
    ('2026-10-27', 'FOMC 利率决议', 'high', '10-29 02:00', ''),
    ('2026-11-06', 'CPI 通胀数据', 'high', '20:30', ''),
    ('2026-11-06', '非农就业数据', 'high', '20:30', ''),
    ('2026-12-04', '非农就业数据', 'high', '20:30', ''),
    ('2026-12-08', 'FOMC 利率决议', 'high', '12-10 03:00', '年末会议 + 点阵图'),
    ('2026-12-10', 'CPI 通胀数据', 'high', '20:30', ''),
]

# News priority: mega-caps always + others when selected by top movers
NEWS_PRIORITY = ['NVDA', 'TSLA', 'MSFT', 'GOOGL', 'AAPL', 'AMZN', 'META']

BULLISH = ['beat','surpass','surges','soars','record','growth','raise guidance','upgrade','buyback','strong demand','outperform','bullish','rally','jumps','gains','positive','recovering','surge','突破','超预期','增长','上调','回购','利好','大涨','新高','强劲','回暖','复苏','创纪录']
BEARISH = ['miss','downgrade','cut guidance','weak','slump','layoff','lawsuit','investigation','regulatory','antitrust','bearish','selloff','decline','fall','drops','risk','risks','worries','wary','weary','threat','不及预期','下调','裁员','诉讼','调查','反垄断','利空','大跌','疲软','衰退','风险','下跌','承压','担忧','警告']


def classify(title):
    import html as h
    tl = h.unescape(title).lower()
    b = sum(1 for w in BULLISH if w in tl)
    r = sum(1 for w in BEARISH if w in tl)
    return 'bull' if b > r else ('bear' if r > b else 'neutral')


def http_get_json(url, headers=None, timeout=30):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    return json.loads(opener.open(req, timeout=timeout).read().decode())


def http_get_text(url, headers=None, timeout=30):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    return opener.open(req, timeout=timeout).read().decode('utf-8', errors='ignore')


# ---------------------------------------------------------------------------
# Constituents
# ---------------------------------------------------------------------------
def load_sp500():
    """Load S&P 500 list from bundled JSON (491 symbols)."""
    path = os.path.join(BASE, 'sp500_list.json')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return []


def fetch_nasdaq100():
    """Fetch Nasdaq-100 with live quotes from NASDAQ official API."""
    url = 'https://api.nasdaq.com/api/quote/list-type/nasdaq100'
    try:
        data = http_get_json(url, headers={
            'Accept': 'application/json',
            'Origin': 'https://www.nasdaq.com',
            'Referer': 'https://www.nasdaq.com/',
        })
        rows = data.get('data', {}).get('data', {}).get('rows', [])
        out = []
        for r in rows:
            price = parse_price(r.get('lastSalePrice'))
            chg = parse_pct(r.get('percentageChange'))
            out.append({
                'ticker': r.get('symbol', ''),
                'name': r.get('companyName', ''),
                'sector': r.get('sector', ''),
                'price': price,
                'change_pct': chg,
                'market_cap': r.get('marketCap', ''),
            })
        return out
    except Exception as e:
        print('NASDAQ100 FAIL:', type(e).__name__, str(e)[:100])
        return []


def parse_price(s):
    if not s or s == 'UNCH':
        return None
    try:
        return float(s.replace('$', '').replace(',', ''))
    except (ValueError, AttributeError):
        return None


def parse_pct(s):
    if not s or s == 'UNCH':
        return None
    try:
        return float(s.replace('%', '').replace('+', ''))
    except (ValueError, AttributeError):
        return None


def fetch_quote_yahoo(ticker):
    """Fetch quote for a single symbol via Yahoo chart API."""
    url = (f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}'
           f'?range=5d&interval=1d')
    try:
        data = http_get_json(url)
        q = data['chart']['result'][0]['indicators']['quote'][0]['close']
        q = [c for c in q if c is not None]
        if not q:
            return None
        prev = q[-2] if len(q) > 1 else q[-1]
        chg = (q[-1] - prev) / prev * 100 if prev else 0
        return {'price': round(q[-1], 2), 'change_pct': round(chg, 2)}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------
def rss_news(ticker, n=4):
    try:
        xml = http_get_text(f'https://finance.yahoo.com/rss/headline?s={ticker}')
        items = re.findall(r'<item>(.*?)</item>', xml, re.S)
        out = []
        for it in items[:n]:
            t = re.search(r'<title>(.*?)</title>', it, re.S)
            l = re.search(r'<link>(.*?)</link>', it, re.S)
            d = re.search(r'<pubDate>(.*?)</pubDate>', it, re.S)
            if t:
                import html as h
                title = h.unescape(t.group(1).strip())
                title = re.sub(r'[^\x00-\x7F\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', '', title)
                if title and title.lower() != ticker.lower():
                    out.append({'ticker': ticker, 'title': title,
                                'link': l.group(1).strip() if l else '',
                                'date': d.group(1).strip() if d else '',
                                'sentiment': classify(title)})
        return out
    except Exception:
        return []


def fetch_index(sym):
    try:
        data = http_get_json(
            f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d')
        q = data['chart']['result'][0]['indicators']['quote'][0]['close']
        q = [c for c in q if c is not None]
        if q:
            prev = q[-2] if len(q) > 1 else q[-1]
            chg = (q[-1] - prev) / prev * 100 if prev else 0
            return {'close': round(q[-1], 2), 'change_pct': round(chg, 2)}
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------
def build_snapshot():
    t0 = time.time()
    today = datetime.now().date()

    # --- Nasdaq-100 with quotes ---
    print('Fetching Nasdaq-100...')
    nasdaq100 = fetch_nasdaq100()

    # --- S&P 500 ---
    print('Loading S&P 500 list...')
    sp500 = load_sp500()
    print(f'  {len(sp500)} symbols')

    # --- S&P 500 quotes (batched, only update if no cache or fresh run) ---
    sp500_quoted = []
    for i, s in enumerate(sp500):
        q = fetch_quote_yahoo(s['ticker'])
        sp500_quoted.append({**s, **({'price': q['price'], 'change_pct': q['change_pct']} if q else {'price': None, 'change_pct': None})})
        if (i + 1) % 50 == 0:
            print(f'  quoted {i+1}/{len(sp500)} ({time.time()-t0:.0f}s)')
        time.sleep(0.15)

    # --- News: priority tickers + top movers ---
    print('Fetching news...')
    news_all = []
    # top movers from nasdaq100 (up/down extremes)
    movers = sorted([s for s in nasdaq100 if s.get('change_pct') is not None],
                    key=lambda x: abs(x['change_pct']), reverse=True)[:8]
    news_tickers = list(NEWS_PRIORITY) + [m['ticker'] for m in movers if m['ticker'] not in NEWS_PRIORITY]
    for tk in news_tickers[:12]:
        news_all += rss_news(tk)
        time.sleep(0.3)
    news_all.sort(key=lambda x: x['date'], reverse=True)

    # --- indexes ---
    print('Fetching indexes...')
    market = {}
    for sym, key in [('%5ENDX', 'ndx'), ('%5EGSPC', 'spx'), ('%5EVIX', 'vix'), ('%5EIRX', 'irx')]:
        market[key] = fetch_index(sym)

    # --- earnings / macro ---
    upcoming = []
    for d, comp, tk, period, conf, note in EARNINGS:
        dt = datetime.strptime(d, '%Y-%m-%d').date()
        dl = (dt - today).days
        upcoming.append({'date': d, 'company': comp, 'ticker': tk, 'period': period,
                         'confidence': conf, 'note': note, 'days_left': dl, 'soon': 0 <= dl <= 7})
    macro_upcoming = []
    for d, ev, imp, bj, note in MACRO:
        dt = datetime.strptime(d, '%Y-%m-%d').date()
        dl = (dt - today).days
        if dl >= -1:
            macro_upcoming.append({'date': d, 'event': ev, 'impact': imp,
                                   'beijing_time': bj, 'note': note, 'days_left': dl, 'soon': 0 <= dl <= 3})

    snap = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'today': str(today),
        'nasdaq100': nasdaq100,
        'sp500': sp500_quoted,
        'news': news_all,
        'earnings_upcoming': upcoming,
        'macro_upcoming': macro_upcoming,
        'market': market,
    }
    print(f'Total time: {time.time()-t0:.0f}s')
    return snap


# ---------------------------------------------------------------------------
# Email (same as v1)
# ---------------------------------------------------------------------------
def build_digest(snap):
    L = [f"📊 TradeCal 每日交易摘要 — {datetime.now().strftime('%Y-%m-%d')}", '=' * 46, '']
    L.append('【指数行情】')
    for k, label in [('ndx', '纳斯达克100'), ('spx', '标普500'), ('vix', 'VIX'), ('irx', '13周利率')]:
        v = snap.get('market', {}).get(k)
        if v:
            L.append(f"  {label}: {v['close']}  ({'+' if v['change_pct']>=0 else ''}{v['change_pct']}%)")
    L.append('')
    # top movers
    movers = sorted([s for s in snap.get('nasdaq100', []) if s.get('change_pct') is not None],
                    key=lambda x: abs(x['change_pct']), reverse=True)[:6]
    L.append('【纳指100 今日波动最大】')
    for m in movers:
        L.append(f"  {m['ticker']}: {m['price']} ({'+' if m['change_pct']>=0 else ''}{m['change_pct']}%)")
    L.append('')
    L.append('【未来14天财报】')
    for e in snap.get('earnings_upcoming', []):
        if -1 <= e['days_left'] <= 14:
            dl = '今天！' if e['days_left'] == 0 else (f"{e['days_left']}天后" if e['days_left'] > 0 else f"{abs(e['days_left'])}天前")
            L.append(f"  {'⚠️' if e['soon'] else '  '} {e['date']} {e['company']}({e['ticker']}) {dl}")
    L.append('')
    L.append('【利好/利空新闻】')
    bull = [n for n in snap.get('news', []) if n['sentiment'] == 'bull']
    bear = [n for n in snap.get('news', []) if n['sentiment'] == 'bear']
    if bull:
        L.append('  📈 利好:')
        for n in bull[:4]:
            L.append(f"    {n['ticker']}: {n['title']}")
    if bear:
        L.append('  📉 利空:')
        for n in bear[:4]:
            L.append(f"    {n['ticker']}: {n['title']}")
    if not bull and not bear:
        L.append('  （今日无明确利好/利空新闻）')
    L.append('')
    L.append('-' * 46)
    L.append(f"共 {len(snap.get('sp500', []))} 只标普500 + {len(snap.get('nasdaq100', []))} 只纳指100")
    L.append('TradeCal 云端自动推送 | 数据源: NASDAQ/Yahoo')
    return '\n'.join(L)


def send_email(subject, body):
    host = os.environ.get('SMTP_HOST', 'smtp.qq.com')
    port = int(os.environ.get('SMTP_PORT', '465'))
    username = os.environ['SMTP_USER']
    password = os.environ['SMTP_PASS']
    to_addr = os.environ.get('SMTP_TO', username)
    use_ssl = os.environ.get('SMTP_SSL', '1') != '0'
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = formataddr((str(Header('TradeCal 交易日历', 'utf-8')), username))
    msg['To'] = to_addr
    s = smtplib.SMTP_SSL(host, port, timeout=40)
    try:
        s.login(username, password)
        s.sendmail(username, [to_addr], msg.as_string())
        print('EMAIL SENT ->', to_addr)
    finally:
        s.quit()


def main():
    print('Building snapshot v2...')
    snap = build_snapshot()
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print('data.json written')
    size = os.path.getsize('data.json') / 1024
    print(f'data.json size: {size:.0f} KB')
    if os.environ.get('SMTP_USER'):
        send_email(f"TradeCal 每日摘要 {datetime.now().strftime('%m-%d')}", build_digest(snap))
    else:
        print('no SMTP config, skip email')


if __name__ == '__main__':
    main()
