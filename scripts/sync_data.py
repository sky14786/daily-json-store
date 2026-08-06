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
    ("453650", "KODEX 미국S&P500금융", "금융"),
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

    pct_1w = pct_change(hist, 5)
    pct_1m = pct_change(hist, 21)
    first_close = hist["Close"].iloc[0]
    last_close = hist["Close"].iloc[-1]
    pct_1y = round((last_close / first_close - 1) * 100, 1)

    return {
        "code": code,
        "name": name,
        "theme": theme,
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


def main():
    rows = []
    for code, name, theme in TICKERS:
        row = fetch_one(code, name, theme)
        row["classification"] = classify(row)
        rows.append(row)
        print(f"{code} {name}: {row.get('classification')} "
              f"(1w={row.get('pct_1w')}, 1m={row.get('pct_1m')}, 1y={row.get('pct_1y')})",
              file=sys.stderr)

    counts = {}
    for r in rows:
        counts[r["classification"]] = counts.get(r["classification"], 0) + 1

    output = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "overheat_threshold_1y_pct": OVERHEAT_1Y_PCT,
        "counts": counts,
        "rows": rows,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT_PATH} ({len(rows)} tickers, counts={counts})", file=sys.stderr)


if __name__ == "__main__":
    main()
