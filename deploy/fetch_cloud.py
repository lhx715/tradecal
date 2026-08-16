# -*- coding: utf-8 -*-
import json, os, re, smtplib, urllib.request
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}
opener = urllib.request.build_opener()

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

BULLISH = ['beat','surpass','surges','soars','record','growth','raise guidance','upgrade','buyback','strong demand','outperform','bullish','rally','jumps','gains','positive','recovering','surge','突破','超预期','增长','上调','回购','利好','大涨','新高','强劲','回暖','复苏','创纪录']
BEARISH = ['miss','downgrade','cut guidance','weak','slump','layoff','lawsuit','investigation','regulatory','antitrust','bearish','selloff','decline','fall','drops','risk','risks','worries','wary','weary','threat','不及预期','下调','裁员','诉讼','调查','反垄断','利空','大跌','疲软','衰退','风险','下跌','承压','担忧','警告']

def classify(title):
    import html as h
    tl = h.unescape(title).lower()
    b = sum(1 for w in BULLISH if w in tl)
    r = sum(1 for w in BEARISH if w in tl)
    return 'bull' if b > r else ('bear' if r > b else 'neutral')

def rss(ticker, n=4):
    try:
        req = urllib.request.Request(f'https://finance.yahoo.com/rss/headline?s={ticker}', headers=UA)
        xml = opener.open(req, timeout=25).read().decode('utf-8', errors='ignore')
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

def index_close(sym):
    try:
        req = urllib.request.Request(f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d', headers=UA)
        data = json.loads(opener.open(req, timeout=25).read().decode())
        q = data['chart']['result'][0]['indicators']['quote'][0]['close']
        q = [c for c in q if c is not None]
        if q:
            prev = q[-2] if len(q) > 1 else q[-1]
            chg = (q[-1] - prev) / prev * 100 if prev else 0
            return {'close': round(q[-1], 2), 'change_pct': round(chg, 2)}
    except Exception:
        pass
    return None

def build():
    today = datetime.now().date()
    snap = {'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'today': str(today),
            'earnings_upcoming': [], 'macro_upcoming': [], 'news': [], 'market': {}}
    for d, comp, tk, period, conf, note in EARNINGS:
        dt = datetime.strptime(d, '%Y-%m-%d').date()
        dl = (dt - today).days
        snap['earnings_upcoming'].append({'date': d, 'company': comp, 'ticker': tk, 'period': period,
                                          'confidence': conf, 'note': note, 'days_left': dl, 'soon': 0 <= dl <= 7})
    for d, ev, imp, bj, note in MACRO:
        dt = datetime.strptime(d, '%Y-%m-%d').date()
        dl = (dt - today).days
        if dl >= -1:
            snap['macro_upcoming'].append({'date': d, 'event': ev, 'impact': imp,
                                           'beijing_time': bj, 'note': note, 'days_left': dl, 'soon': 0 <= dl <= 3})
    for tk in ['NVDA', 'TSLA', 'MSFT', 'GOOGL', 'AAPL', 'AMZN', 'META']:
        snap['news'] += rss(tk)
    snap['news'].sort(key=lambda x: x['date'], reverse=True)
    for sym, key in [('%5ENDX', 'ndx'), ('%5EVIX', 'vix'), ('%5EIRX', 'irx')]:
        snap['market'][key] = index_close(sym)
    return snap

def digest(snap):
    L = [f"📊 TradeCal 每日交易摘要 — {datetime.now().strftime('%Y-%m-%d')}", '=' * 46, '']
    L.append('【行情速览】')
    for k, label in [('ndx', '纳斯达克 NDX'), ('vix', 'VIX 恐慌'), ('irx', '13周利率')]:
        v = snap['market'].get(k)
        if v:
            L.append(f"  {label}: {v['close']}  ({'+' if v['change_pct']>=0 else ''}{v['change_pct']}%)")
    L.append(''); L.append('【未来14天财报】')
    for e in snap['earnings_upcoming']:
        if -1 <= e['days_left'] <= 14:
            dl = '今天！' if e['days_left'] == 0 else (f"{e['days_left']}天后" if e['days_left'] > 0 else f"{abs(e['days_left'])}天前")
            L.append(f"  {'⚠️' if e['soon'] else '  '} {e['date']} {e['company']}({e['ticker']}) {e['period']} {dl}")
    L.append(''); L.append('【未来7天宏观事件】')
    for m in snap['macro_upcoming']:
        if -1 <= m['days_left'] <= 7:
            L.append(f"  {'⚠️' if m['soon'] else '  '} {m['date']} {m['event']} ({m['beijing_time']})")
    L.append(''); L.append('【利好/利空新闻摘要】')
    bull = [n for n in snap['news'] if n['sentiment'] == 'bull']
    bear = [n for n in snap['news'] if n['sentiment'] == 'bear']
    if bull:
        L.append('  📈 利好:')
        for n in bull[:5]:
            L.append(f"    {n['ticker']}: {n['title']}")
    if bear:
        L.append('  📉 利空:')
        for n in bear[:5]:
            L.append(f"    {n['ticker']}: {n['title']}")
    if not bull and not bear:
        L.append('  （今日无明确利好/利空新闻）')
    L.append(''); L.append('-' * 46); L.append('TradeCal 云端自动推送 | 数据源: Yahoo Finance')
    return '\n'.join(L)

def send(subject, body):
    host = os.environ.get('SMTP_HOST', 'smtp.qq.com')
    port = int(os.environ.get('SMTP_PORT', '465'))
    user = os.environ['SMTP_USER']
    pw = os.environ['SMTP_PASS']
    to = os.environ.get('SMTP_TO', user)
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = formataddr((str(Header('TradeCal 交易日历', 'utf-8')), user))
    msg['To'] = to
    s = smtplib.SMTP_SSL(host, port, timeout=40)
    try:
        s.login(user, pw)
        s.sendmail(user, [to], msg.as_string())
        print('EMAIL SENT ->', to)
    finally:
        s.quit()

def main():
    print('Building snapshot...')
    snap = build()
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print('data.json written, news:', len(snap['news']))
    if os.environ.get('SMTP_USER'):
        send(f"TradeCal 每日摘要 {datetime.now().strftime('%m-%d')}", digest(snap))
    else:
        print('no SMTP config, skip email')

if __name__ == '__main__':
    main()
