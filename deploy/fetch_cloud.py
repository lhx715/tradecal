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

BULLISH = ['beat','surpass','surges','soars','record','growth','raise guidance','upgrade','buyback','strong demand','outperform','bullish','rally','jumps','gains','positive','recovering','surge','突破','超预期','增长','上调','回购','利好','大涨','新高','强劲','回暖','复苏','创纪录','buy','buying','investors buy','increased','higher','rises','climbs','tops','best','favorite','windfall','booming','boost','jump','gain','profit','earnings beat','sales growth','revenue growth','soared','surged','gaining','bull market','upgrade to','outperform rating','price target raise','raise price target']
BEARISH = ['miss','downgrade','cut guidance','weak','slump','layoff','lawsuit','investigation','regulatory','antitrust','bearish','selloff','decline','fall','drops','risk','risks','worries','wary','weary','threat','不及预期','下调','裁员','诉讼','调查','反垄断','利空','大跌','疲软','衰退','风险','下跌','承压','担忧','警告','suffer','suffers','drop','dropped','fell','falling','tumbles','tumble','plunge','plunges','plunged','worse','worst','sink','sank','problem','problems','cut','cuts','lower','weakness','caution','cautious','concern','concerns','trial','addiction','fraud','scandal','penalty','fine','charge','charges','selling','sell-off','selloff','loses','loss','struggle','struggles','pressure','pressured']


def classify_score(title):
    """Keyword classifier -> continuous score in [-1, +1].
    +1 = strongly bullish, -1 = strongly bearish, ~0 = neutral.
    """
    import html as h
    tl = h.unescape(title).lower()
    b = 0
    r = 0
    for w in BULLISH:
        if w in tl:
            b += 1
    for w in BEARISH:
        if w in tl:
            r += 1
    if b == 0 and r == 0:
        return 0.0
    # normalize to [-1, 1] with diminishing returns
    raw = (b - r) / (b + r)
    return max(-1.0, min(1.0, raw))


def classify(title):
    s = classify_score(title)
    if s > 0.15:
        return 'bull'
    if s < -0.15:
        return 'bear'
    return 'neutral'


# ---------------------------------------------------------------------------
# AI scoring (DeepSeek)
# ---------------------------------------------------------------------------
AI_SCORE_WEIGHT = 0.7      # AI weight in final score
KEYWORD_WEIGHT = 0.3       # keyword classifier weight
THRESHOLD = 0.3            # score above -> GOOD, below -threshold -> BAD


def ai_score_news(title, related_tickers, api_key):
    """Ask DeepSeek to score the news for each affected company.
    Returns list of {ticker, score, reason} or None on failure.
    """
    if not api_key:
        return None
    ticker_list = ', '.join(related_tickers) if related_tickers else 'SPX(大盘)'
    prompt = (
        f'你是金融新闻分析师。判断下面这条新闻对提到的每家公司的影响。\n'
        f'新闻标题: "{title}"\n'
        f'可能影响的公司: {ticker_list}\n'
        f'对每家公司分别输出: 方向(bull/bear/neutral)、分数(-1到+1，+1极大利好，-1极大利空)、简短原因(中文,15字内)。\n'
        f'严格输出 JSON 数组，格式: [{{"ticker":"NVDA","direction":"bull","score":0.8,"reason":"AI需求强劲"}}]\n'
        f'如果新闻不针对任何具体公司，用 ticker 为 "MARKET" 表示大盘。'
    )
    body = json.dumps({
        'model': 'deepseek-chat',
        'messages': [
            {'role': 'system', 'content': '你是金融新闻分析师，只输出合法JSON，不要其他文字。'},
            {'role': 'user', 'content': prompt},
        ],
        'max_tokens': 300,
        'temperature': 0,
        'response_format': {'type': 'json_object'},
    }).encode()
    try:
        req = urllib.request.Request('https://api.deepseek.com/chat/completions', data=body, headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'User-Agent': 'TradeCal',
        })
        resp = json.loads(urllib.request.urlopen(req, timeout=40).read().decode())
        content = resp['choices'][0]['message']['content']
        # extract JSON array from response
        m = re.search(r'\[.*\]', content, re.S)
        if m:
            results = json.loads(m.group(0))
            return results
        return None
    except Exception as e:
        print(f'  AI fail: {str(e)[:60]}')
        return None


# ---------------------------------------------------------------------------
# News impact tagging
# ---------------------------------------------------------------------------
COMPANY_KEYWORDS = {
    'NVDA': ['nvidia', '英伟达', 'jensen huang', '黄仁勋'],
    'AAPL': ['apple', '苹果', 'tim cook', '库克', 'iphone'],
    'MSFT': ['microsoft', '微软', 'azure', 'openai', 'chatgpt'],
    'GOOGL': ['alphabet', 'google', '谷歌', 'gemini', 'youtube', 'chrome'],
    'AMZN': ['amazon', '亚马逊', 'aws'],
    'META': ['meta', 'facebook', '脸书', 'instagram', 'zuckerberg'],
    'TSLA': ['tesla', '特斯拉'],
    'AVGO': ['broadcom', '博通'],
    'NFLX': ['netflix', '奈飞'],
    'AMD': ['amd', 'advanced micro devices'],
    'INTC': ['intel', '英特尔'],
    'ORCL': ['oracle', '甲骨文'],
    'CRM': ['salesforce'],
    'ADBE': ['adobe'],
    'CSCO': ['cisco', '思科'],
    'QCOM': ['qualcomm', '高通'],
    'TXN': ['texas instruments', '德州仪器'],
    'MU': ['micron', '美光'],
    'SMCI': ['super micro', '超微'],
    'ARM': ['arm holdings', 'arm '],
    'COST': ['costco', '好市多'],
    'WMT': ['walmart', '沃尔玛'],
    'XOM': ['exxon', '埃克森'],
    'JPM': ['jpmorgan', '摩根大通'],
    'GS': ['goldman', '高盛'],
    'MS': ['morgan stanley', '摩根士丹利'],
    'BAC': ['bank of america', '美国银行'],
    'LLY': ['eli lilly', '礼来'],
    'PFE': ['pfizer', '辉瑞'],
    'JNJ': ['johnson', '强生'],
    'UNH': ['unitedhealth', '联合健康'],
    'DIS': ['disney', '迪士尼'],
    'NKE': ['nike', '耐克'],
    'MCD': ['mcdonald', '麦当劳'],
    'KO': ['coca-cola', '可口可乐'],
    'PEP': ['pepsi', '百事'],
    'BA': ['boeing', '波音'],
    'TSM': ['taiwan semiconductor', '台积电', 'tsmc'],
    'UBER': ['uber', '优步'],
    'SHOP': ['shopify'],
    'PYPL': ['paypal', '贝宝'],
    'PLTR': ['palantir'],
    'V': ['visa', '维萨'],
    'MA': ['mastercard', '万事达'],
    'FED': ['federal reserve', 'fed', '美联储', 'fomc', 'powell', '鲍威尔', 'rate cut', '降息', '加息'],
    'SPX': ['s&p 500', '标普500', '标普', 'wall street', 'dow jones'],
    'NDX': ['nasdaq', '纳斯达克', '纳指'],
    'MACRO': ['cpi', 'inflation', '通胀', 'nonfarm', '非农', 'unemployment',
              '失业率', 'jobs report', 'gdp', 'treasury yield', '国债收益率'],
}

IMPACT_HIGH = ['earnings', '财报', 'guidance', '指引', 'beat', 'miss', '超预期',
               '不及预期', 'rate decision', '利率决议', 'fomc', 'cpi', '非农',
               'merger', 'acquisition', '收购', '合并', 'lawsuit', '诉讼',
               'antitrust', '反垄断', 'recall', '召回', 'layoff', '裁员',
               'bankrupt', '破产', 'fda', 'approval', '获批']
IMPACT_MED = ['ai', 'artificial intelligence', '人工智能', 'chip', '芯片', 'cloud',
              '云', 'semiconductor', '半导体', 'revenue', '营收', 'sales', '销量',
              'launch', '发布', 'patent', '专利', 'tariff', '关税', 'ban', '禁令',
              'regulation', '监管', 'data center', '数据中心', 'robotics', '机器人']


def detect_tickers(title):
    tl = title.lower()
    hits = []
    for tk, kws in COMPANY_KEYWORDS.items():
        for kw in kws:
            if kw in tl:
                hits.append(tk)
                break
    return hits


def impact_level(title):
    tl = title.lower()
    score = 0
    for w in IMPACT_HIGH:
        if w in tl:
            score += 2
    for w in IMPACT_MED:
        if w in tl:
            score += 1
    if score >= 2:
        return 'high'
    if score == 1:
        return 'medium'
    return 'low'


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


def load_cn_names():
    """Load ticker -> Chinese name mapping."""
    path = os.path.join(BASE, 'cn_names.json')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return {}


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
def rss_news(ticker, n=4, api_key=None):
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
                    related = detect_tickers(title)
                    if not related:
                        related = [ticker]
                    kw_score = classify_score(title)
                    kw_dir = classify(title)

                    # AI scoring (per company)
                    impacts = None
                    if api_key:
                        impacts = ai_score_news(title, related, api_key)
                        time.sleep(0.2)  # be nice to API

                    if impacts:
                        # merge: final = keyword*0.3 + AI*0.7, per ticker
                        merged = []
                        for imp in impacts:
                            tk = imp.get('ticker', 'MARKET')
                            ai_s = float(imp.get('score', 0))
                            # keyword score applies to the feed ticker primarily
                            kw_s = kw_score if tk == ticker else 0.0
                            final_s = kw_s * KEYWORD_WEIGHT + ai_s * AI_SCORE_WEIGHT
                            final_s = max(-1.0, min(1.0, final_s))
                            direction = ('bull' if final_s > THRESHOLD
                                         else 'bear' if final_s < -THRESHOLD else 'neutral')
                            merged.append({
                                'ticker': tk,
                                'score': round(final_s, 2),
                                'direction': direction,
                                'reason': imp.get('reason', ''),
                            })
                        out.append({'ticker': ticker, 'title': title,
                                    'link': l.group(1).strip() if l else '',
                                    'date': d.group(1).strip() if d else '',
                                    'sentiment': kw_dir,
                                    'keyword_score': round(kw_score, 2),
                                    'related': related,
                                    'impact': impact_level(title),
                                    'ai': True,
                                    'impacts': merged})
                    else:
                        # fallback: keyword only, apply to feed ticker
                        direction = ('bull' if kw_score > THRESHOLD
                                     else 'bear' if kw_score < -THRESHOLD else 'neutral')
                        out.append({'ticker': ticker, 'title': title,
                                    'link': l.group(1).strip() if l else '',
                                    'date': d.group(1).strip() if d else '',
                                    'sentiment': kw_dir,
                                    'keyword_score': round(kw_score, 2),
                                    'related': related,
                                    'impact': impact_level(title),
                                    'ai': False,
                                    'impacts': [{'ticker': ticker, 'score': round(kw_score, 2),
                                                 'direction': direction, 'reason': '关键词规则'}]})
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

    # --- Chinese names ---
    cn_names = load_cn_names()

    # --- S&P 500 ---
    print('Loading S&P 500 list...')
    sp500 = load_sp500()
    print(f'  {len(sp500)} symbols')

    # --- S&P 500 quotes (batched) ---
    sp500_quoted = []
    for i, s in enumerate(sp500):
        q = fetch_quote_yahoo(s['ticker'])
        sp500_quoted.append({
            **s,
            'cn_name': cn_names.get(s['ticker'], ''),
            **({'price': q['price'], 'change_pct': q['change_pct']} if q else {'price': None, 'change_pct': None}),
        })
        if (i + 1) % 50 == 0:
            print(f'  quoted {i+1}/{len(sp500)} ({time.time()-t0:.0f}s)')
        time.sleep(0.15)

    # add cn_name to nasdaq100 too
    for s in nasdaq100:
        s['cn_name'] = cn_names.get(s['ticker'], '')

    # --- News: priority tickers + top movers ---
    print('Fetching news...')
    news_all = []
    api_key = os.environ.get('DEEPSEEK_API_KEY', '')
    # top movers from nasdaq100 (up/down extremes)
    movers = sorted([s for s in nasdaq100 if s.get('change_pct') is not None],
                    key=lambda x: abs(x['change_pct']), reverse=True)[:8]
    news_tickers = list(NEWS_PRIORITY) + [m['ticker'] for m in movers if m['ticker'] not in NEWS_PRIORITY]
    for tk in news_tickers[:12]:
        news_all += rss_news(tk, api_key=api_key)
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
