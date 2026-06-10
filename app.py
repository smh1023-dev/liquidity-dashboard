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
    """달러 시계열을 '십억 달러(Billion)' 단위로 통일."""
    if s.empty:
        return s
    # WALCL 은 백만 달러 단위 → 1000으로 나눠 십억으로
    if series_id == "WALCL":
        return s / 1000.0
    # WTREGEN, RRPONTSYD, M2SL 은 이미 십억 달러 단위
    return s


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

    # ── 에러 안내(부분 실패해도 계속 진행)
    if errors:
        st.warning("일부 지표를 불러오지 못했습니다: " + ", ".join(errors)
                   + ". 네트워크/거래소 응답 상태에 따라 잠시 후 새로고침하면 복구될 수 있습니다. "
                   "(불러온 지표만으로 점수를 계산했습니다.)")

    # ── 상단: 요약 지표
    st.markdown("### 📌 오늘의 요약")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("비트코인 현재가", f"${btc_price:,.0f}" if btc_price else "—")
    c2.metric("BTC 200일 이동평균", f"${btc_ma_val:,.0f}" if btc_ma_val else "—",
              delta=("200일선 위 ▲" if btc_above_ma else "200일선 아래 ▼")
              if btc_above_ma is not None else None)
    c3.metric("종합 점수", f"{'+' if total >= 0 else ''}{total}점")
    c4.metric("최종 판정", f"{interp['emoji']} {interp['label']}")

    # 점수 게이지(간단 바)
    pct = (total + 11) / 22 * 100  # -11~+11 → 0~100
    st.markdown(
        f"""<div style="background:#1f2937;border-radius:8px;height:10px;margin:4px 0 18px;">
            <div style="width:{pct:.0f}%;height:10px;border-radius:8px;background:{interp['color']};"></div>
        </div>""",
        unsafe_allow_html=True,
    )

    # ── 종합 리포트
    st.markdown("### 📝 종합 해석 리포트")
    st.info(generate_report(total, breakdown, changes, netliq_change, btc_above_ma))

    # ── 중단: 지표 카드
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

    # Net Liquidity + BTC 추세 카드
    cols2 = st.columns(3)
    with cols2[0]:
        render_card(
            "실제 시장 유동성 (Net Liquidity)",
            fmt_value("M2SL", netliq_change.get("current")),   # trillion 포맷 재사용
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

    # ── 하단: 그래프 8개
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

    # ── 대표 ETF 추세 (200일선)
    st.markdown("### 📈 대표 ETF 추세 (200일선)")
    st.caption("거시 유동성 점수와 별개로, 주요 지수의 가격 추세만 따로 보는 현황판입니다. "
               "현재가가 200일선 위면 상승 추세로 봅니다.")

    # 추세 요약 카드 (3개씩 줄바꿈)
    etf_keys = list(ETFS.keys())
    for row_start in range(0, len(etf_keys), 3):
        ecols = st.columns(3)
        for j, key in enumerate(etf_keys[row_start:row_start + 3]):
            meta = ETFS[key]
            stt = etf_state[key]
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

    # 추세 차트 (3개씩 줄바꿈)
    for row_start in range(0, len(etf_keys), 3):
        fcols = st.columns(3)
        for j, key in enumerate(etf_keys[row_start:row_start + 3]):
            meta = ETFS[key]
            stt = etf_state[key]
            fcols[j].plotly_chart(
                plot_price_ma_chart(stt["series"], stt["ma_series"], meta["kr"], period,
                                    ccy=meta["ccy"], name=key),
                use_container_width=True,
            )

    # ── 점수 산정 내역
    with st.expander("🔎 점수 산정 내역 보기"):
        rows = []
        name_map = {**{k: v["kr"] for k, v in INDICATORS.items()},
                    "BTC": "비트코인 200일선", "NETLIQ": "Net Liquidity"}
        for sid, b in breakdown.items():
            rows.append({"지표": name_map.get(sid, sid),
                         "상태": f"{b['emoji']} {b['status']}",
                         "점수": f"{'+' if b['score'] >= 0 else ''}{b['score']}"})
        st.table(pd.DataFrame(rows))
        st.caption("최종 점수 해석 — +7↑ 강세 가능성 / +3~+6 중립~강세 / "
                   "-2~+2 중립 / -3~-6 방어 필요 / -7↓ 위험 회피")

    st.markdown("---")
    st.caption("⚠️ 본 대시보드는 거시 유동성 환경을 판단하기 위한 참고 도구이며, "
               "매수·매도 신호나 투자 추천이 아닙니다. 모든 투자 판단과 책임은 본인에게 있습니다.")


if __name__ == "__main__":
    main()
