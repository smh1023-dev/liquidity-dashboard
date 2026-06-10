# -*- coding: utf-8 -*-
"""
비트코인 & 주식시장 거시 유동성 분석 대시보드 (app.py)
======================================================

6개 핵심 거시 유동성 지표 + 비트코인 200일 이동평균선을 자동 수집하여,
현재 시장 환경이 위험자산(비트코인·나스닥)에 긍정/중립/부정인지
초보자도 이해할 수 있게 한글로 설명하는 Streamlit 대시보드입니다.

실행:
    pip install -r requirements.txt
    streamlit run app.py

데이터 소스(견고한 다층 폴백):
    - FRED 시계열: 공개 CSV 엔드포인트(키 불필요) → pandas_datareader 폴백
      (FRED_API_KEY 가 환경변수/secrets 에 있으면 공식 API 를 우선 사용)
    - DXY / BTC: yfinance

※ 본 도구는 투자 추천이 아니라 '시장 환경 판단' 보조 도구입니다.
"""
from __future__ import annotations

import io
import os
from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go

# ─────────────────────────────────────────────────────────────────────────────
# 기본 설정 / 상수
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="거시 유동성 대시보드", page_icon="💧", layout="wide")

HISTORY_YEARS = 3.3                       # 200일선 + 3년 차트를 위해 넉넉히 수집
START_DATE = (datetime.now() - timedelta(days=int(365 * HISTORY_YEARS))).strftime("%Y-%m-%d")

# 기간 선택 → 표시할 일수
PERIOD_DAYS = {"3개월": 90, "6개월": 180, "1년": 365, "3년": 365 * 3}

# 색상 팔레트
C_GREEN, C_YELLOW, C_RED = "#22c55e", "#eab308", "#ef4444"
C_LINE, C_MA = "#38bdf8", "#f59e0b"
PLOT_TEMPLATE = "plotly_dark"

# 지표 메타데이터
#   good_when_up: 값이 오를 때 위험자산에 '긍정'이면 True
#   weight: 점수 가중치(절대값)
#   unit: 'usd_b'(달러, 십억 단위로 정규화) / 'pct'(금리·지수)
INDICATORS = {
    "WALCL": {
        "kr": "연준 돈 수도꼭지 (Fed Balance Sheet)",
        "good_when_up": True, "weight": 1, "unit": "usd_b", "fmt": "trillion",
        "desc": "연준의 돈 수도꼭지입니다. 증가하면 시장에 돈이 풀리는 방향이고, "
                "감소하면 돈을 회수하는 방향입니다.",
    },
    "WTREGEN": {
        "kr": "미국 정부 통장 (TGA)",
        "good_when_up": False, "weight": 1, "unit": "usd_b", "fmt": "billion",
        "desc": "미국 정부의 통장입니다. TGA가 증가하면 돈이 정부 통장에 쌓여 시장에서 "
                "빠져나간 것이고, 감소하면 정부가 돈을 써서 시장에 유동성이 공급된 것입니다.",
    },
    "RRPONTSYD": {
        "kr": "연준 돈 주차장 (RRP)",
        "good_when_up": False, "weight": 1, "unit": "usd_b", "fmt": "billion",
        "desc": "연준의 돈 주차장입니다. RRP가 증가하면 돈이 시장이 아니라 Fed에 주차된 "
                "것이고, 감소하면 돈이 시장으로 나올 가능성이 커집니다.",
    },
    "M2SL": {
        "kr": "미국 돈의 총량 (M2)",
        "good_when_up": True, "weight": 2, "unit": "usd_b", "fmt": "trillion",
        "desc": "미국 안에 풀린 돈의 양입니다. 돈이 많아지면 주식과 비트코인을 살 여력이 커집니다.",
    },
    "DXY": {
        "kr": "달러의 힘 (DXY)",
        "good_when_up": False, "weight": 1, "unit": "pct", "fmt": "index",
        "desc": "달러의 힘입니다. 달러가 강하면 위험자산에 불리하고, 달러가 약하면 "
                "비트코인과 주식에 유리합니다.",
    },
    "DGS10": {
        "kr": "안전자산 이자율 (미국 10년물 금리)",
        "good_when_up": False, "weight": 1, "unit": "pct", "fmt": "rate",
        "desc": "안전한 국채 이자의 대표 지표입니다. 금리가 높으면 비트코인보다 채권이 "
                "매력적이어서 위험자산에 부담이 됩니다.",
    },
}

BTC_DESC = ("비트코인의 장기 추세선입니다. 가격이 200일선 위에 있으면 상승 추세, "
            "아래에 있으면 하락 추세로 봅니다.")
NETLIQ_DESC = ("Fed가 푼 돈에서 정부 통장과 Fed 주차장에 묶인 돈을 뺀 값입니다. "
               "실제 시장에 남아 있는 돈의 양을 보는 지표입니다.")

# 추가: 대표 ETF 추세 (200일선) — 가격 추세 현황(거시 점수와 별개)
ETFS = {
    "QQQ": {
        "kr": "미국 나스닥100 (QQQ)", "symbol": "QQQ", "ccy": "USD",
        "desc": "미국 기술주 대표 지수입니다. 가격이 200일선 위면 상승 추세, 아래면 하락 추세로 봅니다.",
    },
    "SOXX": {
        "kr": "미국 반도체 (SOXX)", "symbol": "SOXX", "ccy": "USD",
        "desc": "미국 반도체 대표 지수입니다. 위험자산 심리의 선행 지표로 자주 쓰입니다. "
                "200일선 위면 상승 추세입니다.",
    },
    "KODEX200": {
        "kr": "한국 코스피200 (KODEX 200)", "symbol": "069500.KS", "ccy": "KRW",
        "desc": "한국 증시 대표 지수입니다. 200일선 위면 상승 추세, 아래면 하락 추세로 봅니다.",
    },
    "GLD": {
        "kr": "금 (GLD)", "symbol": "GLD", "ccy": "USD",
        "desc": "금 가격을 따르는 대표 ETF입니다. 안전자산이자 인플레이션 방어 수단으로 봅니다. "
                "200일선 위면 상승 추세입니다.",
    },
    "USO": {
        "kr": "석유/원유 (USO)", "symbol": "USO", "ccy": "USD",
        "desc": "WTI 원유 가격을 따르는 ETF입니다. 경기와 물가의 영향을 크게 받습니다. "
                "200일선 위면 상승 추세입니다.",
    },
    "TLT": {
        "kr": "미국 장기채 (TLT)", "symbol": "TLT", "ccy": "USD",
        "desc": "미국 20년+ 국채 ETF입니다. 금리가 내리면 오르고, 금리가 오르면 내립니다. "
                "200일선 위면 상승 추세입니다.",
    },
}

# 차트 제목 (한글 별명 + 실제 지표명 병기)
CHART_TITLES = {
    "WALCL": "연준 돈 수도꼭지 (Fed Balance Sheet)",
    "WTREGEN": "미국 정부 통장 (TGA)",
    "RRPONTSYD": "연준 돈 주차장 (RRP)",
    "M2SL": "미국 돈의 총량 (M2)",
    "DXY": "달러의 힘 (DXY)",
    "DGS10": "안전자산 이자율 (US 10Y)",
    "BTC": "비트코인 장기 추세 (BTC + 200일선)",
    "NETLIQ": "실제 시장 유동성 (Net Liquidity)",
}


# ─────────────────────────────────────────────────────────────────────────────
# 1) 데이터 수집 — FRED
# ─────────────────────────────────────────────────────────────────────────────
def _fred_via_csv(series_id: str, start: str) -> pd.Series:
    """FRED 공개 CSV 엔드포인트 (API 키 불필요). 재시도 + 대체 URL."""
    urls = [
        f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}",
        f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}",
    ]
    headers = {"User-Agent": "Mozilla/5.0 (compatible; liquidity-dashboard/1.0)"}
    last_err = None
    for url in urls:
        for _ in range(2):  # 가벼운 재시도
            try:
                r = requests.get(url, timeout=25, headers=headers)
                r.raise_for_status()
                df = pd.read_csv(io.StringIO(r.text))
                date_col = df.columns[0]               # DATE 또는 observation_date
                val_col = df.columns[-1]
                df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
                df[val_col] = pd.to_numeric(df[val_col], errors="coerce")  # '.' → NaN
                s = df.dropna(subset=[date_col]).set_index(date_col)[val_col].dropna()
                s.index = s.index.tz_localize(None)
                s.name = series_id
                if not s.empty:
                    return s
            except Exception as e:
                last_err = e
    if last_err:
        raise last_err
    return pd.Series(dtype=float, name=series_id)


def _fred_via_api(series_id: str, start: str, api_key: str) -> pd.Series:
    """공식 FRED API (키가 있을 때 우선 사용)."""
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {"series_id": series_id, "api_key": api_key, "file_type": "json",
              "observation_start": start}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    obs = r.json().get("observations", [])
    dates = pd.to_datetime([o["date"] for o in obs], errors="coerce")
    vals = pd.to_numeric([o["value"] for o in obs], errors="coerce")
    s = pd.Series(vals, index=dates, name=series_id).dropna()
    s.index = s.index.tz_localize(None)
    return s


def _fred_via_pdr(series_id: str, start: str) -> pd.Series:
    """pandas_datareader 폴백."""
    from pandas_datareader import data as pdr
    df = pdr.DataReader(series_id, "fred", start)
    s = df[series_id].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    s.name = series_id
    return s


def _get_fred_api_key() -> str:
    """환경변수 → st.secrets 순으로 FRED API 키를 안전하게 조회 (없으면 '')."""
    key = os.environ.get("FRED_API_KEY", "")
    if key:
        return key
    try:                                    # secrets.toml 이 없으면 예외 → 무시
        return st.secrets.get("FRED_API_KEY", "")
    except Exception:
        return ""


@st.cache_data(ttl=300, show_spinner=False)
def fred_diagnostics() -> dict:
    """FRED 연결 상태 진단: 키 인식 여부 + API/CSV 응답 결과."""
    key = _get_fred_api_key()
    info = {"key_present": bool(key), "key_len": len(key) if key else 0,
            "api": "미시도", "csv": "미시도"}
    # API
    if key:
        try:
            s = _fred_via_api("WALCL", START_DATE, key)
            info["api"] = f"성공 ({len(s)}건)" if not s.empty else "빈 응답"
        except Exception as e:
            info["api"] = f"실패: {type(e).__name__}"
    # CSV
    try:
        s = _fred_via_csv("WALCL", START_DATE)
        info["csv"] = f"성공 ({len(s)}건)" if not s.empty else "빈 응답"
    except Exception as e:
        info["csv"] = f"실패: {type(e).__name__}"
    return info


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_fred_data(series_id: str, start: str = START_DATE) -> pd.Series:
    """단일 FRED 시계열 수집 (다층 폴백). 실패 시 빈 Series."""
    api_key = _get_fred_api_key()
    # 1순위: 키가 있으면 공식 API
    if api_key:
        try:
            s = _fred_via_api(series_id, start, api_key)
            if not s.empty:
                return s
        except Exception:
            pass
    # 2순위: 공개 CSV
    try:
        s = _fred_via_csv(series_id, start)
        if not s.empty:
            return s
    except Exception:
        pass
    # 3순위: pandas_datareader
    try:
        return _fred_via_pdr(series_id, start)
    except Exception:
        return pd.Series(dtype=float, name=series_id)


# ─────────────────────────────────────────────────────────────────────────────
# 2) 데이터 수집 — yfinance (DXY, BTC)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=3 * 3600, show_spinner=False)
def fetch_yfinance_data(symbol: str, start: str = START_DATE) -> pd.Series:
    """yfinance 종가 시계열 (DXY: DX-Y.NYB, BTC: BTC-USD). 실패 시 빈 Series."""
    try:
        import yfinance as yf
        df = yf.Ticker(symbol).history(start=start, interval="1d", auto_adjust=False)
        if df.empty:
            # 폴백: download API
            df = yf.download(symbol, start=start, interval="1d",
                             progress=False, auto_adjust=False)
        if df.empty:
            return pd.Series(dtype=float, name=symbol)
        s = df["Close"].dropna()
        if isinstance(s, pd.DataFrame):           # 멀티컬럼 방어
            s = s.iloc[:, 0]
        s.index = pd.to_datetime(s.index).tz_localize(None)
        s.name = symbol
        return s
    except Exception:
        return pd.Series(dtype=float, name=symbol)


# ─────────────────────────────────────────────────────────────────────────────
# 3) 정규화 / 파생 지표
# ─────────────────────────────────────────────────────────────────────────────
def normalize_usd_billions(series_id: str, s: pd.Series) -> pd.Series:
    """
    달러 시계열을 '십억 달러(Billion)' 단위로 통일.

    FRED 시리즈마다 단위가 다름:
      - WALCL(연준 자산), WTREGEN(TGA) → 백만 달러(Millions)
      - RRPONTSYD(RRP), M2SL(M2)       → 십억 달러(Billions)
    하드코딩 대신 최신값의 자릿수로 자동 판별한다.
      · 최신값이 100,000 이상이면 백만 단위로 보고 1000으로 나눠 십억으로 변환
        (미국 어떤 집계도 10만 십억 달러 = 10경 달러에 이르지 않으므로 안전)
      · 그보다 작으면 이미 십억 단위로 간주
    """
    if s is None or s.dropna().empty:
        return s
    latest = abs(float(s.dropna().iloc[-1]))
    if latest >= 100_000:          # 백만 달러 단위 → 십억으로
        return s / 1000.0
    return s                       # 이미 십억 달러 단위


def compute_net_liquidity(walcl_b: pd.Series, tga_b: pd.Series,
                          rrp_b: pd.Series) -> pd.Series:
    """Net Liquidity = Fed Balance Sheet - TGA - RRP (모두 십억 달러 기준)."""
    if walcl_b.empty or tga_b.empty or rrp_b.empty:
        return pd.Series(dtype=float, name="NETLIQ")
    df = pd.concat(
        [walcl_b.rename("walcl"), tga_b.rename("tga"), rrp_b.rename("rrp")],
        axis=1,
    ).sort_index().ffill().dropna()
    net = (df["walcl"] - df["tga"] - df["rrp"]).rename("NETLIQ")
    return net


def compute_btc_ma(btc: pd.Series, window: int = 200) -> pd.Series:
    """비트코인 N일 이동평균."""
    if btc.empty:
        return pd.Series(dtype=float)
    return btc.rolling(window, min_periods=max(20, window // 4)).mean()


# ─────────────────────────────────────────────────────────────────────────────
# 4) 변화율 계산
# ─────────────────────────────────────────────────────────────────────────────
def calculate_change(s: pd.Series, months: int = 3) -> dict:
    """
    최근 값 대비 약 'months'개월 전 값의 변화.
    반환: {current, past, delta, pct, ok}
    """
    if s is None or s.dropna().empty:
        return {"current": None, "past": None, "delta": None, "pct": None, "ok": False}
    s = s.dropna()
    last_date = s.index[-1]
    target = last_date - timedelta(days=months * 30)
    prior = s.loc[:target]
    past_val = float(prior.iloc[-1]) if not prior.empty else float(s.iloc[0])
    cur_val = float(s.iloc[-1])
    delta = cur_val - past_val
    pct = (delta / past_val * 100) if past_val not in (0, None) else None
    return {"current": cur_val, "past": past_val, "delta": delta, "pct": pct, "ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# 5) 점수화
# ─────────────────────────────────────────────────────────────────────────────
def _sign(x: float | None) -> int:
    if x is None or x == 0:
        return 0
    return 1 if x > 0 else -1


def calculate_score(changes: dict, btc_above_ma: bool | None,
                    netliq_change: dict) -> tuple[int, dict]:
    """
    스펙의 점수화 로직을 그대로 적용.
    반환: (총점, {metric: {'score': int, 'status': '긍정/중립/부정', 'emoji': ...}})
    """
    breakdown = {}

    def status(score: int) -> tuple[str, str]:
        if score > 0:
            return "긍정", "🟢"
        if score < 0:
            return "부정", "🔴"
        return "중립", "🟡"

    # 6개 거시 지표
    for sid, meta in INDICATORS.items():
        ch = changes.get(sid, {})
        sgn = _sign(ch.get("delta"))
        # good_when_up=True 면 상승이 +, False 면 하락이 +
        direction = 1 if meta["good_when_up"] else -1
        score = meta["weight"] * sgn * direction
        st_txt, emoji = status(score)
        breakdown[sid] = {"score": score, "status": st_txt, "emoji": emoji}

    # 비트코인 200일선
    if btc_above_ma is None:
        btc_score = 0
    else:
        btc_score = 2 if btc_above_ma else -2
    st_txt, emoji = status(btc_score)
    breakdown["BTC"] = {"score": btc_score, "status": st_txt, "emoji": emoji}

    # Net Liquidity (가중치 2)
    nl_sgn = _sign(netliq_change.get("delta"))
    nl_score = 2 * nl_sgn
    st_txt, emoji = status(nl_score)
    breakdown["NETLIQ"] = {"score": nl_score, "status": st_txt, "emoji": emoji}

    total = sum(v["score"] for v in breakdown.values())
    return total, breakdown


# ─────────────────────────────────────────────────────────────────────────────
# 6) 종합 해석
# ─────────────────────────────────────────────────────────────────────────────
def generate_interpretation(total: int) -> dict:
    """최종 점수 → 판정 라벨/색/한 줄 설명."""
    if total >= 7:
        return {"label": "강세장 가능성 높음", "color": C_GREEN, "emoji": "🟢",
                "line": "거시 지표 기준으로는 위험자산에 우호적인 환경에 가깝습니다."}
    if total >= 3:
        return {"label": "중립 ~ 강세", "color": "#84cc16", "emoji": "🟢",
                "line": "거시 지표 기준으로는 약간 우호적인 쪽에 가깝습니다."}
    if total >= -2:
        return {"label": "중립", "color": C_YELLOW, "emoji": "🟡",
                "line": "거시 지표 기준으로는 뚜렷한 방향이 없는 중립 구간에 가깝습니다."}
    if total >= -6:
        return {"label": "방어 필요", "color": "#f97316", "emoji": "🟠",
                "line": "거시 지표 기준으로는 신중함이 필요한 환경에 가깝습니다."}
    return {"label": "위험 회피 구간", "color": C_RED, "emoji": "🔴",
            "line": "거시 지표 기준으로는 위험자산에 불리한 환경에 가깝습니다."}


def generate_report(total: int, breakdown: dict, changes: dict,
                    netliq_change: dict, btc_above_ma: bool | None) -> str:
    """긍정/부정 요인을 모아 한글 리포트 문장을 생성."""
    pos, neg = [], []
    label_map = {
        "WALCL": "Fed Balance Sheet", "WTREGEN": "TGA", "RRPONTSYD": "RRP",
        "M2SL": "M2", "DXY": "달러(DXY)", "DGS10": "10년물 금리",
        "NETLIQ": "Net Liquidity", "BTC": "비트코인 추세",
    }
    move = {  # (good_when_up, delta_sign) → 표현
        "WALCL": ("증가", "감소"), "WTREGEN": ("증가", "감소"),
        "RRPONTSYD": ("증가", "감소"), "M2SL": ("증가", "감소"),
        "DXY": ("상승", "하락"), "DGS10": ("상승", "하락"),
        "NETLIQ": ("증가", "감소"),
    }

    for sid in list(INDICATORS) + ["NETLIQ"]:
        ch = changes.get(sid) if sid in changes else netliq_change
        d = ch.get("delta") if ch else None
        if d is None or d == 0:
            continue
        up_word, down_word = move[sid]
        word = up_word if d > 0 else down_word
        phrase = f"{label_map[sid]} {word}"
        if breakdown[sid]["score"] > 0:
            pos.append(phrase)
        elif breakdown[sid]["score"] < 0:
            neg.append(phrase)

    if btc_above_ma is True:
        pos.append("비트코인 200일선 위")
    elif btc_above_ma is False:
        neg.append("비트코인 200일선 아래")

    interp = generate_interpretation(total)
    parts = [f"현재 거시 유동성 점수는 {'+' if total >= 0 else ''}{total}점입니다."]
    if pos:
        parts.append(f"{', '.join(pos)}는 위험자산에 긍정적인 요인입니다.")
    if neg:
        parts.append(f"반면 {', '.join(neg)}는 부담 요인입니다.")
    parts.append(f"종합적으로 {interp['line']}")
    return " ".join(parts)


# ═════════════════════════════════════════════════════════════════════════════
#  매크로 분석 엔진 (Actionable Insight)
#  ※ 투자 추천이 아니라 '거시 환경 시그널'. 국채시장이 반영한 기대치 기반 추정.
# ═════════════════════════════════════════════════════════════════════════════
def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# ── (4) 유동성 종합 점수 0~100 ──────────────────────────────────────────────
def liquidity_score_100(breakdown: dict) -> dict:
    """
    6개 거시 지표(Fed BS·TGA·RRP·M2·DXY·10Y)의 방향 점수를 0~100으로 환산.
    개별 점수 합 범위 [-7, +7] → [0, 100].
    """
    keys = ["WALCL", "WTREGEN", "RRPONTSYD", "M2SL", "DXY", "DGS10"]
    raw = sum(breakdown.get(k, {}).get("score", 0) for k in keys)
    score = round((raw + 7) / 14 * 100)
    score = max(0, min(100, score))
    if score >= 70:
        label, color = "완화적 (유동성 우호)", C_GREEN
    elif score >= 55:
        label, color = "다소 완화적", "#84cc16"
    elif score >= 45:
        label, color = "중립", C_YELLOW
    elif score >= 30:
        label, color = "다소 긴축적", "#f97316"
    else:
        label, color = "긴축적 (유동성 비우호)", C_RED
    return {"score": score, "label": label, "color": color, "raw": raw}


# ── (2) 금리 일기예보 ───────────────────────────────────────────────────────
def analyze_rates(dgs2: pd.Series, dgs10: pd.Series, t10y3m: pd.Series) -> dict:
    """
    DGS2(2년)·DGS10(10년)·T10Y3M(장단기차)로 시장의 금리 기대를 추정.
    2년물은 향후 1~2년 정책금리 기대를 가장 잘 반영한다.
      - 2년물 하락 → 시장이 인하를 기대(완화)
      - 2년물 보합 → 동결 기대
      - 2년물 상승 → 긴축/고금리 지속 기대
    """
    ch2_3m = calculate_change(dgs2, months=3)
    ch2_1m = calculate_change(dgs2, months=1)
    d3 = ch2_3m.get("delta")
    d1 = ch2_1m.get("delta")
    cur2 = ch2_3m.get("current")
    cur10 = calculate_change(dgs10, months=3).get("current")
    cur_spread = calculate_change(t10y3m, months=3).get("current")

    if d3 is None:
        return {"ok": False, "label": "데이터 없음", "emoji": "⚪",
                "view_3m": "—", "view_6m": "—", "text": "금리 데이터를 불러오지 못했습니다.",
                "dgs2": cur2, "dgs10": cur10, "spread": cur_spread}

    # 완화/동결/긴축 판정 (3개월 2년물 변화, %p 기준)
    if d3 <= -0.20:
        label, emoji, bias = "완화 기대 (인하 기대)", "🟢", "ease"
    elif d3 >= 0.20:
        label, emoji, bias = "긴축 기대 (고금리 지속)", "🔴", "tight"
    else:
        label, emoji, bias = "동결 기대 (관망)", "🟡", "hold"

    # 방향성 (최근 1개월 모멘텀으로 가속/둔화 판단)
    def momentum_word(short, long_):
        if short is None:
            return "방향 불확실"
        if abs(short) < 0.05:
            return "추세 둔화·횡보 가능"
        if (short < 0) == (long_ < 0):
            return "현 추세 지속 가능"
        return "추세 전환 조짐"

    mom = momentum_word(d1, d3)
    if bias == "ease":
        view_3m = "추가 하락(인하 기대 강화) 쪽에 무게"
        view_6m = "완만한 인하 사이클 진입 가능성"
    elif bias == "tight":
        view_3m = "추가 상승(고금리 지속) 쪽에 무게"
        view_6m = "고금리 장기화 또는 추가 긴축 경계"
    else:
        view_3m = "현 수준 부근 박스권 가능성"
        view_6m = "데이터 확인 후 방향 결정될 구간"

    spread_txt = ""
    if cur_spread is not None:
        if cur_spread < 0:
            spread_txt = f"장단기 금리차(10년-3개월)는 {cur_spread:.2f}%p로 역전 상태입니다."
        else:
            spread_txt = f"장단기 금리차(10년-3개월)는 +{cur_spread:.2f}%p로 정상(우상향)입니다."

    text = (f"2년물 금리가 최근 3개월간 {'+' if d3 >= 0 else ''}{d3:.2f}%p 변했습니다. "
            f"시장은 {label.split(' (')[0]}를 반영하고 있으며({mom}), {spread_txt}")

    return {"ok": True, "label": label, "emoji": emoji, "bias": bias,
            "view_3m": view_3m, "view_6m": view_6m, "momentum": mom, "text": text,
            "dgs2": cur2, "dgs10": cur10, "spread": cur_spread, "d3": d3}


# ── (3) 경기 사이클 + 침체 확률 ─────────────────────────────────────────────
def analyze_cycle(t10y3m: pd.Series) -> dict:
    """
    장단기 금리차(T10Y3M) 수준과 추세로 경기 사이클 단계를 추정.
      level(역전/정상) × trend(가팔라짐/평탄해짐) 4분면.
    침체 확률은 금리차 기반 로지스틱 근사치(NY Fed 공식 모델 아님).
    """
    ch0 = calculate_change(t10y3m, months=3)
    if not ch0.get("ok"):
        return {"ok": False, "stage": "데이터 없음", "emoji": "⚪", "color": C_YELLOW,
                "recession_prob": None, "spread": None, "desc": "금리차 데이터 없음."}
    spread = ch0["current"]
    trend = ch0["delta"]   # +면 가팔라짐(steepening), -면 평탄/심화

    inverted = spread < 0
    steepening = trend > 0.02
    flattening = trend < -0.02

    if not inverted and steepening:
        stage, emoji, color = "회복 / 확장", "🟢", C_GREEN
        desc = "금리차가 정상이고 가팔라지는 중 — 경기 확장에 우호적 신호입니다."
    elif not inverted and flattening:
        stage, emoji, color = "확장 후반 / 둔화 진입", "🟡", C_YELLOW
        desc = "금리차가 정상이나 평탄해지는 중 — 성장 둔화 가능성을 살필 구간입니다."
    elif inverted and flattening:
        stage, emoji, color = "둔화 / 침체 위험 누적", "🔴", C_RED
        desc = "금리차가 역전·심화 중 — 침체 선행 신호가 누적되는 구간입니다."
    elif inverted and steepening:
        stage, emoji, color = "침체 임박 / 전환 구간", "🟠", "#f97316"
        desc = "역전 상태에서 다시 가팔라지는 중 — 역사적으로 침체 직전·직후에 자주 나타납니다."
    else:
        stage, emoji, color = "중립", "🟡", C_YELLOW
        desc = "뚜렷한 사이클 신호가 약한 구간입니다."

    # 침체 확률 근사: 금리차에 대한 로지스틱 (앵커: -1.0%p≈70%, 0≈30%, +1.5%p≈8%)
    import math
    prob = 1.0 / (1.0 + math.exp(2.2 * (spread + 0.45)))
    prob = round(max(0.02, min(0.95, prob)) * 100)

    return {"ok": True, "stage": stage, "emoji": emoji, "color": color,
            "recession_prob": prob, "spread": spread, "trend": trend, "desc": desc}


# ── (1) 자산별 환경 시그널 ──────────────────────────────────────────────────
# 각 자산이 받는 거시 요인 가중치 (합이 대략 1이 되도록). 부호는 '해당 요인이 우호적일 때 +'.
ASSET_FACTORS = {
    "BTC":  {"liq": 0.35, "rate": 0.15, "dollar": 0.25, "trend": 0.25},
    "QQQ":  {"liq": 0.30, "rate": 0.30, "dollar": 0.10, "trend": 0.30},
    "SOXX": {"liq": 0.25, "rate": 0.25, "dollar": 0.10, "trend": 0.40},
    "GLD":  {"liq": 0.20, "rate": 0.25, "dollar": 0.40, "trend": 0.15},
    "TLT":  {"liq": 0.15, "rate": 0.65, "dollar": 0.05, "trend": 0.15},
}
FACTOR_KR = {"liq": "유동성", "rate": "금리(완화)", "dollar": "달러(약세)", "trend": "200일선 추세"}
ASSET_KR = {"BTC": "비트코인", "QQQ": "나스닥100(QQQ)", "SOXX": "반도체(SOXX)",
            "GLD": "금(GLD)", "TLT": "장기채(TLT)", "CASH": "현금/MMF (Cash)"}


def build_macro_factors(liq: dict, rates: dict, dxy_change: dict) -> dict:
    """공통 거시 요인을 -1~+1 '우호도'로 환산."""
    f_liq = _clip((liq["score"] - 50) / 50.0)                      # 유동성 점수
    # 금리: 2년물 3개월 하락(=완화)이 우호 → -delta
    d3 = rates.get("d3")
    f_rate = _clip(-(d3 or 0) / 0.5)                               # 0.5%p ≈ 만점
    # 달러: DXY 3개월 하락(약달러)이 우호 → -pct
    dxy_pct = dxy_change.get("pct")
    f_dollar = _clip(-(dxy_pct or 0) / 5.0)                        # 5% ≈ 만점
    return {"liq": f_liq, "rate": f_rate, "dollar": f_dollar}


def asset_signal(asset_key: str, factors: dict, trend_above: bool | None,
                trend_dist: float | None) -> dict:
    """
    자산별 환경 시그널 산출.
    반환: stance(우호적/중립/비우호적), score(-1~1), confidence, top3 근거, 3개월·6개월 전망
    """
    w = ASSET_FACTORS[asset_key]
    # 추세 요인: 200일선과의 이격(%)을 -1~1로
    if trend_above is None:
        f_trend = 0.0
    elif trend_dist is not None:
        f_trend = _clip(trend_dist / 0.10)        # 10% 이격 ≈ 만점
    else:
        f_trend = 1.0 if trend_above else -1.0

    f = {"liq": factors["liq"], "rate": factors["rate"],
         "dollar": factors["dollar"], "trend": f_trend}
    # 가중 합성 (TLT는 금리 하락이 우호인데 f['rate']가 이미 '완화=+'라 그대로 적용)
    contrib = {k: w[k] * f[k] for k in w}
    score = sum(contrib.values())   # 대략 -1~+1

    if score >= 0.20:
        stance, emoji, color = "우호적", "🟢", C_GREEN
    elif score <= -0.20:
        stance, emoji, color = "비우호적", "🔴", C_RED
    else:
        stance, emoji, color = "중립", "🟡", C_YELLOW

    # 신뢰도: 주요 요인들이 같은 방향을 보면 높음
    sig = [1 if contrib[k] > 0.02 else (-1 if contrib[k] < -0.02 else 0) for k in w]
    nonzero = [x for x in sig if x != 0]
    if nonzero:
        agree = abs(sum(nonzero)) / len(nonzero)   # 0~1
    else:
        agree = 0
    if agree >= 0.8 and len(nonzero) >= 3:
        conf = "높음"
    elif agree >= 0.5:
        conf = "중간"
    else:
        conf = "낮음"

    # 핵심 근거 3개 (기여도 절대값 상위)
    ranked = sorted(w.keys(), key=lambda k: abs(contrib[k]), reverse=True)[:3]
    top3 = []
    for k in ranked:
        v = contrib[k]
        arrow = "우호" if v > 0 else ("비우호" if v < 0 else "중립")
        top3.append(f"{FACTOR_KR[k]} {arrow}")

    # 전망 텍스트
    momentum_good = f_trend > 0
    if stance == "우호적":
        view_3m = "추세·거시 동반 우호 — 환경상 강세 쪽에 가깝습니다." if momentum_good \
            else "거시는 우호적이나 추세 회복 확인이 필요합니다."
        view_6m = "유동성 흐름이 유지되면 우호적 환경 지속 가능."
    elif stance == "비우호적":
        view_3m = "추세·거시 동반 부담 — 환경상 약세 쪽에 가깝습니다." if not momentum_good \
            else "추세는 버티나 거시 역풍이 부담입니다."
        view_6m = "거시 역풍이 풀리기 전까지 신중함이 필요한 구간."
    else:
        view_3m = "방향성이 약한 중립 구간 — 변동성 확대 가능."
        view_6m = "유동성·금리 방향이 잡히면 추세가 결정될 전망."

    return {"stance": stance, "emoji": emoji, "color": color, "score": score,
            "confidence": conf, "top3": top3, "view_3m": view_3m, "view_6m": view_6m}


def cash_signal(risk_asset_signals: dict, dgs2_val: float | None,
                weather: dict) -> dict:
    """
    현금/MMF 환경 시그널 (가격·200일선 없음).
    현금은 위험자산의 '반대' + 단기금리 수준으로 매력도가 결정된다.
      - 위험자산 평균 환경이 비우호적(Risk-Off)일수록 현금 우호
      - 2년물 금리가 높을수록 현금 보유 수익(이자) 매력 가산
    """
    # 위험자산 평균 점수(현금 제외) → 반대 부호
    vals = [s["score"] for k, s in risk_asset_signals.items() if k != "CASH"]
    avg_risk = sum(vals) / len(vals) if vals else 0.0
    f_riskoff = _clip(-avg_risk * 1.3)              # 위험자산 약세 = 현금 우호
    # 단기금리: 4%를 중립, 5.5%↑ 만점. 0%면 -1
    if dgs2_val is not None:
        f_rate = _clip((dgs2_val - 4.0) / 1.5)
    else:
        f_rate = 0.0
    score = 0.65 * f_riskoff + 0.35 * f_rate

    if score >= 0.20:
        stance, emoji, color = "우호적", "🟢", C_GREEN
    elif score <= -0.20:
        stance, emoji, color = "비우호적", "🔴", C_RED
    else:
        stance, emoji, color = "중립", "🟡", C_YELLOW

    # 신뢰도: 두 요인이 같은 방향이면 높음
    sig = [1 if f_riskoff > 0.05 else (-1 if f_riskoff < -0.05 else 0),
           1 if f_rate > 0.05 else (-1 if f_rate < -0.05 else 0)]
    nz = [x for x in sig if x != 0]
    if nz and abs(sum(nz)) == len(nz) and len(nz) == 2:
        conf = "높음"
    elif nz:
        conf = "중간"
    else:
        conf = "낮음"

    # Top Drivers
    top3 = []
    top3.append("위험자산 약세(Risk-Off)" if f_riskoff > 0 else
                "위험자산 강세(Risk-On)" if f_riskoff < 0 else "위험자산 중립")
    rate_txt = f"단기금리 {dgs2_val:.2f}%" if dgs2_val is not None else "단기금리"
    top3.append(f"{rate_txt} {'(이자 매력)' if f_rate > 0 else '(낮음)' if f_rate < 0 else ''}".strip())
    top3.append(f"시장 날씨 {weather.get('label','—')}")

    if stance == "우호적":
        view_3m = "위험자산이 부담받는 국면 — 현금의 방어·대기 매력이 큰 구간."
        view_6m = "금리가 유지되면 현금 보유 비용이 낮아 대기 자금으로 유리."
    elif stance == "비우호적":
        view_3m = "위험선호가 강한 국면 — 현금 보유의 기회비용이 큰 구간."
        view_6m = "유동성 우호가 지속되면 현금보다 위험자산이 유리할 전망."
    else:
        view_3m = "뚜렷한 우열이 없는 중립 — 분산·관망에 무난한 구간."
        view_6m = "금리·유동성 방향이 잡히면 현금 매력도 재평가될 전망."

    return {"stance": stance, "emoji": emoji, "color": color, "score": score,
            "confidence": conf, "top3": top3, "view_3m": view_3m, "view_6m": view_6m}
def generate_briefing(liq: dict, rates: dict, cycle: dict,
                      changes: dict, breakdown: dict) -> str:
    """라이브 데이터를 읽어 한 문단의 거시 브리핑을 작성(규칙 기반)."""
    pos, neg = [], []
    name = {"WALCL": "유동성(연준 자산)", "WTREGEN": "정부통장(TGA)", "RRPONTSYD": "RRP",
            "M2SL": "M2", "DXY": "달러", "DGS10": "장기금리"}
    word = {"WALCL": ("개선", "축소"), "WTREGEN": ("증가", "감소"),
            "RRPONTSYD": ("증가", "감소"), "M2SL": ("증가", "감소"),
            "DXY": ("강세", "약세"), "DGS10": ("상승", "하락")}
    for k in ["M2SL", "WALCL", "RRPONTSYD", "WTREGEN", "DGS10", "DXY"]:
        b = breakdown.get(k, {})
        if b.get("score", 0) > 0:
            d = changes.get(k, {}).get("delta", 0)
            pos.append(f"{name[k]} {word[k][0] if (d or 0) >= 0 else word[k][1]}")
        elif b.get("score", 0) < 0:
            d = changes.get(k, {}).get("delta", 0)
            neg.append(f"{name[k]} {word[k][0] if (d or 0) >= 0 else word[k][1]}")

    parts = [f"현재 유동성 종합 점수는 100점 만점에 {liq['score']}점({liq['label']})입니다."]
    if pos:
        parts.append(f"{', '.join(pos[:3])}는 위험자산에 긍정적입니다.")
    if neg:
        parts.append(f"반면 {', '.join(neg[:3])}는 상승을 제한하는 요인입니다.")
    if rates.get("ok"):
        parts.append(f"금리 시장은 {rates['label'].split(' (')[0]}를 반영 중이며, "
                     f"향후 3개월은 {rates['view_3m']}.")
    if cycle.get("ok"):
        parts.append(f"국채 곡선 기준 경기 단계는 '{cycle['stage']}'이며, "
                     f"금리차 기반 침체확률 근사치는 약 {cycle['recession_prob']}%입니다.")

    # 종합 톤
    s = liq["score"]
    if s >= 60:
        tone = "거시 환경은 중립~강세로 판단됩니다."
    elif s >= 45:
        tone = "거시 환경은 중립으로 판단됩니다."
    elif s >= 35:
        tone = "거시 환경은 중립~약세로 판단됩니다."
    else:
        tone = "거시 환경은 위험자산에 비우호적으로 판단됩니다."
    parts.append(tone)
    return " ".join(parts)


# ═════════════════════════════════════════════════════════════════════════════
#  Macro Intelligence 전용 분석 엔진
# ═════════════════════════════════════════════════════════════════════════════
def get_vix() -> pd.Series:
    return fetch_yfinance_data("^VIX")


def get_hy_spread() -> pd.Series:
    """미국 하이일드 스프레드 (FRED BAMLH0A0HYM2, %)."""
    return fetch_fred_data("BAMLH0A0HYM2")


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def global_m2_yoy() -> dict:
    """
    주요국(US·유로존·일본·중국) M2를 USD로 환산·합산한 근사 Global M2와 YoY.
    받을 수 있는 국가만 합산하고 포함 국가를 함께 반환.
    """
    comps = {
        "US": ("M2SL", None),                       # 이미 USD(십억)
        "EU": ("MYAGM2EZM196N", "EURUSD=X"),        # 유로 → USD
        "JP": ("MYAGM2JPM189S", "JPY=X"),           # 엔(USD/JPY) → USD
        "CN": ("MYAGM2CNM189N", "CNY=X"),           # 위안(USD/CNY) → USD
    }
    included, series_list = [], []
    for ctry, (fred_id, fx_sym) in comps.items():
        s = fetch_fred_data(fred_id)
        if s is None or s.dropna().empty:
            continue
        s = s.dropna()
        # 단위: 각국 M2는 보통 십억(자국통화). USD 환산.
        if fx_sym is None:
            usd = s  # US: 이미 십억 USD
        else:
            fx = fetch_yfinance_data(fx_sym)
            if fx is None or fx.dropna().empty:
                continue
            rate = float(fx.dropna().iloc[-1])  # 최신 환율로 환산(근사)
            if fx_sym == "EURUSD=X":
                usd = s * rate                  # EUR → USD (곱)
            else:
                usd = s / rate                  # USD/JPY, USD/CNY (나눔)
        # 월별로 리샘플해 정렬
        usd = usd.resample("MS").last().dropna() if hasattr(usd, "resample") else usd
        series_list.append(usd.rename(ctry))
        included.append(ctry)

    if not series_list:
        return {"ok": False, "included": [], "current": None, "yoy": None,
                "series": pd.Series(dtype=float)}

    df = pd.concat(series_list, axis=1).sort_index().ffill().dropna()
    total = df.sum(axis=1).rename("GLOBAL_M2")     # 십억 USD
    # YoY: 12개월 전 대비
    if len(total) >= 13:
        yoy = (total.iloc[-1] / total.iloc[-13] - 1) * 100
    else:
        yoy = None
    return {"ok": True, "included": included, "current": float(total.iloc[-1]),
            "yoy": (float(yoy) if yoy is not None else None), "series": total}


def macro_weather(liq: dict, rates: dict, dxy_change: dict,
                  vix_val: float | None) -> dict:
    """
    종합 리스크온/오프 날씨. 유동성·금리·달러·VIX를 합성.
    """
    f_liq = _clip((liq["score"] - 50) / 50.0)
    f_rate = _clip(-(rates.get("d3") or 0) / 0.5)
    f_dollar = _clip(-(dxy_change.get("pct") or 0) / 5.0)
    # VIX: 낮으면 위험선호(+), 높으면 위험회피(-). 20을 중립으로.
    if vix_val is not None:
        f_vix = _clip((20 - vix_val) / 15.0)
    else:
        f_vix = 0.0
    composite = 0.40 * f_liq + 0.20 * f_rate + 0.20 * f_dollar + 0.20 * f_vix

    if composite >= 0.45:
        return {"emoji": "☀️", "label": "Strong Risk-On", "color": C_GREEN,
                "kr": "강한 위험선호", "score": composite}
    if composite >= 0.15:
        return {"emoji": "🌤️", "label": "Mild Risk-On", "color": "#84cc16",
                "kr": "약한 위험선호", "score": composite}
    if composite > -0.15:
        return {"emoji": "⛅", "label": "Neutral", "color": C_YELLOW,
                "kr": "중립", "score": composite}
    if composite > -0.45:
        return {"emoji": "🌧️", "label": "Risk-Off", "color": "#f97316",
                "kr": "위험회피", "score": composite}
    return {"emoji": "⛈️", "label": "Severe Risk-Off", "color": C_RED,
            "kr": "강한 위험회피", "score": composite}


def liquidity_trend(changes: dict) -> dict:
    """유동성 4지표(WALCL·TGA·RRP·M2)의 방향으로 개선/안정/악화 판정."""
    score = 0
    score += _sign(changes.get("WALCL", {}).get("delta"))          # 자산 증가 +
    score += -_sign(changes.get("WTREGEN", {}).get("delta"))       # TGA 감소 +
    score += -_sign(changes.get("RRPONTSYD", {}).get("delta"))     # RRP 감소 +
    score += 2 * _sign(changes.get("M2SL", {}).get("delta"))       # M2 증가 +
    if score >= 2:
        return {"label": "Improving", "kr": "개선", "emoji": "🟢", "color": C_GREEN}
    if score <= -2:
        return {"label": "Deteriorating", "kr": "악화", "emoji": "🔴", "color": C_RED}
    return {"label": "Stable", "kr": "안정", "emoji": "🟡", "color": C_YELLOW}


def economic_cycle_full(cycle: dict, unrate: pd.Series, gdp: pd.Series) -> dict:
    """기존 금리곡선 사이클에 실업률·GDP 추세 근거를 보강."""
    out = dict(cycle)
    notes = []
    # 실업률 추세 (상승=둔화 신호)
    ur = calculate_change(unrate, months=6)
    if ur.get("ok"):
        d = ur["delta"]
        notes.append(f"실업률 6개월 {'상승' if d > 0 else '하락' if d < 0 else '보합'}"
                     f"({ur['current']:.1f}%)")
    # GDP 추세 (실질 GDP YoY 근사)
    g = calculate_change(gdp, months=12)
    if g.get("ok") and g.get("pct") is not None:
        notes.append(f"실질 GDP 전년대비 {g['pct']:+.1f}%")
    out["macro_notes"] = notes
    return out


def risk_dashboard(vix_val, dxy_change, dgs10_val, hy_val) -> dict:
    """VIX·DXY·US10Y·하이일드 스프레드로 종합 위험 수준."""
    items = []
    risk_pts = 0

    # VIX
    if vix_val is not None:
        if vix_val >= 30: lv, c, p = "High", C_RED, 3
        elif vix_val >= 20: lv, c, p = "Elevated", "#f97316", 2
        elif vix_val >= 15: lv, c, p = "Moderate", C_YELLOW, 1
        else: lv, c, p = "Low", C_GREEN, 0
        risk_pts += p
        items.append(("VIX (변동성)", f"{vix_val:.1f}", lv, c))

    # DXY 변화 (강달러 = 위험)
    dp = dxy_change.get("pct")
    if dp is not None:
        if dp >= 3: lv, c, p = "Elevated", "#f97316", 2
        elif dp >= 1: lv, c, p = "Moderate", C_YELLOW, 1
        else: lv, c, p = "Low", C_GREEN, 0
        risk_pts += p
        items.append(("DXY 3개월 변화", f"{dp:+.1f}%", lv, c))

    # US10Y 수준
    if dgs10_val is not None:
        if dgs10_val >= 4.5: lv, c, p = "Elevated", "#f97316", 2
        elif dgs10_val >= 4.0: lv, c, p = "Moderate", C_YELLOW, 1
        else: lv, c, p = "Low", C_GREEN, 0
        risk_pts += p
        items.append(("US 10Y 금리", f"{dgs10_val:.2f}%", lv, c))

    # HY 스프레드
    if hy_val is not None:
        if hy_val >= 5.0: lv, c, p = "High", C_RED, 3
        elif hy_val >= 4.0: lv, c, p = "Elevated", "#f97316", 2
        elif hy_val >= 3.0: lv, c, p = "Moderate", C_YELLOW, 1
        else: lv, c, p = "Low", C_GREEN, 0
        risk_pts += p
        items.append(("하이일드 스프레드", f"{hy_val:.2f}%", lv, c))

    n = max(1, len(items))
    avg = risk_pts / n
    if avg >= 2.3: overall, oc = "High", C_RED
    elif avg >= 1.5: overall, oc = "Elevated", "#f97316"
    elif avg >= 0.7: overall, oc = "Moderate", C_YELLOW
    else: overall, oc = "Low", C_GREEN

    assess = {
        "High": "시장 위험 지표가 전반적으로 높습니다. 변동성 확대에 유의할 구간입니다.",
        "Elevated": "일부 위험 지표가 높아 신중함이 필요한 구간입니다.",
        "Moderate": "위험 수준은 보통입니다. 특이 신호는 제한적입니다.",
        "Low": "위험 지표가 전반적으로 안정적입니다.",
    }[overall]
    return {"items": items, "overall": overall, "color": oc, "assess": assess}


def asset_preference_ranking(signals: dict) -> list:
    """거시환경 점수 기준 자산 상대 선호도 순위(투자 추천 아님)."""
    order = sorted(signals.items(), key=lambda kv: kv[1]["score"], reverse=True)
    ranking = []
    for rank, (key, sg) in enumerate(order, 1):
        ranking.append({"rank": rank, "key": key, "name": ASSET_KR[key],
                        "stance": sg["stance"], "color": sg["color"],
                        "score": sg["score"], "driver": sg["top3"][0] if sg["top3"] else "—"})
    return ranking


# ── Model Allocation (거시환경 기준 모델 포트폴리오 · 투자자문 아님) ────────────
# 위험자산 키(현금 제외)
RISK_ASSETS = ["BTC", "QQQ", "SOXX", "GLD", "TLT"]

# 모델별 파라미터
#  cash_floor: 현금 최소 비중, cash_cap: 현금 최대 비중
#  temp: 점수 민감도(클수록 우호 자산에 더 몰아줌)
#  max_w: 위험자산 한 종목 최대 비중
ALLOC_MODELS = {
    "Conservative": {"kr": "보수형", "cash_floor": 0.30, "cash_cap": 0.70,
                     "temp": 1.6, "max_w": 0.30,
                     "desc": "현금 비중을 높게 유지하며 변동성을 낮추는 방어적 구성입니다."},
    "Balanced":     {"kr": "균형형", "cash_floor": 0.10, "cash_cap": 0.55,
                     "temp": 2.2, "max_w": 0.35,
                     "desc": "현금과 위험자산의 균형을 맞춘 중도적 구성입니다."},
    "Aggressive":   {"kr": "공격형", "cash_floor": 0.02, "cash_cap": 0.40,
                     "temp": 3.0, "max_w": 0.45,
                     "desc": "우호적인 위험자산에 더 적극적으로 배분하는 공격적 구성입니다."},
}


def _softmax(scores: dict, temp: float) -> dict:
    import math
    mx = max(scores.values())
    exps = {k: math.exp(temp * (v - mx)) for k, v in scores.items()}
    tot = sum(exps.values()) or 1.0
    return {k: v / tot for k, v in exps.items()}


def macro_allocation(signals: dict, model: str) -> dict:
    """
    환경 점수를 비중(%)으로 환산. 투자 추천이 아니라 환경 기반 상대 비중.
      1) 위험자산 평균 환경으로 '현금 비중'을 먼저 결정(약세일수록 현금↑)
      2) 남은 비중을 위험자산 점수 소프트맥스로 배분(우호적일수록↑)
      3) 종목 상한(max_w) 적용 후 재정규화, 총합 100%
    """
    p = ALLOC_MODELS[model]
    risk_scores = {k: signals[k]["score"] for k in RISK_ASSETS if k in signals}
    if not risk_scores:
        return {"weights": {}, "model": model}

    avg = sum(risk_scores.values()) / len(risk_scores)   # -1~+1
    # 현금 비중: 위험자산이 약세(-)일수록 floor→cap 쪽으로
    t = _clip((avg + 1) / 2)                              # 0(약세)~1(강세)
    cash_w = p["cash_cap"] - (p["cash_cap"] - p["cash_floor"]) * t
    # 현금 자체 환경 점수가 매우 우호적이면 약간 가산
    if "CASH" in signals:
        cash_w += 0.10 * _clip(signals["CASH"]["score"])
    cash_w = min(p["cash_cap"], max(p["cash_floor"], cash_w))

    # 위험자산 비중 = (1 - cash) 을 소프트맥스로 분배
    risk_budget = 1.0 - cash_w
    dist = _softmax(risk_scores, p["temp"])
    weights = {k: dist[k] * risk_budget for k in risk_scores}

    # 종목 상한 적용 후 초과분을 다른 위험자산에 재분배
    for _ in range(3):
        over = {k: w - p["max_w"] for k, w in weights.items() if w > p["max_w"]}
        if not over:
            break
        excess = sum(over.values())
        for k in over:
            weights[k] = p["max_w"]
        under = {k: w for k, w in weights.items() if w < p["max_w"]}
        ub = sum(under.values()) or 1.0
        for k in under:
            weights[k] += excess * (under[k] / ub)

    weights["CASH"] = cash_w
    # 반올림(정수%) 후 총합 100 보정
    pct = {k: round(v * 100) for k, v in weights.items()}
    diff = 100 - sum(pct.values())
    if diff != 0:
        # 가장 비중 큰 항목에서 보정
        big = max(pct, key=pct.get)
        pct[big] += diff
    return {"weights": pct, "cash": pct["CASH"], "model": model, "avg_risk": avg}


def allocation_commentary(alloc: dict, signals: dict) -> str:
    """배분 결과 한글 설명 자동 생성."""
    w = alloc["weights"]
    cash = w.get("CASH", 0)
    # 위험자산 비중 상위 2개
    risk_sorted = sorted([(k, w[k]) for k in RISK_ASSETS if k in w],
                         key=lambda x: x[1], reverse=True)
    top = risk_sorted[:2]
    parts = []
    if cash >= 40:
        parts.append(f"현재 환경에서는 위험자산 우호도가 낮아 현금 비중이 {cash}%로 높게 산정되었습니다.")
    elif cash >= 20:
        parts.append(f"현금 비중은 {cash}%로 중간 수준의 방어를 유지합니다.")
    else:
        parts.append(f"위험선호 환경이 우세해 현금 비중은 {cash}%로 낮게 산정되었습니다.")
    if top:
        names = ", ".join(f"{ASSET_KR[k].split(' (')[0]} {v}%" for k, v in top)
        parts.append(f"위험자산 중에서는 환경 우호도가 높은 {names} 순으로 비중이 배정되었습니다.")
    # 비트코인 코멘트(있으면)
    if "BTC" in signals:
        sg = signals["BTC"]
        parts.append(f"비트코인은 환경상 {sg['stance']}으로 평가되어 {w.get('BTC',0)}% 배정되었습니다.")
    return " ".join(parts)


def biggest_changes(weekly_inputs: list) -> list:
    """
    이번 주(약 7일) 가장 큰 변화 3개 자동 추출.
    weekly_inputs: [(label, series, good_up, fmt), ...]
      good_up=True 면 '상승=위험자산에 긍정'
    """
    cands = []
    for label, series, good_up, fmt in weekly_inputs:
        if series is None or series.dropna().empty:
            continue
        s = series.dropna()
        if len(s) < 2:
            continue
        last = float(s.iloc[-1])
        prev_idx = s.index[-1] - timedelta(days=7)
        prior = s.loc[:prev_idx]
        prev = float(prior.iloc[-1]) if not prior.empty else float(s.iloc[0])
        if prev == 0:
            continue
        delta = last - prev
        pct = delta / abs(prev) * 100
        good = (delta > 0) == good_up
        cands.append({"label": label, "prev": prev, "last": last, "delta": delta,
                      "pct": pct, "good": good, "fmt": fmt, "mag": abs(pct)})
    cands.sort(key=lambda c: c["mag"], reverse=True)
    return cands[:3]


# ─────────────────────────────────────────────────────────────────────────────
# 7~8) 차트
# ─────────────────────────────────────────────────────────────────────────────
def _slice_period(s: pd.Series, period: str) -> pd.Series:
    if s.empty:
        return s
    days = PERIOD_DAYS.get(period, 365)
    cutoff = s.index[-1] - timedelta(days=days)
    return s.loc[s.index >= cutoff]


def _base_layout(fig: go.Figure, title: str, ytitle: str = ""):
    fig.update_layout(
        title=dict(text=title, font=dict(size=16)),
        template=PLOT_TEMPLATE, height=320,
        margin=dict(l=10, r=10, t=44, b=10),
        hovermode="x unified", showlegend=False,
        yaxis_title=ytitle,
    )
    return fig


def _empty_fig(title: str, msg: str = "데이터 없음 (새로고침 시 복구될 수 있음)") -> go.Figure:
    """데이터가 없을 때 안내 문구를 표시하는 빈 차트."""
    fig = go.Figure()
    fig.add_annotation(text=msg,
                       xref="paper", yref="paper", x=0.5, y=0.5,
                       showarrow=False, font=dict(size=13, color="#94a3b8"))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return _base_layout(fig, title)


def plot_indicator_chart(s: pd.Series, title: str, period: str,
                        ytitle: str = "") -> go.Figure:
    sp = _slice_period(s, period) if s is not None else pd.Series(dtype=float)
    if sp is None or sp.dropna().empty:
        return _empty_fig(title)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=sp.index, y=sp.values, mode="lines",
        line=dict(color=C_LINE, width=2),
        fill="tozeroy", fillcolor="rgba(56,189,248,0.10)",
    ))
    return _base_layout(fig, title, ytitle)


def plot_price_ma_chart(price: pd.Series, ma: pd.Series, title: str, period: str,
                        ccy: str = "USD", name: str = "가격") -> go.Figure:
    """가격 + 200일선 차트 (BTC·ETF 공용)."""
    fig = go.Figure()
    p = _slice_period(price, period) if price is not None else pd.Series(dtype=float)
    m = _slice_period(ma, period) if ma is not None else pd.Series(dtype=float)
    if (p is None or p.dropna().empty) and (m is None or m.dropna().empty):
        return _empty_fig(title)
    if p is not None and not p.empty:
        fig.add_trace(go.Scatter(x=p.index, y=p.values, mode="lines",
                                 name=name, line=dict(color=C_LINE, width=2)))
    if m is not None and not m.empty:
        fig.add_trace(go.Scatter(x=m.index, y=m.values, mode="lines",
                                 name="200일선", line=dict(color=C_MA, width=2, dash="dash")))
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=1.12, x=0))
    return _base_layout(fig, title, ccy)


def plot_btc_ma_chart(btc: pd.Series, ma: pd.Series, period: str) -> go.Figure:
    fig = go.Figure()
    b, m = _slice_period(btc, period), _slice_period(ma, period)
    if not b.empty:
        fig.add_trace(go.Scatter(x=b.index, y=b.values, mode="lines",
                                 name="BTC", line=dict(color=C_LINE, width=2)))
    if not m.empty:
        fig.add_trace(go.Scatter(x=m.index, y=m.values, mode="lines",
                                 name="200일선", line=dict(color=C_MA, width=2, dash="dash")))
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=1.12, x=0))
    return _base_layout(fig, CHART_TITLES["BTC"], "USD")


def plot_net_liquidity_chart(net: pd.Series, period: str) -> go.Figure:
    sp = _slice_period(net, period) if net is not None else pd.Series(dtype=float)
    if sp is None or sp.dropna().empty:
        return _empty_fig(CHART_TITLES["NETLIQ"])
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=sp.index, y=sp.values / 1000.0, mode="lines",   # 십억→조
        line=dict(color="#a78bfa", width=2),
        fill="tozeroy", fillcolor="rgba(167,139,250,0.10)",
    ))
    return _base_layout(fig, CHART_TITLES["NETLIQ"], "조 달러 (Trillion)")


# ─────────────────────────────────────────────────────────────────────────────
# 표시용 포맷
# ─────────────────────────────────────────────────────────────────────────────
def fmt_value(sid: str, v: float | None) -> str:
    if v is None:
        return "—"
    fmt = INDICATORS.get(sid, {}).get("fmt", "")
    if fmt == "trillion":      # 십억 → 조 달러
        return f"${v/1000:,.2f}T"
    if fmt == "billion":
        return f"${v:,.0f}B"
    if fmt == "rate":
        return f"{v:.2f}%"
    if fmt == "index":
        return f"{v:.2f}"
    return f"{v:,.2f}"


def fmt_delta(sid: str, ch: dict) -> str:
    if not ch or not ch.get("ok"):
        return "—"
    unit = INDICATORS.get(sid, {}).get("unit", "")
    if unit == "pct":  # 금리/지수는 변화량(pt)으로
        d = ch["delta"]
        return f"{'+' if d >= 0 else ''}{d:.2f}p"
    p = ch.get("pct")
    if p is None:
        return "—"
    return f"{'+' if p >= 0 else ''}{p:.2f}%"


# ─────────────────────────────────────────────────────────────────────────────
# 카드 렌더링
# ─────────────────────────────────────────────────────────────────────────────
def render_card(title: str, cur: str, past: str, delta: str,
                emoji: str, status: str, desc: str):
    color = {"긍정": C_GREEN, "중립": C_YELLOW, "부정": C_RED}.get(status, C_YELLOW)
    st.markdown(
        f"""
        <div style="border:1px solid #2a2a35;border-left:4px solid {color};
                    border-radius:12px;padding:14px 16px;background:#15151c;
                    height:100%;">
          <div style="font-size:14px;color:#cbd5e1;font-weight:600;">{title}</div>
          <div style="display:flex;justify-content:space-between;align-items:baseline;
                      margin-top:6px;">
            <span style="font-size:24px;font-weight:700;color:#f1f5f9;">{cur}</span>
            <span style="font-size:13px;color:{color};font-weight:600;">{emoji} {status}</span>
          </div>
          <div style="font-size:12px;color:#94a3b8;margin-top:4px;">
            3개월 전: {past} · 변화: <b style="color:{color};">{delta}</b>
          </div>
          <div style="font-size:12px;color:#94a3b8;margin-top:8px;line-height:1.5;">{desc}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────
def main():
    st.title("💧 비트코인 & 주식시장 거시 유동성 대시보드")
    st.caption(f"{datetime.now():%Y년 %m월 %d일} 기준 · 거시 유동성으로 보는 위험자산(비트코인·나스닥) "
               "환경 판단 도구 · 투자 추천이 아닙니다")

    # ── 사이드바
    with st.sidebar:
        st.header("⚙️ 설정")
        period = st.radio("차트 기간", list(PERIOD_DAYS.keys()), index=2)
        st.session_state["period"] = period
        if st.button("🔄 데이터 새로고침"):
            st.cache_data.clear()
            st.rerun()
        st.markdown("---")
        st.caption("데이터: FRED(공개) · yfinance\n\n3개월 변화율 기준으로 점수를 계산합니다.")
        with st.expander("🔧 FRED 연결 진단"):
            d = fred_diagnostics()
            st.write(f"- API 키 인식: {'✅ 있음(' + str(d['key_len']) + '자)' if d['key_present'] else '❌ 없음'}")
            st.write(f"- 공식 API: {d['api']}")
            st.write(f"- 공개 CSV: {d['csv']}")

    # ── 데이터 수집
    with st.spinner("거시 지표를 수집하는 중..."):
        raw, norm, changes, errors = {}, {}, {}, []
        for sid in ["WALCL", "WTREGEN", "RRPONTSYD", "M2SL", "DGS10"]:
            s = fetch_fred_data(sid)
            if s.empty:
                errors.append(INDICATORS[sid]["kr"])
            raw[sid] = s
            norm[sid] = normalize_usd_billions(sid, s)

        dxy = fetch_yfinance_data("DX-Y.NYB")
        if dxy.empty:
            errors.append("달러의 힘 (DXY)")
        norm["DXY"] = dxy

        btc = fetch_yfinance_data("BTC-USD")
        if btc.empty:
            errors.append("비트코인 가격 (BTC-USD)")

        # 파생
        btc_ma = compute_btc_ma(btc, 200)
        net = compute_net_liquidity(norm["WALCL"], norm["WTREGEN"], norm["RRPONTSYD"])

        # 변화율
        for sid in INDICATORS:
            changes[sid] = calculate_change(norm[sid], months=3)
        netliq_change = calculate_change(net, months=3)

        # BTC vs 200일선
        btc_above_ma = None
        btc_price = btc_ma_val = None
        if not btc.empty and not btc_ma.dropna().empty:
            btc_price = float(btc.iloc[-1])
            btc_ma_val = float(btc_ma.dropna().iloc[-1])
            btc_above_ma = btc_price > btc_ma_val

        total, breakdown = calculate_score(changes, btc_above_ma, netliq_change)
        interp = generate_interpretation(total)

        # 대표 ETF 200일선 추세 (거시 점수와 별개의 '추세 현황')
        etf_state = {}
        for key, meta in ETFS.items():
            s = fetch_yfinance_data(meta["symbol"])
            ma = compute_btc_ma(s, 200)  # 동일한 200일 이동평균 함수 재사용
            if s.empty or ma.dropna().empty:
                etf_state[key] = {"price": None, "ma": None, "above": None,
                                  "series": s, "ma_series": ma}
                errors.append(meta["kr"])
            else:
                price = float(s.iloc[-1]); ma_val = float(ma.dropna().iloc[-1])
                etf_state[key] = {"price": price, "ma": ma_val,
                                  "above": price > ma_val,
                                  "series": s, "ma_series": ma}

        # ── 추가 금리 시리즈 (분석용)
        dgs2 = fetch_fred_data("DGS2")
        t10y3m = fetch_fred_data("T10Y3M")

        # ── 분석 엔진
        liq = liquidity_score_100(breakdown)
        rates = analyze_rates(dgs2, norm["DGS10"], t10y3m)
        cycle = analyze_cycle(t10y3m)
        dxy_change = changes.get("DXY", {})
        macro_factors = build_macro_factors(liq, rates, dxy_change)
        briefing = generate_briefing(liq, rates, cycle, changes, breakdown)

        # 자산별 시그널 (BTC + 4개 ETF)
        signals = {}
        # BTC
        btc_dist = (btc_price / btc_ma_val - 1) if (btc_price and btc_ma_val) else None
        signals["BTC"] = asset_signal("BTC", macro_factors, btc_above_ma, btc_dist)
        # ETF 4종
        for key in ["QQQ", "SOXX", "GLD", "TLT"]:
            stt = etf_state.get(key, {})
            dist = (stt["price"] / stt["ma"] - 1) if (stt.get("price") and stt.get("ma")) else None
            signals[key] = asset_signal(key, macro_factors, stt.get("above"), dist)

    # ── 에러 안내(부분 실패해도 계속 진행)
    if errors:
        st.warning("일부 지표를 불러오지 못했습니다: " + ", ".join(sorted(set(errors)))
                   + ". 잠시 후 사이드바의 🔄 새로고침을 누르면 복구될 수 있습니다. "
                   "(불러온 지표만으로 계산했습니다.)")

    # ── Macro Intelligence 추가 데이터 수집
    with st.spinner("심층 분석 데이터를 수집하는 중..."):
        vix = get_vix()
        vix_val = float(vix.dropna().iloc[-1]) if (vix is not None and not vix.dropna().empty) else None
        hy = get_hy_spread()
        hy_val = float(hy.dropna().iloc[-1]) if (hy is not None and not hy.dropna().empty) else None
        unrate = fetch_fred_data("UNRATE")
        gdp = fetch_fred_data("GDPC1")
        gm2 = global_m2_yoy()

        dgs10_val = rates.get("dgs10")
        weather = macro_weather(liq, rates, dxy_change, vix_val)
        liq_trend = liquidity_trend(changes)
        cycle_full = economic_cycle_full(cycle, unrate, gdp)
        risk = risk_dashboard(vix_val, dxy_change, dgs10_val, hy_val)
        # 현금/MMF 시그널 (위험자산 반대 + 단기금리). weather 계산 후에 추가.
        signals["CASH"] = cash_signal(signals, rates.get("dgs2"), weather)
        ranking = asset_preference_ranking(signals)

        weekly_inputs = [
            ("US10Y 금리", raw.get("DGS10"), False, "{:.2f}%"),
            ("DXY 달러", norm.get("DXY"), False, "{:.2f}"),
            ("VIX 변동성", vix, False, "{:.1f}"),
            ("하이일드 스프레드", hy, False, "{:.2f}%"),
            ("비트코인", btc, True, "${:,.0f}"),
            ("나스닥100(QQQ)", etf_state.get("QQQ", {}).get("series"), True, "${:.2f}"),
            ("반도체(SOXX)", etf_state.get("SOXX", {}).get("series"), True, "${:.2f}"),
            ("금(GLD)", etf_state.get("GLD", {}).get("series"), True, "${:.2f}"),
            ("장기채(TLT)", etf_state.get("TLT", {}).get("series"), True, "${:.2f}"),
            ("Global M2", gm2.get("series"), True, "{:,.0f}B"),
        ]
        top_changes = biggest_changes(weekly_inputs)

        # 모델 포트폴리오 (3종)
        allocations = {m: macro_allocation(signals, m) for m in ALLOC_MODELS}

    # ── 컨텍스트 묶음
    ctx = dict(
        period=period, errors=errors,
        raw=raw, norm=norm, changes=changes, net=net, netliq_change=netliq_change,
        btc=btc, btc_ma=btc_ma, btc_price=btc_price, btc_ma_val=btc_ma_val,
        btc_above_ma=btc_above_ma, total=total, breakdown=breakdown, interp=interp,
        etf_state=etf_state, briefing=briefing, liq=liq, rates=rates, cycle=cycle,
        signals=signals,
        # macro intelligence
        vix=vix, vix_val=vix_val, hy=hy, hy_val=hy_val, unrate=unrate, gdp=gdp,
        gm2=gm2, weather=weather, liq_trend=liq_trend, cycle_full=cycle_full,
        risk=risk, ranking=ranking, top_changes=top_changes, dgs10_val=dgs10_val,
        allocations=allocations,
    )

    # ── 탭 구성
    tab1, tab2, tab3 = st.tabs(
        ["📊 Market Overview", "🧠 Macro Intelligence", "🧮 Model Allocation"])
    with tab1:
        render_market_overview(ctx)
    with tab2:
        render_macro_intelligence(ctx)
    with tab3:
        render_model_allocation(ctx)


# ═════════════════════════════════════════════════════════════════════════════
#  탭 1) Market Overview — 원시 데이터 확인용 (기존 화면)
# ═════════════════════════════════════════════════════════════════════════════
def render_market_overview(ctx: dict):
    period = ctx["period"]; raw = ctx["raw"]; norm = ctx["norm"]; changes = ctx["changes"]
    net = ctx["net"]; netliq_change = ctx["netliq_change"]; btc = ctx["btc"]; btc_ma = ctx["btc_ma"]
    btc_price = ctx["btc_price"]; btc_ma_val = ctx["btc_ma_val"]; btc_above_ma = ctx["btc_above_ma"]
    total = ctx["total"]; breakdown = ctx["breakdown"]; interp = ctx["interp"]
    etf_state = ctx["etf_state"]; briefing = ctx["briefing"]; liq = ctx["liq"]
    rates = ctx["rates"]; cycle = ctx["cycle"]; signals = ctx["signals"]

    # ── 상단: 요약 지표
    st.markdown("### 📌 오늘의 요약")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("비트코인 현재가", f"${btc_price:,.0f}" if btc_price else "—")
    c2.metric("BTC 200일 이동평균", f"${btc_ma_val:,.0f}" if btc_ma_val else "—",
              delta=("200일선 위 ▲" if btc_above_ma else "200일선 아래 ▼")
              if btc_above_ma is not None else None)
    c3.metric("종합 점수", f"{'+' if total >= 0 else ''}{total}점")
    c4.metric("최종 판정", f"{interp['emoji']} {interp['label']}")

    pct = (total + 11) / 22 * 100
    st.markdown(
        f"""<div style="background:#1f2937;border-radius:8px;height:10px;margin:4px 0 18px;">
            <div style="width:{pct:.0f}%;height:10px;border-radius:8px;background:{interp['color']};"></div>
        </div>""", unsafe_allow_html=True)

    # ── 종합 리포트
    st.markdown("### 📝 종합 해석 리포트")
    st.info(generate_report(total, breakdown, changes, netliq_change, btc_above_ma))

    # ── 지표 카드
    st.markdown("### 🧩 지표별 카드")
    card_order = ["WALCL", "WTREGEN", "RRPONTSYD", "M2SL", "DXY", "DGS10"]
    cols = st.columns(3)
    for i, sid in enumerate(card_order):
        ch = changes[sid]
        with cols[i % 3]:
            render_card(
                INDICATORS[sid]["kr"],
                fmt_value(sid, ch.get("current")),
                fmt_value(sid, ch.get("past")),
                fmt_delta(sid, ch),
                breakdown[sid]["emoji"], breakdown[sid]["status"],
                INDICATORS[sid]["desc"],
            )
        if i % 3 == 2 and i != len(card_order) - 1:
            cols = st.columns(3)

    cols2 = st.columns(3)
    with cols2[0]:
        render_card(
            "실제 시장 유동성 (Net Liquidity)",
            fmt_value("M2SL", netliq_change.get("current")),
            fmt_value("M2SL", netliq_change.get("past")),
            (f"{'+' if (netliq_change.get('pct') or 0) >= 0 else ''}"
             f"{netliq_change.get('pct'):.2f}%" if netliq_change.get("pct") is not None else "—"),
            breakdown["NETLIQ"]["emoji"], breakdown["NETLIQ"]["status"], NETLIQ_DESC,
        )
    with cols2[1]:
        trend_txt = ("상승 추세" if btc_above_ma else "하락 추세") if btc_above_ma is not None else "—"
        render_card(
            "비트코인 추세 (200일선)",
            f"${btc_price:,.0f}" if btc_price else "—",
            f"200일선 ${btc_ma_val:,.0f}" if btc_ma_val else "—",
            trend_txt, breakdown["BTC"]["emoji"], breakdown["BTC"]["status"], BTC_DESC,
        )

    # ── 그래프 8개
    st.markdown("### 📊 그래프")
    g1, g2 = st.columns(2)
    g1.plotly_chart(plot_indicator_chart(norm["WALCL"] / 1000, CHART_TITLES["WALCL"],
                                         period, "조 달러"), use_container_width=True)
    g2.plotly_chart(plot_indicator_chart(norm["WTREGEN"], CHART_TITLES["WTREGEN"],
                                         period, "십억 달러"), use_container_width=True)
    g3, g4 = st.columns(2)
    g3.plotly_chart(plot_indicator_chart(norm["RRPONTSYD"], CHART_TITLES["RRPONTSYD"],
                                         period, "십억 달러"), use_container_width=True)
    g4.plotly_chart(plot_indicator_chart(norm["M2SL"] / 1000, CHART_TITLES["M2SL"],
                                         period, "조 달러"), use_container_width=True)
    g5, g6 = st.columns(2)
    g5.plotly_chart(plot_indicator_chart(norm["DXY"], CHART_TITLES["DXY"],
                                         period, "지수"), use_container_width=True)
    g6.plotly_chart(plot_indicator_chart(raw["DGS10"], CHART_TITLES["DGS10"],
                                         period, "%"), use_container_width=True)
    g7, g8 = st.columns(2)
    g7.plotly_chart(plot_btc_ma_chart(btc, btc_ma, period), use_container_width=True)
    g8.plotly_chart(plot_net_liquidity_chart(net, period), use_container_width=True)

    # ── 대표 ETF 추세
    st.markdown("### 📈 대표 ETF 추세 (200일선)")
    st.caption("주요 지수의 가격 추세 현황판입니다. 현재가가 200일선 위면 상승 추세로 봅니다.")
    etf_keys = list(ETFS.keys())
    for row_start in range(0, len(etf_keys), 3):
        ecols = st.columns(3)
        for j, key in enumerate(etf_keys[row_start:row_start + 3]):
            meta = ETFS[key]; stt = etf_state[key]
            with ecols[j]:
                above = stt["above"]
                if above is None:
                    status, emoji = "중립", "🟡"; cur = past = trend = "—"
                else:
                    status, emoji = ("긍정", "🟢") if above else ("부정", "🔴")
                    trend = "상승 추세" if above else "하락 추세"
                    if meta["ccy"] == "KRW":
                        cur = f"₩{stt['price']:,.0f}"; past = f"200일선 ₩{stt['ma']:,.0f}"
                    else:
                        cur = f"${stt['price']:,.2f}"; past = f"200일선 ${stt['ma']:,.2f}"
                render_card(meta["kr"], cur, past, trend, emoji, status, meta["desc"])
    for row_start in range(0, len(etf_keys), 3):
        fcols = st.columns(3)
        for j, key in enumerate(etf_keys[row_start:row_start + 3]):
            meta = ETFS[key]; stt = etf_state[key]
            fcols[j].plotly_chart(
                plot_price_ma_chart(stt["series"], stt["ma_series"], meta["kr"], period,
                                    ccy=meta["ccy"], name=key),
                use_container_width=True)

    with st.expander("🔎 점수 산정 내역 보기"):
        rows = []
        name_map = {**{k: v["kr"] for k, v in INDICATORS.items()},
                    "BTC": "비트코인 200일선", "NETLIQ": "Net Liquidity"}
        for sid, b in breakdown.items():
            rows.append({"지표": name_map.get(sid, sid),
                         "상태": f"{b['emoji']} {b['status']}",
                         "점수": f"{'+' if b['score'] >= 0 else ''}{b['score']}"})
        st.table(pd.DataFrame(rows))

    st.markdown("---")
    st.caption("⚠️ 본 화면은 원시 데이터 확인용입니다. 매수·매도 신호나 투자 추천이 아닙니다.")


# ═════════════════════════════════════════════════════════════════════════════
#  탭 2) Macro Intelligence — 해석 중심 (10초 안에 시장 환경 파악)
# ═════════════════════════════════════════════════════════════════════════════
def _pill(text: str, color: str, active: bool) -> str:
    bg = color if active else "#1f2937"
    fg = "#0b0b0f" if active else "#6b7280"
    weight = "800" if active else "500"
    return (f"<span style='display:inline-block;padding:6px 12px;border-radius:999px;"
            f"background:{bg};color:{fg};font-weight:{weight};font-size:13px;margin:2px;'>{text}</span>")


def render_macro_intelligence(ctx: dict):
    weather = ctx["weather"]; liq = ctx["liq"]; rates = ctx["rates"]
    cycle_full = ctx["cycle_full"]; risk = ctx["risk"]; ranking = ctx["ranking"]
    signals = ctx["signals"]; changes = ctx["changes"]; breakdown = ctx["breakdown"]
    liq_trend = ctx["liq_trend"]; gm2 = ctx["gm2"]; top_changes = ctx["top_changes"]
    vix_val = ctx["vix_val"]; dgs10_val = ctx["dgs10_val"]

    # ── Section 1. Macro Weather
    st.markdown(
        f"""<div style="border-radius:16px;padding:22px 24px;margin-bottom:6px;
                    background:linear-gradient(135deg,{weather['color']}22,#15151c);
                    border:1px solid {weather['color']}55;">
          <div style="font-size:13px;color:#94a3b8;letter-spacing:1px;">MACRO WEATHER · 현재 시장 환경</div>
          <div style="font-size:40px;font-weight:900;color:#f8fafc;margin-top:2px;">
            {weather['emoji']} {weather['label']}
            <span style="font-size:18px;color:#cbd5e1;">({weather['kr']})</span></div>
        </div>""", unsafe_allow_html=True)
    w1, w2, w3 = st.columns(3)
    w1.metric("Macro Weather", f"{weather['emoji']} {weather['label']}")
    w2.metric("Liquidity Score", f"{liq['score']} / 100")
    conf = signals["BTC"]["confidence"]  # 대표 신뢰도(요인 일치도)
    agree = "High" if liq['score'] >= 65 or liq['score'] <= 35 else "Medium"
    w3.metric("Confidence", agree)
    st.caption("판단 요소: Fed Balance Sheet · TGA · RRP · M2 · DXY · US10Y · VIX")

    st.divider()

    # ── Section 2. AI Executive Summary
    st.markdown("#### 🤖 AI Executive Summary")
    st.info(ctx["briefing"])

    st.divider()

    # ── Section 3. Liquidity Intelligence
    st.markdown("#### 💧 Liquidity Intelligence")
    l1, l2 = st.columns([1, 1.4])
    with l1:
        st.markdown(
            f"""<div style="border:1px solid #2a2a35;border-radius:12px;padding:16px;background:#15151c;">
              <div style="font-size:13px;color:#94a3b8;">Liquidity Score (0~100)</div>
              <div style="font-size:34px;font-weight:800;color:{liq['color']};">{liq['score']}
                <span style="font-size:15px;color:#cbd5e1;">/100</span></div>
              <div style="background:#1f2937;border-radius:6px;height:8px;margin:8px 0;">
                <div style="width:{liq['score']}%;height:8px;border-radius:6px;background:{liq['color']};"></div></div>
              <div style="font-size:14px;color:{liq_trend['color']};font-weight:700;">
                Trend: {liq_trend['emoji']} {liq_trend['label']} ({liq_trend['kr']})</div>
            </div>""", unsafe_allow_html=True)
    with l2:
        names = {"WALCL": "Fed Balance Sheet", "WTREGEN": "TGA",
                 "RRPONTSYD": "RRP", "M2SL": "M2"}
        rows = []
        for k, nm in names.items():
            ch = changes.get(k, {})
            rows.append({"지표": nm, "현재": fmt_value(k, ch.get("current")),
                         "3개월 변화": fmt_delta(k, ch),
                         "상태": f"{breakdown[k]['emoji']} {breakdown[k]['status']}"})
        st.table(pd.DataFrame(rows))
    interp_liq = ("유동성 4지표가 개선 방향으로 정렬되어 위험자산에 우호적입니다."
                  if liq_trend["label"] == "Improving" else
                  "유동성 4지표가 악화 방향이라 위험자산에 부담입니다."
                  if liq_trend["label"] == "Deteriorating" else
                  "유동성 지표가 혼조세로, 뚜렷한 방향성은 약합니다.")
    st.caption("🔎 AI Interpretation: " + interp_liq)

    st.divider()

    # ── Section 4. Rate Outlook
    st.markdown("#### 🌡️ Rate Outlook (국채시장 기반)")
    if rates.get("ok"):
        rc1, rc2, rc3 = st.columns(3)
        rc1.metric("DGS2 (2년)", f"{rates['dgs2']:.2f}%" if rates.get("dgs2") else "—")
        rc2.metric("DGS10 (10년)", f"{rates['dgs10']:.2f}%" if rates.get("dgs10") else "—")
        rc3.metric("T10Y3M (장단기차)",
                   f"{rates['spread']:+.2f}%p" if rates.get("spread") is not None else "—")
        st.markdown(f"**Current Market Expectation:** {rates['emoji']} {rates['label']}")
        bias = rates.get("bias", "hold")
        labels = ["Easing Bias", "Neutral", "Tightening Bias"]
        active_3m = {"ease": "Easing Bias", "hold": "Neutral", "tight": "Tightening Bias"}[bias]
        cmap = {"Easing Bias": C_GREEN, "Neutral": C_YELLOW, "Tightening Bias": C_RED}
        st.markdown("**3 Month Outlook**", help="2년물 모멘텀 기준")
        st.markdown(" ".join(_pill(l, cmap[l], l == active_3m) for l in labels),
                    unsafe_allow_html=True)
        st.markdown("**6 Month Outlook**")
        st.markdown(" ".join(_pill(l, cmap[l], l == active_3m) for l in labels),
                    unsafe_allow_html=True)
        st.caption("🔎 AI Commentary: " + rates["text"]
                   + f" 향후 6개월은 {rates['view_6m']}.")
    else:
        st.write("금리 데이터를 불러오지 못했습니다.")
    st.caption("※ CME FedWatch 직접 데이터가 아니라 국채(2Y·10Y)·장단기차가 반영하는 기대치 근사입니다.")

    st.divider()

    # ── Section 5. Economic Cycle
    st.markdown("#### 🔄 Economic Cycle")
    stages = ["회복(Recovery)", "확장(Expansion)", "둔화(Slowdown)", "침체위험(Recession Risk)"]
    stage_map = {"회복 / 확장": [0, 1], "확장 후반 / 둔화 진입": [2],
                 "둔화 / 침체 위험 누적": [2, 3], "침체 임박 / 전환 구간": [3],
                 "중립": [], "데이터 없음": []}
    active_idx = stage_map.get(cycle_full.get("stage", ""), [])
    st.markdown(" ".join(
        _pill(s, cycle_full.get("color", C_YELLOW), i in active_idx)
        for i, s in enumerate(stages)), unsafe_allow_html=True)
    cyc1, cyc2 = st.columns(2)
    cyc1.metric("현재 단계", cycle_full.get("stage", "—"))
    rec = cycle_full.get("recession_prob")
    cyc2.metric("침체 확률 (근사)", f"{rec}%" if rec is not None else "—")
    notes = cycle_full.get("macro_notes", [])
    base = "판단 근거: Yield Curve(장단기 금리차)"
    if notes:
        base += " · " + " · ".join(notes)
    st.caption("🔎 " + base)
    if cycle_full.get("ok"):
        st.caption("AI Commentary: " + cycle_full["desc"])
    st.caption("※ ISM 등 일부 지표는 안정적 무료 데이터가 없어 금리곡선·실업률·GDP로 추정합니다.")

    st.divider()

    # ── Section 6. Asset Environment
    st.markdown("#### 🎯 Asset Environment")
    st.caption("매수·매도 추천이 아니라, 거시 환경이 각 자산에 얼마나 우호적인지 보여줍니다.")
    sig_keys = ["BTC", "QQQ", "SOXX", "GLD", "TLT", "CASH"]
    en_map = {"우호적": ("Favorable", C_GREEN), "중립": ("Neutral", C_YELLOW),
              "비우호적": ("Unfavorable", C_RED)}
    cf_map = {"높음": "High", "중간": "Medium", "낮음": "Low"}
    for rs in range(0, len(sig_keys), 3):
        scols = st.columns(3)
        for j, key in enumerate(sig_keys[rs:rs + 3]):
            sg = signals[key]
            en, ec = en_map[sg["stance"]]
            bars = "".join(
                f"<span style='color:{sg['color']};'>●</span>" if i < {"높음":3,"중간":2,"낮음":1}[sg['confidence']]
                else "<span style='color:#374151;'>●</span>" for i in range(3))
            drivers = "".join(f"<li>{t}</li>" for t in sg["top3"])
            with scols[j]:
                st.markdown(
                    f"""<div style="border:1px solid #2a2a35;border-left:4px solid {ec};
                                border-radius:12px;padding:14px 16px;background:#15151c;height:100%;">
                      <div style="display:flex;justify-content:space-between;align-items:baseline;">
                        <span style="font-size:15px;font-weight:800;color:#f1f5f9;">{ASSET_KR[key]}</span>
                        <span style="font-size:13px;font-weight:800;color:{ec};">{sg['emoji']} {en}</span></div>
                      <div style="font-size:12px;color:#94a3b8;margin-top:6px;">Confidence {bars} {cf_map[sg['confidence']]}</div>
                      <div style="font-size:12px;color:#cbd5e1;margin-top:8px;">Top Drivers</div>
                      <ul style="font-size:12px;color:#94a3b8;margin:2px 0 8px 16px;padding:0;">{drivers}</ul>
                      <div style="font-size:12px;color:#94a3b8;line-height:1.5;">
                        <b style="color:#cbd5e1;">3M</b> {sg['view_3m']}<br>
                        <b style="color:#cbd5e1;">6M</b> {sg['view_6m']}</div>
                    </div>""", unsafe_allow_html=True)

    st.divider()

    # ── Section 7. Asset Preference Ranking
    st.markdown("#### 🏆 Asset Preference Ranking")
    st.caption("투자 추천이 아니라, 현재 거시환경 기준 '상대적 우호도' 순위입니다.")
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}
    for r in ranking:
        st.markdown(
            f"""<div style="display:flex;align-items:center;gap:12px;border:1px solid #2a2a35;
                        border-radius:10px;padding:10px 14px;background:#15151c;margin-bottom:6px;">
              <span style="font-size:18px;width:28px;">{medal.get(r['rank'], r['rank'])}</span>
              <span style="font-size:15px;font-weight:700;color:#f1f5f9;flex:1;">{r['name']}</span>
              <span style="font-size:12px;color:{r['color']};font-weight:700;">{r['stance']}</span>
              <span style="font-size:12px;color:#94a3b8;flex:1.4;text-align:right;">근거: {r['driver']}</span>
            </div>""", unsafe_allow_html=True)

    st.divider()

    # ── Section 8. Risk Dashboard
    st.markdown("#### ⚠️ Risk Dashboard")
    rcols = st.columns(max(1, len(risk["items"])))
    for i, (name, val, lv, c) in enumerate(risk["items"]):
        with rcols[i]:
            st.markdown(
                f"""<div style="border:1px solid #2a2a35;border-radius:10px;padding:12px;background:#15151c;text-align:center;">
                  <div style="font-size:12px;color:#94a3b8;">{name}</div>
                  <div style="font-size:22px;font-weight:800;color:#f1f5f9;">{val}</div>
                  <div style="font-size:12px;color:{c};font-weight:700;">{lv}</div>
                </div>""", unsafe_allow_html=True)
    st.markdown(
        f"<div style='margin-top:8px;font-size:15px;'>종합 위험 수준: "
        f"<b style='color:{risk['color']};'>{risk['overall']}</b></div>", unsafe_allow_html=True)
    st.caption("🔎 AI Risk Assessment: " + risk["assess"])

    st.divider()

    # ── Section 9. Global Liquidity
    st.markdown("#### 🌍 Global Liquidity")
    if gm2.get("ok"):
        gc1, gc2, gc3 = st.columns(3)
        gc1.metric("Global M2 (합산)", f"${gm2['current']/1000:,.1f}T")
        yoy = gm2.get("yoy")
        gc2.metric("Global M2 YoY", f"{yoy:+.1f}%" if yoy is not None else "—")
        if yoy is None:
            tr, tc = "데이터 부족", C_YELLOW
        elif yoy >= 4:
            tr, tc = "확장 (Expanding)", C_GREEN
        elif yoy >= 0:
            tr, tc = "완만 (Mild)", C_YELLOW
        else:
            tr, tc = "위축 (Contracting)", C_RED
        gc3.metric("Trend", tr)
        impact = ("글로벌 유동성이 확장 중이라 위험자산에 우호적입니다." if (yoy or 0) >= 4
                  else "글로벌 유동성이 위축 중이라 위험자산에 부담입니다." if (yoy or 0) < 0
                  else "글로벌 유동성은 완만한 흐름입니다.")
        st.caption(f"🔎 Impact Assessment: {impact}")
        st.caption(f"포함 국가: {', '.join(gm2['included'])} · "
                   "각국 M2를 최신 환율로 USD 환산해 합산한 근사치(전세계 전체 아님)입니다.")
    else:
        st.write("Global M2 데이터를 불러오지 못했습니다.")

    st.divider()

    # ── Section 10. Biggest Changes This Week
    st.markdown("#### 📰 Biggest Changes This Week")
    if top_changes:
        for i, c in enumerate(top_changes, 1):
            color = C_GREEN if c["good"] else C_RED
            tag = "Positive for Risk Assets" if c["good"] else "Negative for Risk Assets"
            try:
                prev_s = c["fmt"].format(c["prev"]); last_s = c["fmt"].format(c["last"])
            except Exception:
                prev_s, last_s = f"{c['prev']:.2f}", f"{c['last']:.2f}"
            st.markdown(
                f"""<div style="border:1px solid #2a2a35;border-left:4px solid {color};
                            border-radius:10px;padding:12px 14px;background:#15151c;margin-bottom:6px;">
                  <span style="font-size:14px;font-weight:700;color:#f1f5f9;">{i}. {c['label']}</span>
                  <span style="font-size:13px;color:#cbd5e1;"> &nbsp;{prev_s} → {last_s} ({c['pct']:+.1f}%)</span><br>
                  <span style="font-size:12px;color:{color};font-weight:700;">{tag}</span>
                </div>""", unsafe_allow_html=True)
    else:
        st.write("이번 주 변화 데이터를 계산하지 못했습니다.")

    st.markdown("---")
    st.caption("⚠️ Macro Intelligence는 거시 환경 해석 보조 도구입니다. 투자 추천이 아니며, "
               "금리·침체·글로벌 유동성 지표는 공개 데이터 기반의 근사·추정치를 포함합니다.")


# ═════════════════════════════════════════════════════════════════════════════
#  탭 3) Model Allocation — 거시환경 기준 모델 포트폴리오 (투자자문 아님)
# ═════════════════════════════════════════════════════════════════════════════
ALLOC_COLORS = {
    "CASH": "#64748b", "BTC": "#f7931a", "QQQ": "#38bdf8",
    "SOXX": "#a78bfa", "GLD": "#eab308", "TLT": "#22c55e",
}
ALLOC_ORDER = ["CASH", "BTC", "QQQ", "SOXX", "GLD", "TLT"]


def _alloc_bar(weights: dict) -> str:
    """가로 스택 막대 HTML."""
    segs = []
    for k in ALLOC_ORDER:
        w = weights.get(k, 0)
        if w <= 0:
            continue
        nm = ASSET_KR[k].split(" (")[0]
        segs.append(
            f"<div style='width:{w}%;background:{ALLOC_COLORS[k]};display:flex;"
            f"align-items:center;justify-content:center;font-size:11px;color:#0b0b0f;"
            f"font-weight:700;' title='{nm} {w}%'>{w if w >= 8 else ''}</div>")
    return ("<div style='display:flex;height:34px;border-radius:8px;overflow:hidden;"
            "border:1px solid #2a2a35;'>" + "".join(segs) + "</div>")


def _alloc_legend(weights: dict) -> str:
    items = []
    for k in ALLOC_ORDER:
        w = weights.get(k, 0)
        nm = ASSET_KR[k].split(" (")[0]
        items.append(
            f"<span style='display:inline-flex;align-items:center;gap:6px;margin:3px 10px 3px 0;font-size:13px;color:#cbd5e1;'>"
            f"<span style='width:11px;height:11px;border-radius:3px;background:{ALLOC_COLORS[k]};display:inline-block;'></span>"
            f"{nm} <b style='color:#f1f5f9;'>{w}%</b></span>")
    return "<div style='margin-top:10px;'>" + "".join(items) + "</div>"


def render_model_allocation(ctx: dict):
    allocations = ctx["allocations"]; signals = ctx["signals"]
    weather = ctx["weather"]; liq = ctx["liq"]

    st.markdown("### 🧮 Macro Allocation Model")
    st.caption("투자 추천·자문이 아닙니다. 현재 거시환경 점수를 바탕으로, 환경이 우호적인 "
               "자산군을 상대적으로 높게 / 비우호적인 자산군을 낮게 환산한 모델 비중입니다. 총합 100%.")

    # 현재 환경 한 줄
    st.markdown(
        f"<div style='font-size:14px;color:#94a3b8;margin-bottom:6px;'>"
        f"현재 시장 환경: <b style='color:{weather['color']};'>{weather['emoji']} {weather['label']}</b>"
        f" · 유동성 점수 {liq['score']}/100</div>", unsafe_allow_html=True)

    # 기본(Balanced) 강조 표시
    base = allocations["Balanced"]
    st.markdown("#### 기준 모델 (Balanced)")
    st.markdown(_alloc_bar(base["weights"]), unsafe_allow_html=True)
    st.markdown(_alloc_legend(base["weights"]), unsafe_allow_html=True)
    st.caption("🔎 " + allocation_commentary(base, signals))

    st.divider()

    # 3개 모델 나란히
    st.markdown("#### 위험 성향별 3개 모델")
    st.caption("동일한 환경 데이터를 기반으로 비중만 다르게 산출합니다.")
    mcols = st.columns(3)
    for i, mname in enumerate(["Conservative", "Balanced", "Aggressive"]):
        alloc = allocations[mname]; meta = ALLOC_MODELS[mname]
        with mcols[i]:
            st.markdown(
                f"<div style='font-size:16px;font-weight:800;color:#f1f5f9;'>{mname}"
                f" <span style='font-size:13px;color:#94a3b8;'>({meta['kr']})</span></div>",
                unsafe_allow_html=True)
            st.markdown(_alloc_bar(alloc["weights"]), unsafe_allow_html=True)
            # 표
            rows = [{"자산": ASSET_KR[k].split(" (")[0], "비중": f"{alloc['weights'].get(k,0)}%"}
                    for k in ALLOC_ORDER]
            st.table(pd.DataFrame(rows))
            st.caption(meta["desc"])

    st.divider()

    # 모델 비교 표
    st.markdown("#### 모델 비교")
    comp = {"자산": [ASSET_KR[k].split(" (")[0] for k in ALLOC_ORDER]}
    for mname in ["Conservative", "Balanced", "Aggressive"]:
        comp[mname] = [f"{allocations[mname]['weights'].get(k,0)}%" for k in ALLOC_ORDER]
    st.table(pd.DataFrame(comp))

    st.markdown("---")
    st.caption("⚠️ 본 모델은 거시환경 데이터를 비중으로 환산한 교육·참고용 산출물입니다. "
               "특정 종목의 매수·매도·보유를 권유하는 투자자문이 아니며, 실제 투자 결정과 "
               "그 결과에 대한 책임은 전적으로 본인에게 있습니다.")


if __name__ == "__main__":
    main()