#!/usr/bin/env python3
"""
generate_briefing.py — Daily Portfolio Briefing
Run environment: GitHub Actions (no proxy restrictions)
Deps: pip install yfinance pykrx pandas requests

Data sources:
  - yfinance  : US stock prices, PER/PBR/PSR, 실적, 애널리스트
  - pykrx     : KR stock prices, PER/PBR/EPS, 외인/기관/개인 수급
  - CNN API   : Fear & Greed Index
"""

import sys, json, re, warnings, traceback
from datetime import date, timedelta, datetime
warnings.filterwarnings("ignore")

try:
    import yfinance as yf
    import pandas as pd
    from pykrx import stock as krx
    import requests
except ImportError as e:
    print(f"[ERROR] Missing package: {e}")
    print("Run: pip install yfinance pykrx pandas requests")
    sys.exit(1)

# ═══════════════════════════════════════════════════
# DATE HELPERS
# ═══════════════════════════════════════════════════
TODAY = date.today()

def prev_biz_day(d=None, n=1):
    """Return n-th previous business day before d (excl. weekends only)."""
    if d is None:
        d = TODAY
    count, cur = 0, d
    while count < n:
        cur -= timedelta(days=1)
        if cur.weekday() < 5:  # Mon-Fri
            count += 1
    return cur

LAST_BD     = prev_biz_day(TODAY, 1)
BD_5_AGO    = prev_biz_day(TODAY, 5)
BD_30_AGO   = prev_biz_day(TODAY, 22)  # ~1 month

_FMT        = lambda d: d.strftime("%Y%m%d")
LAST_BD_STR = _FMT(LAST_BD)
BD5_STR     = _FMT(BD_5_AGO)
BD30_STR    = _FMT(BD_30_AGO)
TODAY_STR   = _FMT(TODAY)

WEEKDAY_KO = ["월요일","화요일","수요일","목요일","금요일","토요일","일요일"]
DATE_DISPLAY = TODAY.strftime("%Y년 %m월 %d일") + " " + WEEKDAY_KO[TODAY.weekday()]

print(f"[INFO] Generating briefing for {TODAY_STR} | last biz day: {LAST_BD_STR}")

# ═══════════════════════════════════════════════════
# PORTFOLIO (실제 보유 22종목 — 미국 13 + 한국 9)
# ═══════════════════════════════════════════════════
# fmt: (ticker, display_name, qty_or_None, avg_cost_krw_or_None)
US_PORTFOLIO = [
    ("NVDA",  "NVIDIA",              3,    255_023),
    ("AMZN",  "Amazon",              1,    409_686),
    ("AAPL",  "Apple",               10,   141_363),
    ("TSLA",  "Tesla",               1,    311_406),
    ("ORCL",  "Oracle",              3,    223_255),
    ("INTC",  "Intel",               5,    176_716),
    ("MU",    "Micron",              1,    1_469_222),
    ("UNH",   "UnitedHealth",        2,    403_771),
    ("PLTR",  "Palantir",            1,    236_267),
    ("IONQ",  "IonQ",                9,    84_907),
    ("RGTI",  "Rigetti",             2,    66_010),
    ("IREN",  "IREN Ltd",            17,   83_323),
    ("ARKK",  "ARK Innovation ETF",  1,    122_926),
]

KR_PORTFOLIO = [
    ("005930", "삼성전자",              13,   143_007),
    ("005380", "현대차",                1,    709_000),
    ("012450", "한화에어로스페이스",      2,    1_420_500),
    ("035420", "NAVER",               4,    302_000),
    ("064350", "현대로템",              1,    21_500),
    ("263720", "디앤씨미디어",           5,    46_800),
    ("289220", "자이언트스텝",           4,    41_400),
    ("373220", "LG에너지솔루션",         1,    458_500),
    ("379800", "KODEX 미국S&P500",     5,    22_600),
]

# ═══════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════
def safe(v, digits=2, default=None):
    try:
        f = float(v)
        return round(f, digits) if digits >= 0 else int(f)
    except (TypeError, ValueError):
        return default

def nz(v, digits):
    """safe() + 0을 결측으로 취급 (KRX는 데이터 없을 때 0을 반환함)"""
    x = safe(v, digits)
    return None if (x is None or x == 0) else x

def dlog(msg):
    """콘솔 + debug_fetch.log 동시 기록 (Actions 로그 접근 불가 환경 대비)"""
    print(msg)
    try:
        with open("debug_fetch.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except Exception:
        pass

def naver_kr_fundamentals(code):
    """2차 폴백: 네이버 모바일 증권 API (클라우드 IP에서도 안정적)"""
    out = {}
    try:
        r = requests.get(
            f"https://m.stock.naver.com/api/stock/{code}/integration",
            timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        j = r.json()
        infos = {}
        for item in (j.get("totalInfos") or []):
            k = str(item.get("code") or item.get("key") or "").lower()
            v = str(item.get("value") or "")
            v = re.sub(r"[,원배%\s]", "", v)
            infos[k] = v
        dlog(f"  [naver] {code} keys={list(infos.keys())}")
        out["per"] = nz(infos.get("per"), 1)
        out["pbr"] = nz(infos.get("pbr"), 2)
        out["eps"] = nz(infos.get("eps"), 0)
        out["bps"] = nz(infos.get("bps"), 0)
        out["div"] = safe(infos.get("dividendyieldratio"), 2)  # 배당수익률 %
        out["dps"] = safe(infos.get("dividend"), 0)            # 주당배당금 원
        if any(out.get(x) is not None for x in ("per", "pbr", "eps")):
            dlog(f"  [naver-fallback OK] {code}: PER={out.get('per')} PBR={out.get('pbr')} EPS={out.get('eps')}")
            return out
    except Exception as e:
        dlog(f"  [naver-fallback WARN] {code}: {e}")
    return {}

def pct_chg(new, old):
    try:
        return round((float(new) / float(old) - 1) * 100, 2)
    except:
        return None


def yf_kr_fundamentals(code):
    """Fallback via yfinance using .KS then .KQ suffix. Returns dict + suffix used."""
    out = {}
    for suf in (".KS", ".KQ"):
        try:
            info = yf.Ticker(code + suf).info
            if not info:
                continue
            price = info.get("currentPrice") or info.get("regularMarketPrice")
            if price is None and not info.get("trailingPE") and not info.get("priceToBook"):
                continue
            per = safe(info.get("trailingPE"), 1)
            pbr = safe(info.get("priceToBook"), 2)
            eps = safe(info.get("trailingEps"), 0)
            bps = safe(info.get("bookValue"), 0)
            dy  = info.get("dividendYield")
            div = safe(dy * 100, 2) if dy and dy < 1 else safe(dy, 2)
            dps = safe(info.get("dividendRate"), 0)
            if any(x is not None for x in (per, pbr, eps)):
                out = {"per": per, "pbr": pbr, "eps": eps, "bps": bps,
                       "div": div, "dps": dps, "suffix": suf,
                       "price": safe(price, 0),
                       "prev_close": safe(info.get("regularMarketPreviousClose"), 0)}
                dlog(f"  [yf-fallback OK] {code}{suf}: PER={per} PBR={pbr} EPS={eps}")
                return out
        except Exception as e:
            dlog(f"  [yf-fallback WARN] {code}{suf}: {e}")
    return out

def fmt_krw(v, unit="억원"):
    """Format large KRW value to 억원"""
    if v is None:
        return "—"
    v = int(v)
    if unit == "억원":
        b = v // 100_000_000
        return f"{b:+,}억원" if b != 0 else "0억원"
    return f"{v:,}원"

def fmt_pct(v, suffix="%"):
    if v is None:
        return "—"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.1f}{suffix}"

def color_pct(v):
    """Return CSS class based on sign"""
    if v is None:
        return "c-neu"
    return "c-up" if v > 0 else ("c-down" if v < 0 else "c-neu")

def per_class(per):
    """Color-code PER value"""
    if per is None or per <= 0:
        return "t-gray"
    if per < 15:
        return "t-green"  # 저평가 가능성
    if per < 25:
        return "t-blue"   # 적정
    if per < 40:
        return "t-amber"  # 고평가 주의
    return "t-red"        # 매우 고평가

def pbr_class(pbr):
    if pbr is None or pbr <= 0:
        return "t-gray"
    if pbr < 1.0:
        return "t-green"
    if pbr < 3.0:
        return "t-blue"
    if pbr < 7.0:
        return "t-amber"
    return "t-red"

def rec_ko(key):
    mapping = {
        "strong_buy":  ("강력 매수", "t-green"),
        "buy":         ("매수",      "t-green"),
        "hold":        ("중립 유지", "t-amber"),
        "underperform":("비중축소",  "t-red"),
        "sell":        ("매도",      "t-red"),
    }
    return mapping.get((key or "").lower(), ("정보 없음", "t-gray"))

def inv_bar(val_won):
    """Format investor net buying value"""
    if val_won is None:
        return ("—", "t-gray")
    b = val_won // 100_000_000  # 억
    label = f"{b:+,}억"
    css = "t-green" if b > 0 else ("t-red" if b < 0 else "t-gray")
    return (label, css)

# ═══════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════
def fetch_market():
    """Fetch major index + FX + Fear&Greed"""
    d = {}

    # — NASDAQ Composite —
    try:
        h = yf.Ticker("^IXIC").history(period="10d")
        h = h[h.index.dayofweek < 5].dropna()
        d["nasdaq_close"]   = safe(h["Close"].iloc[-1])
        d["nasdaq_chg"]     = pct_chg(h["Close"].iloc[-1], h["Close"].iloc[-2])
        d["nasdaq_5d_chg"]  = pct_chg(h["Close"].iloc[-1], h["Close"].iloc[-6])
        print(f"[OK] NASDAQ {d['nasdaq_close']} ({d['nasdaq_chg']}%)")
    except Exception as e:
        print(f"[WARN] NASDAQ: {e}")

    # — S&P 500 —
    try:
        h = yf.Ticker("^GSPC").history(period="5d")
        d["sp500_close"]  = safe(h["Close"].iloc[-1])
        d["sp500_chg"]    = pct_chg(h["Close"].iloc[-1], h["Close"].iloc[-2])
    except Exception as e:
        print(f"[WARN] SP500: {e}")

    # — USD/KRW —
    try:
        h = yf.Ticker("KRW=X").history(period="5d")
        d["usdkrw"] = safe(h["Close"].iloc[-1], 1)
        print(f"[OK] USD/KRW {d['usdkrw']}")
    except Exception as e:
        print(f"[WARN] FX: {e}")

    # — KOSPI (pykrx index code 1001, yfinance ^KS11 fallback) —
    try:
        df = krx.get_index_ohlcv_by_date(BD5_STR, LAST_BD_STR, "1001")
        if not df.empty:
            d["kospi_close"] = safe(df["종가"].iloc[-1])
            d["kospi_chg"]   = pct_chg(df["종가"].iloc[-1], df["종가"].iloc[-2])
            print(f"[OK] KOSPI {d['kospi_close']} ({d['kospi_chg']}%)")
    except Exception as e:
        print(f"[WARN] KOSPI pykrx: {e}")
    if not d.get("kospi_close"):
        try:
            h = yf.Ticker("^KS11").history(period="5d")
            if not h.empty:
                d["kospi_close"] = safe(h["Close"].iloc[-1])
                if len(h) >= 2:
                    d["kospi_chg"] = pct_chg(h["Close"].iloc[-1], h["Close"].iloc[-2])
                print(f"[OK] KOSPI(yf) {d['kospi_close']} ({d.get('kospi_chg')}%)")
        except Exception as e:
            print(f"[WARN] KOSPI yf: {e}")

    # — KOSDAQ (pykrx index code 2001, yfinance ^KQ11 fallback) —
    try:
        df = krx.get_index_ohlcv_by_date(BD5_STR, LAST_BD_STR, "2001")
        if not df.empty:
            d["kosdaq_close"] = safe(df["종가"].iloc[-1])
            d["kosdaq_chg"]   = pct_chg(df["종가"].iloc[-1], df["종가"].iloc[-2])
            print(f"[OK] KOSDAQ {d['kosdaq_close']} ({d['kosdaq_chg']}%)")
    except Exception as e:
        print(f"[WARN] KOSDAQ pykrx: {e}")
    if not d.get("kosdaq_close"):
        try:
            h = yf.Ticker("^KQ11").history(period="5d")
            if not h.empty:
                d["kosdaq_close"] = safe(h["Close"].iloc[-1])
                if len(h) >= 2:
                    d["kosdaq_chg"] = pct_chg(h["Close"].iloc[-1], h["Close"].iloc[-2])
                print(f"[OK] KOSDAQ(yf) {d['kosdaq_close']} ({d.get('kosdaq_chg')}%)")
        except Exception as e:
            print(f"[WARN] KOSDAQ yf: {e}")

    # — CNN Fear & Greed Index (미국 주식시장 심리 지수) —
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        fg = r.json()["fear_and_greed"]
        d["fng_score"]   = safe(fg["score"], 1)
        d["fng_rating"]  = fg["rating"]         # e.g. "Fear"
        d["fng_prev_1w"] = safe(fg.get("previous_1_week"), 1)
        d["fng_prev_1m"] = safe(fg.get("previous_1_month"), 1)
        d["fng_prev_1y"] = safe(fg.get("previous_1_year"), 1)
        print(f"[OK] FNG {d['fng_score']} ({d['fng_rating']})")
    except Exception as e:
        print(f"[WARN] Fear&Greed: {e}")

    return d


def fetch_us_stock(ticker):
    """Fetch US stock comprehensive fundamentals via yfinance"""
    s = {"ticker": ticker, "ok": False}
    try:
        t  = yf.Ticker(ticker)
        nw = t.news  # list of news items

        info = t.info

        # Price — 일봉 히스토리 기준 (info 필드는 하루 늦은 캐시가 오거나
        # regularMarketChangePercent 단위가 소수/퍼센트로 뒤섞여 신뢰 불가)
        hist = t.history(period="7d")
        closes = hist["Close"].dropna() if hist is not None and not hist.empty else None
        if closes is not None and len(closes) >= 1:
            s["price"] = safe(float(closes.iloc[-1]), 2)
            if len(closes) >= 2:
                s["prev_close"] = safe(float(closes.iloc[-2]), 2)
                s["chg_pct"]    = pct_chg(closes.iloc[-1], closes.iloc[-2])
        else:
            price = info.get("currentPrice") or info.get("regularMarketPrice")
            s["price"]      = safe(price, 2)
            s["prev_close"] = safe(info.get("regularMarketPreviousClose"), 2)
            if s.get("price") and s.get("prev_close"):
                s["chg_pct"] = pct_chg(s["price"], s["prev_close"])
        s["high_52w"]     = safe(info.get("fiftyTwoWeekHigh"), 2)
        s["low_52w"]      = safe(info.get("fiftyTwoWeekLow"), 2)

        # Valuation
        s["per_trailing"] = safe(info.get("trailingPE"), 1)
        s["per_forward"]  = safe(info.get("forwardPE"), 1)
        s["pbr"]          = safe(info.get("priceToBook"), 1)
        s["psr"]          = safe(info.get("priceToSalesTrailing12Months"), 1)

        # Earnings / Growth
        s["eps_trailing"] = safe(info.get("trailingEps"), 2)
        s["eps_forward"]  = safe(info.get("forwardEps"), 2)
        rev_g = info.get("revenueGrowth")
        s["rev_growth"]   = safe(rev_g * 100, 1) if rev_g else None
        earn_g = info.get("earningsGrowth")
        s["earn_growth"]  = safe(earn_g * 100, 1) if earn_g else None
        pm = info.get("profitMargins")
        s["profit_margin"]= safe(pm * 100, 1) if pm else None
        roe = info.get("returnOnEquity")
        s["roe"]          = safe(roe * 100, 1) if roe else None
        s["debt_equity"]  = safe(info.get("debtToEquity"), 1)

        # Dividends
        dy = info.get("dividendYield")
        s["div_yield"]    = safe(dy * 100, 2) if dy else 0.0

        # Analyst
        s["rec_key"]      = (info.get("recommendationKey") or "").lower()
        s["target_mean"]  = safe(info.get("targetMeanPrice"), 2)
        s["target_high"]  = safe(info.get("targetHighPrice"), 2)
        s["target_low"]   = safe(info.get("targetLowPrice"), 2)
        s["analysts"]     = info.get("numberOfAnalystOpinions")

        # Meta
        s["sector"]       = info.get("sector", "")
        s["industry"]     = info.get("industry", "")
        s["name_en"]      = info.get("shortName", ticker)
        s["market_cap"]   = info.get("marketCap")

        # News (top 3)
        s["news"] = [
            {"title": n.get("title",""), "url": n.get("link",""), "pub": n.get("publisher","")}
            for n in (nw or [])[:3]
        ]

        s["ok"] = True
        print(f"[OK] {ticker}: ${s['price']} ({s['chg_pct']}%) PER={s['per_trailing']}")
    except Exception as e:
        print(f"[WARN] {ticker}: {e}")

    return s


def fetch_kr_stock(ticker_code, name):
    """Fetch Korean stock data via pykrx"""
    s = {"ticker": ticker_code, "name": name, "ok": False}

    # — Price & OHLCV —
    try:
        df = krx.get_market_ohlcv_by_date(BD5_STR, LAST_BD_STR, ticker_code)
        if not df.empty:
            s["price"]      = int(df["종가"].iloc[-1])
            s["prev_close"] = int(df["종가"].iloc[-2]) if len(df) >= 2 else None
            s["chg_pct"]    = pct_chg(df["종가"].iloc[-1], df["종가"].iloc[-2]) if len(df) >= 2 else None
            s["high"]       = int(df["고가"].iloc[-1])
            s["low"]        = int(df["저가"].iloc[-1])
            s["volume"]     = int(df["거래량"].iloc[-1])
            s["ok"]         = True
            print(f"[OK] {name}({ticker_code}): {s['price']}원 ({s['chg_pct']}%)")
    except Exception as e:
        print(f"[WARN] KR price {ticker_code}: {e}")

    # — Fundamentals: PER, PBR, EPS, BPS, DIV, DPS —
    try:
        df_f = krx.get_market_fundamental(BD5_STR, LAST_BD_STR, ticker_code)
        df_f = df_f.dropna(how="all") if not df_f.empty else df_f
        dlog(f"  [pykrx-fund] {ticker_code} rows={0 if df_f is None or df_f.empty else len(df_f)} "
             f"last={df_f.iloc[-1].to_dict() if df_f is not None and not df_f.empty else '{}'}")
        if not df_f.empty and len(df_f) > 0:
            row = df_f.iloc[-1]
            s["per"] = nz(row.get("PER"), 1)
            s["pbr"] = nz(row.get("PBR"), 2)
            s["eps"] = nz(row.get("EPS"), 0)
            s["bps"] = nz(row.get("BPS"), 0)
            s["div"] = safe(row.get("DIV"), 2)   # 배당수익률 % (0 = 무배당, 유효값)
            s["dps"] = safe(row.get("DPS"), 0)   # 주당배당금 원
            print(f"  Fund: PER={s['per']} PBR={s['pbr']} EPS={s['eps']}")
    except Exception as e:
        dlog(f"[WARN] KR fund {ticker_code}: {e}")

    # — Fallback 1: yfinance / Fallback 2: 네이버 금융 —
    if not s.get("per") and not s.get("pbr") and not s.get("eps"):
        fb = yf_kr_fundamentals(ticker_code)
        if not (fb.get("per") or fb.get("pbr") or fb.get("eps")):
            nv = naver_kr_fundamentals(ticker_code)
            for k, v in nv.items():
                if v is not None:
                    fb[k] = v
        if fb:
            for k in ("per", "pbr", "eps", "bps", "div", "dps"):
                if fb.get(k) is not None:
                    s[k] = fb[k]
            # also backfill price if pykrx price failed
            if not s.get("ok") and fb.get("price"):
                s["price"] = fb["price"]
                s["prev_close"] = fb.get("prev_close")
                if fb.get("price") and fb.get("prev_close"):
                    s["chg_pct"] = pct_chg(fb["price"], fb["prev_close"])
                s["ok"] = True

    # — Investor trading (외인/기관/개인) last 5 days net —
    try:
        df_inv = krx.get_market_trading_value_by_date(BD5_STR, LAST_BD_STR, ticker_code)
        if not df_inv.empty:
            cols = list(df_inv.columns)
            # Column names may vary by pykrx version
            # Common: '외국인합계', '기관합계', '개인', '기타법인'
            foreign_col = next((c for c in cols if "외국인" in c or "외인" in c), None)
            inst_col    = next((c for c in cols if "기관합계" in c or "기관" == c), None)
            indiv_col   = next((c for c in cols if "개인" in c), None)
            if foreign_col: s["inv_foreign"] = int(df_inv[foreign_col].sum())
            if inst_col:    s["inv_inst"]    = int(df_inv[inst_col].sum())
            if indiv_col:   s["inv_indiv"]   = int(df_inv[indiv_col].sum())
            print(f"  Investor: 외인={fmt_krw(s.get('inv_foreign'))} 기관={fmt_krw(s.get('inv_inst'))}")
    except Exception as e:
        print(f"[WARN] KR investor {ticker_code}: {e}")

    return s


# ═══════════════════════════════════════════════════
# STRATEGY SCORING (종합 진단)
# ═══════════════════════════════════════════════════
def score_us(s, usdkrw=1400):
    """0-100 composite score for US stock"""
    score = 50
    # PER: lower forward PER = better
    per = s.get("per_forward") or s.get("per_trailing")
    if per and per > 0:
        if per < 15:   score += 12
        elif per < 25: score += 6
        elif per > 60: score -= 12
        elif per > 40: score -= 6

    # Revenue growth
    rg = s.get("rev_growth") or 0
    if rg > 30:    score += 10
    elif rg > 10:  score += 5
    elif rg < -5:  score -= 8

    # Earnings growth
    eg = s.get("earn_growth") or 0
    if eg > 30:    score += 8
    elif eg > 0:   score += 3
    elif eg < -20: score -= 8

    # Analyst consensus
    rec = s.get("rec_key", "")
    if rec == "strong_buy": score += 10
    elif rec == "buy":      score += 6
    elif rec == "sell":     score -= 10
    elif rec == "underperform": score -= 6

    # Price vs analyst mean target
    price, target = s.get("price"), s.get("target_mean")
    if price and target and price > 0:
        upside = (target / price - 1) * 100
        if upside > 25:    score += 8
        elif upside > 10:  score += 4
        elif upside < -10: score -= 8

    # Average cost comparison
    avg_krw = s.get("avg_krw")
    if avg_krw and usdkrw and price:
        avg_usd = avg_krw / usdkrw
        gain_pct = (price / avg_usd - 1) * 100
        # If up a lot from avg, consider taking partial profits
        if gain_pct > 50:   score -= 3  # caution: might be overextended
        if gain_pct < -30:  score -= 5  # large unrealized loss

    return max(0, min(100, round(score)))


def score_kr(s):
    """0-100 composite score for KR stock"""
    score = 50

    # PER
    per = s.get("per")
    if per and per > 0:
        if per < 10:   score += 12
        elif per < 20: score += 6
        elif per > 40: score -= 10
        elif per > 60: score -= 14

    # PBR
    pbr = s.get("pbr")
    if pbr:
        if pbr < 0.8:  score += 10
        elif pbr < 1.5: score += 4
        elif pbr > 4:   score -= 6

    # Investor flow: foreign + institutional
    fgn = s.get("inv_foreign", 0) or 0
    ins = s.get("inv_inst", 0) or 0
    smart_money = fgn + ins
    if smart_money > 50_000_000_000:   score += 10  # >500억 순매수
    elif smart_money > 10_000_000_000: score += 5
    elif smart_money < -50_000_000_000: score -= 10
    elif smart_money < -10_000_000_000: score -= 5

    # Dividend yield
    div = s.get("div", 0) or 0
    if div > 3: score += 4
    elif div > 1: score += 2

    return max(0, min(100, round(score)))


STRATEGY_LEVELS = [
    (70, "홀드+",     "green", "펀더멘털 양호. 현재 포지션 유지하면서 추가 매수 기회를 볼 수 있는 종목입니다."),
    (55, "홀드",      "gray",  "특별한 행동 없이 유지하세요. 현재 매도/매수 시점이 아닙니다."),
    (40, "홀드 ⚠️",  "amber", "일부 지표가 부진합니다. 손절 기준을 미리 정해두고 예의주시하세요."),
    (0,  "매도 검토", "red",   "복수 지표가 약세를 가리킵니다. 매도 여부를 진지하게 검토하세요."),
]

def get_strategy(score):
    for threshold, label, color, desc in STRATEGY_LEVELS:
        if score >= threshold:
            return label, color, desc
    return "매도 검토", "red", "지표 전반 부진."


# ═══════════════════════════════════════════════════
# HTML HELPERS
# ═══════════════════════════════════════════════════
def tag(text, cls):
    return f'<span class="tag t-{cls}">{text}</span>'

def metric_cell(label, value, css=""):
    return f'<div class="met"><div class="met-l">{label}</div><div class="met-v{" "+css if css else ""}">{value}</div></div>'

def inv_html(label, val_won):
    lbl, cls = inv_bar(val_won)
    return f'<div class="inv-row"><span class="inv-who">{label}</span><span class="tag {cls}" style="font-size:11px">{lbl}</span></div>'

def chg_badge(pct_val):
    if pct_val is None:
        return '<span class="c-neu">—</span>'
    sign = "▲" if pct_val > 0 else "▼"
    cls = "c-up" if pct_val > 0 else "c-down"
    return f'<span class="{cls}">{sign}{abs(pct_val):.2f}%</span>'

def vs_avg_badge(price_usd, avg_krw, usdkrw):
    """Show gain/loss vs average purchase price"""
    if not (price_usd and avg_krw and usdkrw):
        return ""
    avg_usd = avg_krw / usdkrw
    g = (price_usd / avg_usd - 1) * 100
    sign = "▲" if g > 0 else "▼"
    cls = "c-up" if g > 0 else "c-down"
    return f'<span class="{cls}" style="font-size:11px"> {sign}평단 대비 {abs(g):.1f}%</span>'

def vs_avg_kr_badge(price_krw, avg_krw):
    if not (price_krw and avg_krw):
        return ""
    g = (price_krw / avg_krw - 1) * 100
    sign = "▲" if g > 0 else "▼"
    cls = "c-up" if g > 0 else "c-down"
    return f'<span class="{cls}" style="font-size:11px"> {sign}평단 대비 {abs(g):.1f}%</span>'

def news_items_html(news_list):
    if not news_list:
        return ""
    items = "".join(
        f'<div class="news-li"><a href="{n["url"]}" target="_blank" rel="noopener">{n["title"]}</a>'
        f'<span class="news-pub"> · {n["pub"]}</span></div>'
        for n in news_list if n.get("title")
    )
    if not items:
        return ""
    return f'<div class="news-blk"><div class="news-lbl">최신 뉴스</div>{items}</div>'


# ═══════════════════════════════════════════════════
# FNG SECTION
# ═══════════════════════════════════════════════════
FNG_COLORS = {
    "Extreme Fear": "#b83229",
    "Fear":         "#c0663a",
    "Neutral":      "#7a4a08",
    "Greed":        "#4a7c3f",
    "Extreme Greed":"#1f6636",
}
FNG_KO = {
    "Extreme Fear": "극도의 공포",
    "Fear":         "공포",
    "Neutral":      "중립",
    "Greed":        "탐욕",
    "Extreme Greed":"극도의 탐욕",
}
FNG_EMOJI = {
    "Extreme Fear": "😱",
    "Fear":         "😰",
    "Neutral":      "😐",
    "Greed":        "😏",
    "Extreme Greed":"🤑",
}
FNG_INTERP = {
    "Extreme Fear":  "시장이 <strong>극도로 겁먹은</strong> 상태입니다. 좋은 종목도 싸게 팔리는 구간일 수 있습니다. 단, 추가 하락에 대비해 분할 접근하세요.",
    "Fear":          "투자자 심리가 <strong>위축</strong>돼 있습니다. 무리한 추가 매수보다 현금 비중 유지가 안전합니다.",
    "Neutral":       "시장 심리가 <strong>중립</strong>입니다. 개별 종목 실적과 뉴스에 따라 대응하세요.",
    "Greed":         "분위기가 좋지만 <strong>과열 주의</strong> 구간입니다. 신규 진입보다 보유 종목 점검이 우선입니다.",
    "Extreme Greed": "<strong>극도의 과열</strong>입니다. 매수보다 일부 차익 실현을 고려해보세요.",
}

def fng_section_html(mkt):
    score   = mkt.get("fng_score")
    rating  = mkt.get("fng_rating", "")
    prev1w  = mkt.get("fng_prev_1w")
    prev1m  = mkt.get("fng_prev_1m")
    prev1y  = mkt.get("fng_prev_1y")

    if score is None:
        score, rating = 25, "Fear"  # fallback if API fails

    color    = FNG_COLORS.get(rating, "#7a4a08")
    ko_name  = FNG_KO.get(rating, rating)
    emoji    = FNG_EMOJI.get(rating, "📊")
    interp   = FNG_INTERP.get(rating, "")
    needle   = max(2, min(98, score))

    def hist_tag(label, val):
        if val is None:
            return ""
        vr = round(val)
        grade = ("극도공포" if vr<=24 else "공포" if vr<=44 else "중립" if vr<=55 else "탐욕" if vr<=74 else "극도탐욕")
        return f'<span class="tag t-gray">{label}: {vr} ({grade})</span>'

    return f"""
  <div class="section" id="fng">
    <div class="sec-title">😨 공포탐욕지수 — CNN Fear &amp; Greed Index (미국 주식시장 심리)</div>
    <div class="card"><div style="padding:16px">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:14px;gap:10px">
        <div style="flex:1">
          <div style="font-size:11px;font-weight:700;color:var(--text3);margin-bottom:6px">미국 주식시장 투자자 심리 (0=극도공포 / 100=극도탐욕)</div>
          <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
            <span style="font-size:42px;font-weight:900;line-height:1;color:{color}">{int(score)}</span>
            <div>
              <div style="font-size:15px;font-weight:800;color:{color}">{ko_name}</div>
              <div style="font-size:11px;color:var(--text3);margin-top:2px">{rating} · CNN</div>
            </div>
          </div>
        </div>
        <div style="font-size:48px;line-height:1;flex-shrink:0">{emoji}</div>
      </div>
      <div style="position:relative;height:12px;border-radius:6px;background:linear-gradient(to right,#b83229 0%,#d4652b 25%,#c8a000 50%,#6aab5e 75%,#1f6636 100%);overflow:visible;margin-bottom:4px">
        <div style="position:absolute;top:-5px;width:6px;height:22px;background:var(--text);border-radius:3px;transform:translateX(-50%);left:{needle}%;box-shadow:0 1px 4px rgba(0,0,0,.35)"></div>
      </div>
      <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text3);padding:0 2px;margin-bottom:14px">
        <span>😱 극도공포</span><span>😰 공포</span><span>😐 중립</span><span>😏 탐욕</span><span>🤑 극도탐욕</span>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px">
        {hist_tag("1주 전", prev1w)}
        {hist_tag("1달 전", prev1m)}
        {hist_tag("1년 전", prev1y)}
      </div>
      <div style="background:var(--surface2);border-radius:8px;padding:11px 14px;font-size:12px;color:var(--text2);line-height:1.65">{interp}</div>
    </div></div>
  </div>"""


# ═══════════════════════════════════════════════════
# US STOCK CARD HTML
# ═══════════════════════════════════════════════════
def us_stock_card_html(s, portfolio_meta, usdkrw):
    ticker   = s["ticker"]
    name_kr, qty, avg_krw = portfolio_meta

    price    = s.get("price")
    chg      = s.get("chg_pct")
    high52   = s.get("high_52w")
    low52    = s.get("low_52w")

    score    = score_us({**s, "avg_krw": avg_krw}, usdkrw or 1400)
    strat, strat_col, strat_desc = get_strategy(score)

    # meta line
    meta_parts = []
    if qty:     meta_parts.append(f"{qty}주 보유")
    if avg_krw: meta_parts.append(f"평단 {avg_krw:,}원")
    meta_str = " · ".join(meta_parts) or "보유 중"

    # Price display
    price_str = f"${price:,.2f}" if price else "—"
    chg_html  = chg_badge(chg)
    avg_html  = vs_avg_badge(price, avg_krw, usdkrw)

    # 52w range bar
    range_html = ""
    if high52 and low52 and price and high52 != low52:
        pos_pct = max(0, min(100, (price - low52) / (high52 - low52) * 100))
        range_html = f"""
    <div class="rng-wrap">
      <div class="rng-lbl"><span>${low52:,.0f}</span><span style="font-size:10px;color:var(--text3)">52주 범위</span><span>${high52:,.0f}</span></div>
      <div class="rng-track"><div class="rng-fill" style="width:{pos_pct:.0f}%"></div></div>
    </div>"""

    # Valuation metrics
    per_t = s.get("per_trailing")
    per_f = s.get("per_forward")
    pbr   = s.get("pbr")
    psr   = s.get("psr")

    def val_cell(label, val, cls_fn=None, suffix=""):
        if val is None: return f'<div class="met"><div class="met-l">{label}</div><div class="met-v c-neu">—</div></div>'
        css = cls_fn(val) if cls_fn else ""
        tag_html = f'<span class="tag {css}" style="font-size:11px;padding:1px 6px">{val}{suffix}</span>' if css else f'{val}{suffix}'
        return f'<div class="met"><div class="met-l">{label}</div><div class="met-v">{tag_html}</div></div>'

    metrics_html = f"""
    <div class="met-grid">
      {val_cell("PER(현재)", per_t, per_class, "x")}
      {val_cell("PER(예상)", per_f, per_class, "x")}
      {val_cell("PBR", pbr, pbr_class, "x")}
      {val_cell("PSR", psr, None, "x")}
      {val_cell("EPS(현재)", s.get('eps_trailing'), None, "$")}
      {val_cell("EPS(예상)", s.get('eps_forward'), None, "$")}
      {val_cell("매출성장", f"{fmt_pct(s.get('rev_growth'))}" if s.get('rev_growth') is not None else None)}
      {val_cell("순익성장", f"{fmt_pct(s.get('earn_growth'))}" if s.get('earn_growth') is not None else None)}
      {val_cell("ROE", f"{s.get('roe')}%" if s.get('roe') is not None else None)}
      {val_cell("부채비율", f"{s.get('debt_equity')}" if s.get('debt_equity') is not None else None)}
      {val_cell("배당수익률", f"{s.get('div_yield')}%" if s.get('div_yield') else "—")}
    </div>"""

    # Analyst section
    rec_text, rec_cls = rec_ko(s.get("rec_key"))
    target = s.get("target_mean")
    upside_html = ""
    if target and price:
        upside = (target / price - 1) * 100
        upside_html = f' → 현재가 대비 <strong>{fmt_pct(upside)}</strong>'
    analyst_html = f"""
    <div class="analyst-row">
      <span class="tag t-{rec_cls}">{rec_text}</span>
      {f'<span style="font-size:12px;color:var(--text2)">목표가 ${target}{upside_html} ({s.get("analysts","?")}명)</span>' if target else ""}
    </div>"""

    # News
    n_html = news_items_html(s.get("news", []))

    # Strategy description
    strat_html = f'<div class="strat-desc">{strat_desc}</div>' if strat_desc else ""

    return f"""
      <div class="stk-item">
        <div class="stk-top">
          <div class="stk-info">
            <div class="stk-name">{ticker} <span style="font-weight:500;color:var(--text2)">{name_kr}</span></div>
            <div class="stk-meta">{meta_str}</div>
          </div>
          <div class="stk-tag"><span class="tag t-{strat_col}">{strat}</span></div>
        </div>
        <div style="padding:4px 16px 10px">
          <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:6px;flex-wrap:wrap">
            <span style="font-size:22px;font-weight:900">{price_str}</span>
            {chg_html}
            {avg_html}
          </div>
          {range_html}
          {metrics_html}
          {analyst_html}
          {strat_html}
        </div>
        {n_html}
      </div>"""


# ═══════════════════════════════════════════════════
# KR STOCK CARD HTML
# ═══════════════════════════════════════════════════
def kr_stock_card_html(s, portfolio_meta):
    code   = s["ticker"]
    name   = s["name"]
    _, qty, avg_krw = portfolio_meta

    price  = s.get("price")
    chg    = s.get("chg_pct")

    score  = score_kr(s)
    strat, strat_col, strat_desc = get_strategy(score)

    meta_parts = []
    if qty:     meta_parts.append(f"{qty}주 보유")
    if avg_krw: meta_parts.append(f"평단 {avg_krw:,}원")
    meta_str = " · ".join(meta_parts) or "보유 중"

    price_str = f"{price:,}원" if price else "—"
    chg_html  = chg_badge(chg)
    avg_html  = vs_avg_kr_badge(price, avg_krw)

    # Fundamentals
    per   = s.get("per")
    pbr   = s.get("pbr")
    eps   = s.get("eps")
    bps   = s.get("bps")
    div   = s.get("div")
    dps   = s.get("dps")

    def kr_met(label, val, suffix=""):
        if val is None: return f'<div class="met"><div class="met-l">{label}</div><div class="met-v c-neu">—</div></div>'
        return f'<div class="met"><div class="met-l">{label}</div><div class="met-v">{val}{suffix}</div></div>'

    def kr_met_tag(label, val, cls_fn, suffix=""):
        if val is None: return f'<div class="met"><div class="met-l">{label}</div><div class="met-v c-neu">—</div></div>'
        css = cls_fn(val)
        inner = f'<span class="tag {css}" style="font-size:11px;padding:1px 6px">{val}{suffix}</span>'
        return f'<div class="met"><div class="met-l">{label}</div><div class="met-v">{inner}</div></div>'

    metrics_html = f"""
    <div class="met-grid">
      {kr_met_tag("PER", per, per_class, "배")}
      {kr_met_tag("PBR", pbr, pbr_class, "배")}
      {kr_met("EPS", f"{int(eps):,}" if eps else None, "원")}
      {kr_met("BPS", f"{int(bps):,}" if bps else None, "원")}
      {kr_met("배당수익률", div, "%")}
      {kr_met("주당배당금", f"{int(dps):,}" if dps else None, "원")}
    </div>"""

    # Investor flows
    fgn  = s.get("inv_foreign")
    ins  = s.get("inv_inst")
    ind  = s.get("inv_indiv")
    has_inv = any(x is not None for x in [fgn, ins, ind])
    inv_section = ""
    if has_inv:
        inv_section = f"""
    <div class="inv-section">
      <div class="inv-title">외인/기관/개인 수급 (최근 5거래일 순매수)</div>
      {inv_html("외국인", fgn)}
      {inv_html("기관", ins)}
      {inv_html("개인", ind)}
    </div>"""

    strat_html = f'<div class="strat-desc">{strat_desc}</div>' if strat_desc else ""

    return f"""
      <div class="stk-item">
        <div class="stk-top">
          <div class="stk-info">
            <div class="stk-name">{name} <span style="font-size:11px;color:var(--text3)">{code}</span></div>
            <div class="stk-meta">{meta_str}</div>
          </div>
          <div class="stk-tag"><span class="tag t-{strat_col}">{strat}</span></div>
        </div>
        <div style="padding:4px 16px 10px">
          <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:8px;flex-wrap:wrap">
            <span style="font-size:22px;font-weight:900">{price_str}</span>
            {chg_html}
            {avg_html}
          </div>
          {metrics_html}
          {inv_section}
          {strat_html}
        </div>
      </div>"""


# ═══════════════════════════════════════════════════
# MAIN HTML BUILDER
# ═══════════════════════════════════════════════════

# ═══════════════════════════════════════════════════
# 나의 매매 기록 (trades.csv)
# ═══════════════════════════════════════════════════
def load_trades(path="trades.csv"):
    """Read trades.csv → list of dict. Tolerant of blanks/comments.
    Columns: date,ticker,name,market,action,qty,price,memo (memo may contain commas)."""
    rows = []
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return rows
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        parts = [p.strip() for p in s.split(",", 7)]
        if parts and parts[0].lower() == "date":
            continue
        while len(parts) < 8:
            parts.append("")
        rows.append({
            "date": parts[0], "ticker": parts[1], "name": parts[2],
            "market": parts[3], "action": parts[4].lower(),
            "qty": parts[5], "price": parts[6], "memo": parts[7],
        })
    return rows


def _is_buy(action):
    return action in ("buy", "매수", "b", "bought")


def trades_section_html(trades):
    title = '<div class="sec-title">📒 나의 매매 기록</div>'

    # server rows from trades.csv
    if trades:
        ts = sorted(trades, key=lambda t: t.get("date", ""), reverse=True)
        nbuy = sum(1 for t in ts if _is_buy(t["action"]))
        nsell = len(ts) - nbuy
        srv = ""
        cur = None
        for t in ts:
            if t["date"] != cur:
                cur = t["date"]
                srv += f'<div style="font-size:12px;font-weight:800;color:var(--text2);margin:14px 0 6px">{cur}</div>'
            buy = _is_buy(t["action"])
            bc = "t-red" if buy else "t-blue"
            bt = "매수" if buy else "매도"
            ps = f'@ {t["price"]}' if t["price"] else ""
            ms = (f'<span style="color:var(--text3);font-size:11px;margin-left:6px">· {t["memo"]}</span>' if t["memo"] else "")
            srv += ('<div style="display:flex;align-items:center;gap:8px;padding:9px 11px;border:1px solid var(--border);'
                    'border-radius:var(--r-sm);margin-bottom:6px;background:var(--surface);flex-wrap:wrap">'
                    f'<span class="tag {bc}">{bt}</span>'
                    f'<span style="font-weight:700">{t["name"] or t["ticker"]}</span>'
                    f'<span style="color:var(--text3);font-size:12px">{t["ticker"]}</span>'
                    f'<span style="margin-left:auto;font-size:13px;font-weight:600">{t["qty"]}주 {ps}</span>'
                    f'{ms}</div>')
        srv_head = ('<div style="display:flex;gap:6px;padding:12px 0 2px;align-items:center">'
                    f'<span class="tag t-red">매수 {nbuy}</span>'
                    f'<span class="tag t-blue">매도 {nsell}</span>'
                    f'<span style="margin-left:auto;font-size:11px;color:var(--text3)">저장소 기록 {len(ts)}건</span></div>')
        server_block = srv_head + srv
    else:
        server_block = ('<div style="padding:10px 0;color:var(--text3);font-size:12px">저장소(trades.csv)에 저장된 기록이 아직 없습니다.</div>')

    INPUT = "padding:7px 9px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text1);font-size:13px;min-width:0"

    form = (
        '<div style="display:flex;flex-wrap:wrap;gap:7px;padding:12px 14px 6px;align-items:center">'
        f'<input id="tr-date" type="date" style="{INPUT}">'
        f'<input id="tr-ticker" placeholder="티커 (TSLA)" style="{INPUT};width:96px">'
        f'<input id="tr-name" placeholder="종목명" style="{INPUT};width:96px">'
        f'<select id="tr-market" style="{INPUT}"><option>US</option><option>KR</option></select>'
        f'<select id="tr-action" style="{INPUT}"><option>매수</option><option>매도</option></select>'
        f'<input id="tr-qty" type="number" placeholder="수량" style="{INPUT};width:74px">'
        f'<input id="tr-price" placeholder="가격" style="{INPUT};width:88px">'
        f'<input id="tr-memo" placeholder="메모" style="{INPUT};flex:1;min-width:110px">'
        '<button onclick="addTrade()" style="padding:8px 16px;border:none;border-radius:8px;background:var(--accent,#3b82f6);color:#fff;font-weight:700;font-size:13px;cursor:pointer">＋ 추가</button>'
        '</div>'
    )

    js = JS_TRADES

    return (f'<div class="section" id="trades">{title}<div class="card">'
            f'{form}'
            '<div id="tr-local" style="padding:0 14px"></div>'
            f'<div style="padding:4px 14px 14px">{server_block}</div>'
            f'</div>{js}</div>')


JS_TRADES = """
<script>
var TR_KEY = "pb_local_trades_v1";
var TR_EDIT = "https://github.com/minseonprivacy-gif/portfolio-briefing/edit/main/trades.csv";
function trLoad(){ try{ return JSON.parse(localStorage.getItem(TR_KEY))||[]; }catch(e){ return []; } }
function trSave(a){ localStorage.setItem(TR_KEY, JSON.stringify(a)); }
function trEsc(s){ return (s||"").toString().replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
function trIsBuy(a){ return ["buy","매수","b","bought"].indexOf((a||"").toLowerCase())>=0; }
function addTrade(){
  var g=function(id){return (document.getElementById(id).value||"").trim();};
  var t={date:g("tr-date")||new Date().toISOString().slice(0,10),ticker:g("tr-ticker"),name:g("tr-name"),
         market:document.getElementById("tr-market").value,action:document.getElementById("tr-action").value,
         qty:g("tr-qty"),price:g("tr-price"),memo:g("tr-memo")};
  if(!t.ticker && !t.name){ alert("티커 또는 종목명을 입력하세요."); return; }
  var a=trLoad(); a.push(t); trSave(a); trRender();
  ["tr-ticker","tr-name","tr-qty","tr-price","tr-memo"].forEach(function(id){document.getElementById(id).value="";});
}
function delTrade(i){ var a=trLoad(); a.splice(i,1); trSave(a); trRender(); }
function trCsv(){
  return trLoad().map(function(t){
    return [t.date,t.ticker,t.name,t.market,t.action,t.qty,t.price,(t.memo||"").replace(/\n/g," ")].join(",");
  }).join("\n");
}
function trSync(){
  var csv=trCsv();
  if(!csv){ alert("기기에 저장된 매매가 없습니다."); return; }
  if(navigator.clipboard){ navigator.clipboard.writeText(csv); }
  alert("아래 줄이 클립보드에 복사되었습니다.\n열리는 GitHub 편집창 맨 아래에 붙여넣고 'Commit changes'를 누르면 모든 기기에 반영됩니다.\n\n"+csv);
  window.open(TR_EDIT,"_blank");
}
function trRow(t,i){
  var buy=trIsBuy(t.action), cls=buy?"t-red":"t-blue", txt=buy?"매수":"매도";
  var price=t.price?("@ "+trEsc(t.price)):"";
  var memo=t.memo?('<span style="color:var(--text3);font-size:11px;margin-left:6px">· '+trEsc(t.memo)+'</span>'):"";
  return '<div style="display:flex;align-items:center;gap:8px;padding:9px 11px;border:1px dashed var(--border);border-radius:var(--r-sm,10px);margin-bottom:6px;background:var(--surface);flex-wrap:wrap">'
    +'<span class="tag '+cls+'">'+txt+'</span>'
    +'<span style="font-weight:700">'+trEsc(t.name||t.ticker)+'</span>'
    +'<span style="color:var(--text3);font-size:12px">'+trEsc(t.ticker)+'</span>'
    +'<span style="font-size:11px;color:var(--text3)">'+trEsc(t.date)+'</span>'
    +'<span style="margin-left:auto;font-size:13px;font-weight:600">'+trEsc(t.qty)+'주 '+price+'</span>'
    +memo
    +'<button onclick="delTrade('+i+')" title="삭제" style="border:none;background:none;cursor:pointer;color:var(--text3);font-size:14px">🗑</button>'
    +'</div>';
}
function trRender(){
  var box=document.getElementById("tr-local"); if(!box) return;
  var a=trLoad();
  if(!a.length){ box.innerHTML=""; return; }
  a=a.map(function(t,i){return {t:t,i:i};}).sort(function(x,y){return (y.t.date||"").localeCompare(x.t.date||"");});
  var rows=a.map(function(o){return trRow(o.t,o.i);}).join("");
  var bar='<div style="display:flex;gap:8px;align-items:center;margin:10px 0 6px">'
    +'<span class="tag t-blue" style="background:none;border:1px dashed var(--border);color:var(--text2)">내 기기 기록 '+a.length+'건</span>'
    +'<button onclick="trSync()" style="margin-left:auto;padding:6px 12px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text1);font-size:12px;font-weight:700;cursor:pointer">☁ GitHub에 저장(모든 기기)</button>'
    +'<button onclick="if(confirm(\'기기 기록을 모두 지울까요? (GitHub 저장분은 유지)\')){trSave([]);trRender();}" style="padding:6px 12px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text3);font-size:12px;cursor:pointer">비우기</button>'
    +'</div>';
  box.innerHTML=bar+rows;
}
(function(){
  var d=document.getElementById("tr-date"); if(d && !d.value){ d.value=new Date().toISOString().slice(0,10); }
  trRender();
})();
</script>
"""


def build_html(mkt, us_data_list, kr_data_list, trades=None):
    usdkrw = mkt.get("usdkrw", 1400)

    # Market card helpers
    def mkt_card(label, value, change, note="", index_name=""):
        cls = color_pct(change)
        chg_str = chg_badge(change) if change is not None else ""
        note_html = f'<div class="m-chg c-neu">{note}</div>' if note else ""
        # Label includes exact index name to avoid confusion
        return f"""<div class="market-card">
          <div class="m-label">{label}</div>
          <div class="m-val {cls}">{value if value else "—"}</div>
          <div class="m-chg">{chg_str}</div>
          {note_html}
        </div>"""

    nasdaq_c = mkt.get("nasdaq_close")
    nasdaq_d = mkt.get("nasdaq_chg")
    kospi_c  = mkt.get("kospi_close")
    kospi_d  = mkt.get("kospi_chg")
    kosdaq_c = mkt.get("kosdaq_close")
    kosdaq_d = mkt.get("kosdaq_chg")
    sp500_c  = mkt.get("sp500_close")
    sp500_d  = mkt.get("sp500_chg")
    usdkrw_v = mkt.get("usdkrw")

    # Build US stock cards
    us_cards_html = ""
    for s, pm in zip(us_data_list, US_PORTFOLIO):
        us_cards_html += us_stock_card_html(s, (pm[1], pm[2], pm[3]), usdkrw)

    # Build KR stock cards
    kr_cards_html = ""
    for s, pm in zip(kr_data_list, KR_PORTFOLIO):
        kr_cards_html += kr_stock_card_html(s, (pm[1], pm[2], pm[3]))

    # Strategy legend
    legend_html = """
    <div class="legend-box">
      <div class="legend-title">📌 전략 등급 읽는 법</div>
      <div class="legend-grid">
        <div class="legend-item"><span class="tag t-green">홀드+</span><span class="legend-desc">유지하면서 추가 매수 기회를 볼 수 있는 종목</span></div>
        <div class="legend-item"><span class="tag t-gray">홀드</span><span class="legend-desc">지금 아무 행동도 필요 없습니다. 그냥 유지</span></div>
        <div class="legend-item"><span class="tag t-amber">홀드 ⚠️</span><span class="legend-desc">유지하되 손절 기준 미리 정해두고 예의주시</span></div>
        <div class="legend-item"><span class="tag t-red">매도 검토</span><span class="legend-desc">복수 지표 약세. 매도를 진지하게 검토할 시점</span></div>
      </div>
      <div style="font-size:10px;color:var(--text3);margin-top:8px">
        ※ 등급은 PER·PBR·PSR·매출성장·수익성장·ROE·애널리스트컨센서스·목표주가·수급 데이터를 종합한 자동 진단입니다. 최종 투자 결정은 본인 판단으로 하세요.
      </div>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>포트폴리오 브리핑 — {TODAY.strftime('%Y.%m.%d')}</title>
<style>
:root {{
  --bg:#f4f4f2;--surface:#fff;--surface2:#f0efeb;
  --border:rgba(0,0,0,.07);--border2:rgba(0,0,0,.12);
  --text:#1a1a18;--text2:#5c5c58;--text3:#9a9a96;
  --red:#b83229;--red-bg:#fdf0ef;--red-bd:#f5c4c0;
  --green:#1f6636;--green-bg:#edf7f1;--green-bd:#aedbbf;
  --amber:#7a4a08;--amber-bg:#fdf5e6;--amber-bd:#f4d595;
  --blue:#154c8c;--blue-bg:#edf4fd;--blue-bd:#aed0f5;
  --purple:#5b2d8e;--purple-bg:#f3edfb;--purple-bd:#c9a8ec;
  --r:12px;--r-sm:7px;--shadow:0 1px 3px rgba(0,0,0,.07);
}}
*{{box-sizing:border-box;margin:0;padding:0;-webkit-text-size-adjust:100%}}
html{{scroll-behavior:smooth}}
body{{font-family:-apple-system,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;background:var(--bg);color:var(--text);font-size:15px;line-height:1.6}}
.page{{max-width:900px;margin:0 auto;padding:24px 16px 80px}}

/* Header */
.header{{margin-bottom:20px}}
.header-meta{{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;flex-wrap:wrap;gap:6px}}
.header-date{{font-size:12px;color:var(--text3);font-weight:500;letter-spacing:.04em}}
.badge{{font-size:11px;font-weight:700;border-radius:20px;padding:3px 11px;background:var(--red-bg);color:var(--red);border:1px solid var(--red-bd)}}
.header h1{{font-size:clamp(20px,5vw,28px);font-weight:800;letter-spacing:-.03em;line-height:1.25}}
.header-sub{{font-size:13px;color:var(--text2);margin-top:4px}}

/* TOC */
.toc{{position:sticky;top:0;z-index:100;background:rgba(244,244,242,.92);backdrop-filter:blur(10px);border-bottom:1px solid var(--border2);margin:0 -16px 20px;overflow-x:auto;white-space:nowrap;scrollbar-width:none}}
.toc::-webkit-scrollbar{{display:none}}
.toc-inner{{display:inline-flex;gap:2px;padding:8px 16px}}
.toc a{{font-size:12px;font-weight:600;color:var(--text2);padding:5px 11px;border-radius:20px;text-decoration:none;white-space:nowrap;transition:background .15s,color .15s}}
.toc a:hover,.toc a:active{{background:var(--surface);color:var(--text)}}

/* Market grid */
.market-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px}}
.market-card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:14px 16px;box-shadow:var(--shadow)}}
.m-label{{font-size:11px;color:var(--text3);font-weight:600;letter-spacing:.04em;margin-bottom:4px}}
.m-val{{font-size:clamp(16px,4vw,22px);font-weight:800;line-height:1.1}}
.m-chg{{font-size:12px;margin-top:3px}}
.c-up{{color:var(--green)}}.c-down{{color:var(--red)}}.c-neu{{color:var(--text2)}}

/* Section */
.section{{margin-bottom:28px;scroll-margin-top:50px}}
.sec-title{{font-size:11px;font-weight:700;color:var(--text3);letter-spacing:.08em;text-transform:uppercase;padding-bottom:10px;border-bottom:1px solid var(--border2);margin-bottom:14px}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);box-shadow:var(--shadow);overflow:hidden;margin-bottom:8px}}
.sumbox{{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:14px 16px;margin-bottom:12px;box-shadow:var(--shadow);font-size:14px;line-height:1.75}}
.sumbox strong{{font-weight:700}}

/* Tags */
.tag{{display:inline-block;font-size:11px;font-weight:700;padding:2px 9px;border-radius:20px;white-space:nowrap}}
.t-red{{background:var(--red-bg);color:var(--red);border:1px solid var(--red-bd)}}
.t-green{{background:var(--green-bg);color:var(--green);border:1px solid var(--green-bd)}}
.t-amber{{background:var(--amber-bg);color:var(--amber);border:1px solid var(--amber-bd)}}
.t-blue{{background:var(--blue-bg);color:var(--blue);border:1px solid var(--blue-bd)}}
.t-purple{{background:var(--purple-bg);color:var(--purple);border:1px solid var(--purple-bd)}}
.t-gray{{background:var(--surface2);color:var(--text2);border:1px solid var(--border2)}}

/* Stock items */
.group-lbl{{background:var(--surface2);border:1px solid var(--border);border-radius:var(--r-sm);padding:9px 14px;margin:14px 0 8px;font-size:12px;font-weight:700;color:var(--text2)}}
.stk-item{{border-bottom:1px solid var(--border)}}
.stk-item:last-child{{border-bottom:none}}
.stk-top{{display:flex;gap:10px;align-items:flex-start;padding:13px 16px 4px}}
.stk-info{{min-width:0;flex:1}}
.stk-name{{font-size:14px;font-weight:800}}
.stk-meta{{font-size:11px;color:var(--text3);margin-top:1px}}
.stk-tag{{flex-shrink:0}}

/* Metric grid */
.met-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:10px 0}}
.met{{background:var(--surface2);border-radius:6px;padding:7px 10px}}
.met-l{{font-size:10px;color:var(--text3);font-weight:600;margin-bottom:2px}}
.met-v{{font-size:13px;font-weight:700;color:var(--text)}}

/* 52w range */
.rng-wrap{{margin:8px 0 10px}}
.rng-lbl{{display:flex;justify-content:space-between;font-size:10px;color:var(--text3);margin-bottom:3px}}
.rng-track{{height:5px;background:var(--surface2);border-radius:3px;overflow:hidden}}
.rng-fill{{height:5px;background:linear-gradient(to right,var(--red),var(--amber),var(--green));border-radius:3px}}

/* Analyst */
.analyst-row{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:8px 0 4px}}

/* Investor flow */
.inv-section{{background:var(--surface2);border-radius:8px;padding:10px 12px;margin:8px 0}}
.inv-title{{font-size:10px;font-weight:700;color:var(--text3);letter-spacing:.05em;margin-bottom:6px}}
.inv-row{{display:flex;align-items:center;justify-content:space-between;padding:3px 0}}
.inv-who{{font-size:12px;font-weight:600;color:var(--text2)}}

/* Strategy desc */
.strat-desc{{font-size:12px;color:var(--text2);background:var(--surface2);border-radius:6px;padding:7px 10px;margin:6px 0 2px;line-height:1.55}}

/* News */
.news-blk{{margin:0 16px 12px;background:var(--surface2);border-radius:var(--r-sm);padding:9px 12px}}
.news-lbl{{font-size:10px;font-weight:800;color:var(--text3);letter-spacing:.07em;text-transform:uppercase;margin-bottom:6px}}
.news-li{{font-size:12px;color:var(--text);line-height:1.55;padding:3px 0;display:flex;gap:6px;align-items:flex-start}}
.news-li::before{{content:"·";color:var(--text3);flex-shrink:0;font-size:14px;line-height:1.4}}
.news-li a{{color:var(--text);text-decoration:none}}
.news-li a:hover{{text-decoration:underline}}
.news-pub{{font-size:10px;color:var(--text3);white-space:nowrap}}

/* Legend */
.legend-box{{background:var(--surface);border:1px solid var(--border2);border-radius:var(--r);padding:14px 16px;margin-bottom:14px;box-shadow:var(--shadow)}}
.legend-title{{font-size:11px;font-weight:800;color:var(--text3);letter-spacing:.07em;margin-bottom:10px}}
.legend-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
.legend-item{{display:flex;gap:8px;align-items:flex-start}}
.legend-desc{{font-size:11px;color:var(--text2);line-height:1.5;margin-top:2px}}

/* Footer */
.footer{{text-align:center;font-size:11px;color:var(--text3);margin-top:48px;padding-top:18px;border-top:1px solid var(--border2);line-height:1.8}}

/* Mobile */
@media(max-width:520px){{
  .page{{padding:16px 12px 80px}}
  .market-grid{{grid-template-columns:1fr 1fr}}
  .market-grid .market-card:nth-child(5),
  .market-grid .market-card:nth-child(6){{grid-column:span 1}}
  .met-grid{{grid-template-columns:repeat(2,1fr)}}
  .legend-grid{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>
<div class="page">

<div class="header">
  <div class="header-meta">
    <div class="header-date">{DATE_DISPLAY} — 자동 생성 브리핑</div>
    <div class="badge">📊 실시간 데이터</div>
  </div>
  <h1>포트폴리오 투자 브리핑<br><span style="color:var(--text2);font-weight:600">— 22종목 펀더멘털 종합 진단</span></h1>
  <div class="header-sub">미국 13 · 한국 9 · PER/PBR/PSR · 수급 · 애널리스트 · 종합 전략</div>
</div>

<nav class="toc" aria-label="목차">
  <div class="toc-inner">
    <a href="#market">📊 시장</a>
    <a href="#fng">😨 공포탐욕</a>
    <a href="#trades">📒 매매기록</a>
    <a href="#us-stocks">🇺🇸 미국 종목</a>
    <a href="#kr-stocks">🇰🇷 한국 종목</a>
    <a href="#footer">📋 안내</a>
  </div>
</nav>

<!-- ① 시장 요약 -->
<div class="section" id="market">
  <div class="sec-title">시장 지수 — {LAST_BD.strftime('%Y.%m.%d')} 기준 (직전 거래일)</div>
  <div class="market-grid">
    {mkt_card("나스닥 종합지수 (COMP)", f"{nasdaq_c:,.2f}" if nasdaq_c else None, nasdaq_d)}
    {mkt_card("S&P 500", f"{sp500_c:,.2f}" if sp500_c else None, sp500_d)}
    {mkt_card("USD/KRW 환율", f"{usdkrw_v:,.1f}원" if usdkrw_v else None, None)}
    {mkt_card("코스피 (KOSPI)", f"{kospi_c:,.2f}" if kospi_c else None, kospi_d)}
    {mkt_card("코스닥 (KOSDAQ)", f"{kosdaq_c:,.2f}" if kosdaq_c else None, kosdaq_d)}
  </div>
</div>

<!-- ② 공포탐욕지수 -->
{fng_section_html(mkt)}

<!-- ②-b 나의 매매 기록 -->
{trades_section_html(trades)}

<!-- ③ 미국 종목 -->
<div class="section" id="us-stocks">
  <div class="sec-title">🇺🇸 미국 보유 종목 — 13개 · 펀더멘털 종합 진단</div>
  {legend_html}
  <div class="card">
    {us_cards_html}
  </div>
</div>

<!-- ④ 한국 종목 -->
<div class="section" id="kr-stocks">
  <div class="sec-title">🇰🇷 한국 보유 종목 — 9개 · PER/PBR · 외인기관개인 수급</div>
  <div class="card">
    {kr_cards_html}
  </div>
</div>

<div class="footer" id="footer">
  포트폴리오 투자 브리핑 · {TODAY.strftime('%Y년 %m월 %d일')} · GitHub Actions 자동 생성<br>
  데이터: yfinance (미국) · pykrx (한국) · CNN Fear &amp; Greed<br>
  이 브리핑은 투자 참고용입니다. 최종 투자 결정은 본인 판단으로 하세요.
</div>

</div>
</body>
</html>"""


# ═══════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════
def main():
    print("=" * 60)
    print(f"[START] Daily Portfolio Briefing — {TODAY_STR}")
    print("=" * 60)

    # 1. Market data
    print("\n[STEP 1] Fetching market data...")
    mkt = fetch_market()

    # 2. US stocks
    print("\n[STEP 2] Fetching US stocks...")
    us_data = []
    for ticker, name, qty, avg in US_PORTFOLIO:
        s = fetch_us_stock(ticker)
        us_data.append(s)

    # 3. KR stocks
    print("\n[STEP 3] Fetching KR stocks...")
    kr_data = []
    for code, name, qty, avg in KR_PORTFOLIO:
        s = fetch_kr_stock(code, name)
        kr_data.append(s)

    # 4. Build HTML
    print("\n[STEP 4] Building HTML...")
    trades = load_trades()
    print(f"[STEP 4] Trades loaded: {len(trades)}")
    html = build_html(mkt, us_data, kr_data, trades)

    # 5. Write output (+ 진단 로그를 주석으로 포함 — Actions 로그 접근 불가 환경 대비)
    out_path = "index.html"
    try:
        with open("debug_fetch.log", encoding="utf-8") as df:
            dbg = df.read().replace("--", "- -")
        html += f"\n<!-- DEBUG_FETCH\n{dbg}\n-->\n"
    except Exception:
        pass
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] Written: {out_path} ({len(html):,} chars)")
    print("[DONE]")


if __name__ == "__main__":
    main()
