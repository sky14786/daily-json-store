#!/usr/bin/env python3
"""ISA 리포트의 기계적인 부분(표·아카이브)을 Notion API로 직접 쓴다.

과열/하락/통과 분류, 보유종목 표, 관심후보 표, 상세표, 어제자 아카이브까지 전부
이 스크립트가 결정론적으로 처리한다. Claude 루틴은 이제 신규발굴·시장폭 코멘트만
"오늘의 시황 코멘트" 페이지에 쓰면 된다(이 스크립트가 건드리지 않는 영역).

메인 페이지 레이아웃(고정, 이 스크립트가 [정적 영역] 아래를 매번 재작성):
  [0] 안내문 paragraph (정적)
  [1] 이전기록/시황코멘트 mention 링크 paragraph (정적)
  [2] divider (정적)
  [3..] 오늘자 데이터 (매 실행마다 삭제 후 재작성, 필요하면 먼저 아카이브)
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

NOTION_VERSION = "2022-06-28"
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")

MAIN_PAGE_ID = "3b343a8e-e493-81ab-ad38-df5dcffc2f74"
ARCHIVE_HUB_ID = "3b443a8e-e493-8144-a563-d426b07a7c69"
DETAIL_PAGE_ID = "3b443a8e-e493-812f-85ee-fb14faa2b425"
COMMENTARY_PAGE_ID = "3b443a8e-e493-8138-b0ba-d047e0a4f340"

HOLDINGS = [
    ("360750", "TIGER 미국S&P500", "30%"),
    ("458730", "TIGER 미국배당다우존스", "30%"),
    ("453650", "KODEX 미국S&P500금융", "20%"),
    ("494840", "TIGER 미국방산TOP10", "20%"),
]


def _request(method, path, body=None):
    url = f"https://api.notion.com/v1{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8")
        raise RuntimeError(f"Notion API {method} {path} -> {e.code}: {detail}") from e


def get_children(block_id):
    out, cursor = [], None
    while True:
        q = f"?start_cursor={cursor}" if cursor else ""
        res = _request("GET", f"/blocks/{block_id}/children{q}")
        out.extend(res["results"])
        if not res.get("has_more"):
            return out
        cursor = res["next_cursor"]


def append_children(block_id, children, after=None):
    for i in range(0, len(children), 90):
        body = {"children": children[i:i + 90]}
        if after:
            body["after"] = after
        res = _request("PATCH", f"/blocks/{block_id}/children", body)
        time.sleep(0.3)
        # 다음 배치는 방금 넣은 마지막 블록 뒤에 이어붙여야 순서가 안 꼬인다.
        if after and res.get("results"):
            after = res["results"][-1]["id"]


def delete_block(block_id):
    _request("DELETE", f"/blocks/{block_id}")
    time.sleep(0.3)


def sanitize_rich_text(rt_list):
    """GET 응답의 rich_text에는 plain_text/href 등 생성 시 못 쓰는 부가 필드가
    섞여 있어(멘션은 참조 페이지의 icon 같은 것까지 딸려옴) 필요한 필드만 남긴다."""
    out = []
    for rt in rt_list:
        t = rt["type"]
        item = {"type": t}
        if t == "text":
            item["text"] = {"content": rt["text"]["content"], "link": rt["text"].get("link")}
        elif t == "mention":
            m = rt["mention"]
            if m["type"] == "page":
                item["mention"] = {"type": "page", "page": {"id": m["page"]["id"]}}
            else:
                continue  # 다른 멘션 종류는 이 스크립트에서 안 씀
        elif t == "equation":
            item["equation"] = {"expression": rt["equation"]["expression"]}
        else:
            continue
        ann = rt.get("annotations")
        if ann:
            item["annotations"] = {k: ann[k] for k in
                                    ("bold", "italic", "strikethrough", "underline", "code", "color")
                                    if k in ann and ann[k] not in (False, "default")}
        out.append(item)
    return out


def strip_block_for_recreation(block):
    """children()이 돌려준 block 객체를 그대로 재생성 payload로 못 쓴다(plain_text,
    icon 등 읽기 전용 부가 필드가 섞여 있어 POST 시 validation_error가 남) —
    타입별로 필요한 필드만 골라 새로 만든다. table은 행(table_row)이 목록 조회에
    안 딸려오므로(has_children만 true) 따로 가져와 채운다."""
    t = block["type"]
    if t in ("paragraph", "heading_1", "heading_2", "heading_3"):
        content = {"rich_text": sanitize_rich_text(block[t]["rich_text"])}
    elif t == "table":
        content = {
            "table_width": block["table"]["table_width"],
            "has_column_header": block["table"]["has_column_header"],
            "has_row_header": block["table"]["has_row_header"],
        }
        if block.get("has_children"):
            rows = get_children(block["id"])
            content["children"] = [strip_block_for_recreation(r) for r in rows]
    elif t == "table_row":
        content = {"cells": [sanitize_rich_text(cell) for cell in block["table_row"]["cells"]]}
    elif t == "divider":
        content = {}
    else:
        raise ValueError(f"strip_block_for_recreation: unsupported block type {t!r}")
    return {"type": t, t: content}


def text_rich(content, color=None, bold=False):
    ann = {}
    if color:
        ann["color"] = color
    if bold:
        ann["bold"] = True
    obj = {"type": "text", "text": {"content": content}}
    if ann:
        obj["annotations"] = ann
    return obj


def mention_page_rich(page_id):
    return {"type": "mention", "mention": {"type": "page", "page": {"id": page_id}}}


def pct_cell(value):
    if value is None:
        return [text_rich("확인 안 됨")]
    if value > 0:
        return [text_rich(f"+{value}%", color="red")]
    if value < 0:
        return [text_rich(f"{value}%", color="blue")]
    return [text_rich("0.0%")]


def heading2(text):
    return {"type": "heading_2", "heading_2": {"rich_text": [text_rich(text)]}}


def paragraph(rich_text_list):
    return {"type": "paragraph", "paragraph": {"rich_text": rich_text_list}}


def table_block(header, rows, widths=None):
    def row(cells):
        return {"type": "table_row", "table_row": {"cells": cells}}

    children = [row([[text_rich(h)] for h in header])]
    for r in rows:
        children.append(row(r))
    return {
        "type": "table",
        "table": {
            "table_width": len(header),
            "has_column_header": True,
            "has_row_header": False,
            "children": children,
        },
    }


def build_holdings_table(rows_by_code):
    header = ["종목코드", "종목명", "비중", "1일", "1주일", "1개월", "1년"]
    rows = []
    for code, name, weight in HOLDINGS:
        r = rows_by_code.get(code)
        if r is None or r.get("error"):
            rows.append([[text_rich(code)], [text_rich(name)], [text_rich(weight)],
                         [text_rich("확인 안 됨")], [text_rich("확인 안 됨")],
                         [text_rich("확인 안 됨")], [text_rich("확인 안 됨")]])
            continue
        rows.append([[text_rich(code)], [text_rich(name)], [text_rich(weight)],
                     pct_cell(r["pct_1d"]), pct_cell(r["pct_1w"]),
                     pct_cell(r["pct_1m"]), pct_cell(r["pct_1y"])])
    return table_block(header, rows)


def build_classification_table(rows, with_theme=True):
    header = ["종목코드", "종목명"] + (["테마"] if with_theme else []) + ["1일", "1주일", "1개월", "1년"]
    out_rows = []
    for r in rows:
        cells = [[text_rich(r["code"])], [text_rich(r["name"])]]
        if with_theme:
            cells.append([text_rich(r["theme"])])
        cells += [pct_cell(r.get("pct_1d")), pct_cell(r.get("pct_1w")),
                  pct_cell(r.get("pct_1m")), pct_cell(r.get("pct_1y"))]
        out_rows.append(cells)
    return table_block(header, out_rows)


def find_date_heading(blocks):
    for b in blocks:
        if b["type"] == "heading_2":
            rt = b["heading_2"]["rich_text"]
            if rt:
                return rt[0]["plain_text"] if "plain_text" in rt[0] else rt[0]["text"]["content"]
    return None


def sync_main_page(data, today_str, commentary_page_id):
    """메인 페이지 갱신. child_page 블록(하위페이지 4개: 이전기록/상세표/아침브리핑/
    시황코멘트)은 절대 건드리지 않는다 — 이 블록을 지우면 그 하위페이지 자체가
    삭제/이동될 위험이 있다(Notion API 특성). intro 문단과 divider도 그대로 두고,
    그 사이(intro/divider 다음 ~ child_page 블록들 전) 영역만 재작성한다."""
    holdings_codes = {c for c, _, _ in HOLDINGS}
    rows_by_code = {r["code"]: r for r in data["rows"]}
    all_rows = data["rows"]

    non_holding = [r for r in all_rows if r["code"] not in holdings_codes]
    passed = [r for r in non_holding if r.get("classification") == "통과"]
    overheat = [r for r in non_holding if r.get("classification") == "과열"]
    declined = [r for r in non_holding if r.get("classification") == "하락"]
    declined_sorted = sorted(declined, key=lambda r: (r.get("pct_1w") if r.get("pct_1w") is not None else -999), reverse=True)
    watchlist = declined_sorted[:3]

    children = get_children(MAIN_PAGE_ID)
    child_page_ids = {b["id"] for b in children if b["type"] == "child_page"}
    dividers = [b for b in children if b["type"] == "divider"]
    anchor_id = dividers[0]["id"] if dividers else children[0]["id"]
    dynamic = [b for b in children if b["id"] not in child_page_ids and b["type"] != "divider"
               and b["id"] != children[0]["id"]]

    if dynamic:
        old_date = find_date_heading(dynamic)
        if old_date and old_date != today_str:
            archive_children = [strip_block_for_recreation(b) for b in dynamic]
            new_page = _request("POST", "/pages", {
                "parent": {"page_id": ARCHIVE_HUB_ID},
                "properties": {"title": {"title": [{"text": {"content": old_date}}]}},
                "children": archive_children,
            })
            # 주의: parent를 이 페이지로 지정해 만들면 Notion이 허브 페이지 본문에
            # child_page 블록을 자동으로 추가한다 — 따로 mention 링크를 또 추가하면
            # 중복이 생기므로(실제로 겪음) 여기서는 아무것도 더 안 붙인다.
            print(f"archived {old_date} -> {new_page['url']}", file=sys.stderr)
        for b in dynamic:
            delete_block(b["id"])

    new_blocks = [heading2(today_str), build_holdings_table(rows_by_code)]
    new_blocks.append(heading2(f"통과 종목 (ISA 스윙 후보, {len(passed)}개)"))
    if passed:
        new_blocks.append(build_classification_table(passed))
    else:
        new_blocks.append(paragraph([text_rich("통과 없음 — 매매 보류 권고", bold=True)]))
    new_blocks.append(heading2("관심후보 (하락 중 1주일 수익률 상위 3)"))
    if watchlist:
        new_blocks.append(build_classification_table(watchlist))
    new_blocks.append(paragraph([
        text_rich(f"과열 {len(overheat)}개 · 하락 {len(declined)}개 — 전체 표는 "),
        mention_page_rich(DETAIL_PAGE_ID),
        text_rich(", 오늘의 발굴·시황 코멘트는 "),
        mention_page_rich(commentary_page_id),
        text_rich(" 참고"),
    ]))
    append_children(MAIN_PAGE_ID, new_blocks, after=anchor_id)
    print(f"main page updated: 통과 {len(passed)} / 과열 {len(overheat)} / 하락 {len(declined)}", file=sys.stderr)


def sync_detail_page(data):
    holdings_codes = {c for c, _, _ in HOLDINGS}
    non_holding = [r for r in data["rows"] if r["code"] not in holdings_codes]
    overheat = [r for r in non_holding if r.get("classification") == "과열"]
    declined = [r for r in non_holding if r.get("classification") == "하락"]

    for b in get_children(DETAIL_PAGE_ID):
        delete_block(b["id"])

    blocks = [heading2(f"과열 종목 ({len(overheat)}개)")]
    if overheat:
        blocks.append(build_classification_table(overheat))
    blocks.append(heading2(f"하락 종목 ({len(declined)}개)"))
    if declined:
        blocks.append(build_classification_table(declined))
    append_children(DETAIL_PAGE_ID, blocks)
    print(f"detail page updated: 과열 {len(overheat)} / 하락 {len(declined)}", file=sys.stderr)


def main():
    if not NOTION_TOKEN:
        print("NOTION_TOKEN not set, skipping Notion sync", file=sys.stderr)
        return
    data_path = Path(__file__).resolve().parent.parent / "data" / "latest.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    generated_utc = datetime.fromisoformat(data["generated_at_utc"].replace("Z", "+00:00"))
    today_str = (generated_utc + timedelta(hours=9)).strftime("%Y-%m-%d")
    sync_detail_page(data)
    sync_main_page(data, today_str, COMMENTARY_PAGE_ID)


if __name__ == "__main__":
    main()
