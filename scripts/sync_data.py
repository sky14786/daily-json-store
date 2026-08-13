#!/usr/bin/env python3
"""ETF 일일 수익률 데이터 동기화.

yfinance로 국내상장 ETF의 1주일/1개월/1년 수익률을 실제로 가져와 data/latest.json에
기록한다. 분류 기준·티커 목록은 이 저장소를 소비하는 쪽(Claude 루틴)의 문서를 따른다.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "latest.json"

TICKERS = [
    # 연금저축 핵심 보유 4종목은 2026-08-13 에 뺐다. portfolio-dashboard 가 실시간으로
    # 평가손익까지 보여주므로 여기서 또 추적할 이유가 없다. 여기 있던 이유는 Notion
    # "보유종목 표" 하나뿐이었고 그 표도 같이 없앴다.
    # 근거: vault Decisions/2026-08-12-dashboard-ticker-source-of-truth
    #
    # 단 overnight_estimates 는 그대로 둔다 — 아침에 "오늘 얼마나 갭 뜰까"를 보는
    # 용도라 대시보드가 대체하지 않으며, OVERNIGHT_BENCHMARKS 라는 별도 목록을 쓴다.
    ("494670", "TIGER 조선TOP10", "조선"),
    ("466920", "SOL 조선TOP3플러스", "조선"),
    ("396500", "TIGER Fn반도체TOP10", "반도체"),
    ("463250", "TIGER K방산&우주", "방산"),
    ("305540", "TIGER 2차전지테마", "2차전지"),
    ("305720", "KODEX 2차전지산업", "2차전지"),
    ("139250", "TIGER 200 에너지화학", "화학"),
    ("117460", "KODEX 에너지화학", "화학"),
    ("143860", "TIGER 헬스케어", "헬스케어"),
    ("266420", "KODEX 헬스케어", "헬스케어"),
    ("244580", "KODEX 바이오", "바이오"),
    ("261070", "TIGER KOSDAQ150바이오테크", "바이오"),
    ("091180", "KODEX 자동차", "자동차"),
    ("138540", "TIGER 현대차그룹플러스", "자동차"),
    ("069500", "KODEX 200", "코스피200"),
    ("229200", "KODEX 코스닥150", "코스닥150"),
    ("453640", "KODEX 미국S&P500헬스케어", "헬스케어(미국)"),
    ("218420", "KODEX 미국S&P500에너지", "에너지(미국)"),
    ("453660", "KODEX 미국S&P500경기소비재", "경기소비재"),
    ("453630", "KODEX 미국S&P500필수소비재", "필수소비재"),
    ("280930", "KODEX 미국러셀2000", "러셀2000"),
    ("133690", "TIGER 미국나스닥100", "나스닥100"),
    ("200030", "KODEX 미국S&P500산업재", "산업재"),
    ("418670", "TIGER 글로벌AI사이버보안", "사이버보안"),
    ("241180", "TIGER 일본니케이225", "니케이225"),
    ("101280", "KODEX 일본TOPIX100", "TOPIX"),
    ("195920", "TIGER 일본TOPIX(합성H)", "TOPIX"),
    ("352540", "KODEX 일본부동산리츠(H)", "일본리츠"),
    ("469160", "ACE 일본반도체", "일본반도체"),
    ("465660", "TIGER 일본반도체FACTSET", "일본반도체"),
    ("488480", "RISE 일본섹터TOP4Plus", "일본섹터"),
    ("292560", "TIGER 일본엔선물", "엔선물"),
]

OVERHEAT_1Y_PCT = 50.0

# 보유 4종목이 실제로 추종하는 미국 벤치마크(전부 환노출 상품). 방산(494840)은
# 미래에셋 자체 커스텀 10종목 지수라 완전히 같은 상품이 없어 ITA를 근사치로 씀 —
# 조사 근거는 vault ISA_SWING_TRADING_RULES_2026-08.md 참고.
OVERNIGHT_BENCHMARKS = [
    ("360750", "TIGER 미국S&P500", "SPY", True),
    ("458730", "TIGER 미국배당다우존스", "SCHD", True),
    ("453650", "KODEX 미국S&P500금융", "XLF", True),
    ("494840", "TIGER 미국방산TOP10", "ITA", False),  # 근사치, 정확히 같은 지수 아님
]
FX_TICKER = "KRW=X"


def pct_change(hist, back_rows):
    if len(hist) <= back_rows:
        return None
    latest = hist["Close"].iloc[-1]
    past = hist["Close"].iloc[-1 - back_rows]
    return round((latest / past - 1) * 100, 1)


def fetch_one(code, name, theme):
    ticker = f"{code}.KS"
    try:
        hist = yf.Ticker(ticker).history(period="1y")
    except Exception as e:  # noqa: BLE001
        return {"code": code, "name": name, "theme": theme, "error": str(e)}

    if hist.empty:
        return {"code": code, "name": name, "theme": theme, "error": "no data"}

    pct_1d = pct_change(hist, 1)
    pct_1w = pct_change(hist, 5)
    pct_1m = pct_change(hist, 21)
    first_close = hist["Close"].iloc[0]
    last_close = hist["Close"].iloc[-1]
    pct_1y = round((last_close / first_close - 1) * 100, 1)

    return {
        "code": code,
        "name": name,
        "theme": theme,
        "pct_1d": pct_1d,
        "pct_1w": pct_1w,
        "pct_1m": pct_1m,
        "pct_1y": pct_1y,
    }


def classify(row):
    if row.get("error"):
        return "확인 안 됨"
    if row["pct_1y"] is not None and row["pct_1y"] > OVERHEAT_1Y_PCT:
        return "과열"
    if row["pct_1m"] is None:
        return "확인 안 됨"
    return "하락" if row["pct_1m"] < 0 else "통과"


def last_1d_pct(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="5d")
    except Exception as e:  # noqa: BLE001
        return None, str(e)
    if len(hist) < 2:
        return None, "no data"
    return round((hist["Close"].iloc[-1] / hist["Close"].iloc[-2] - 1) * 100, 2), None


def build_overnight_estimates():
    """간밤 미국장 마감 기준으로, 보유종목이 오늘 한국장에서 얼마나 갭띄고
    시작할지 추정치. 실제 벤치마크(SPY/SCHD/XLF/ITA) 1일 수익률 + 원/달러
    환율 1일 변동을 합쳐서 계산한다(둘 다 환노출 상품이라 원화환산 시 그대로
    더해짐). 한국장이 아직 안 열린 아침 시점에만 의미가 있는 수치 — 오후엔
    실제 마감가가 이미 이걸 반영해서 나오므로 참고용일 뿐."""
    fx_pct, fx_err = last_1d_pct(FX_TICKER)
    estimates = []
    for code, name, benchmark, is_exact in OVERNIGHT_BENCHMARKS:
        bench_pct, bench_err = last_1d_pct(benchmark)
        if bench_err or fx_err or bench_pct is None or fx_pct is None:
            estimates.append({"code": code, "name": name, "benchmark": benchmark,
                               "is_exact_match": is_exact, "error": bench_err or fx_err})
            continue
        implied_krw_pct = round(((1 + bench_pct / 100) * (1 + fx_pct / 100) - 1) * 100, 2)
        estimates.append({
            "code": code, "name": name, "benchmark": benchmark, "is_exact_match": is_exact,
            "benchmark_1d_pct": bench_pct, "usdkrw_1d_pct": fx_pct,
            "implied_overnight_pct": implied_krw_pct,
        })
        print(f"overnight estimate {code} {name} via {benchmark}: "
              f"benchmark={bench_pct}%, USDKRW={fx_pct}%, implied={implied_krw_pct}%",
              file=sys.stderr)
    return estimates


def main():
    rows = []
    for code, name, theme in TICKERS:
        row = fetch_one(code, name, theme)
        row["classification"] = classify(row)
        rows.append(row)
        print(f"{code} {name}: {row.get('classification')} "
              f"(1d={row.get('pct_1d')}, 1w={row.get('pct_1w')}, 1m={row.get('pct_1m')}, 1y={row.get('pct_1y')})",
              file=sys.stderr)

    counts = {}
    for r in rows:
        counts[r["classification"]] = counts.get(r["classification"], 0) + 1

    output = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "overheat_threshold_1y_pct": OVERHEAT_1Y_PCT,
        "counts": counts,
        "rows": rows,
        "overnight_estimates": build_overnight_estimates(),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT_PATH} ({len(rows)} tickers, counts={counts})", file=sys.stderr)


if __name__ == "__main__":
    main()
