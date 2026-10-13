#!/usr/bin/env python3
"""提取山西、内蒙古官方 PDF 景区名录，校验序号和分组总数。需要 pdfplumber。"""

from collections import Counter
from io import BytesIO
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen

from fetch_mct import atomic_write_json, iso_timestamp
from fetch_provincial_tourism import CACHE, ROOT, to_item

SOURCES = [
    dict(id="shanxi", province="山西省", name="山西省文化和旅游厅", date="", asOf="2025-01",
         url="https://wlt.shanxi.gov.cn/xwzx/tzgg/202502/P020250210597505427672.pdf", file="shanxi.pdf", expected=451),
    dict(id="neimenggu", province="内蒙古自治区", name="内蒙古自治区文化和旅游厅", date="2024-02-26", asOf="2024-02-26",
         url="https://wlt.nmg.gov.cn/zfxxgk/zfxxglzl/fdzdgknr/zdlyxx/zyml/202402/t20240226_2472006.html",
         download="https://wlt.nmg.gov.cn/zfxxgk/zfxxglzl/fdzdgknr/zdlyxx/zyml/202402/P020240226397569270813.pdf",
         file="neimenggu.pdf", expected=457),
]


def extract_records(source: dict, content: bytes) -> list[dict]:
    import pdfplumber
    records, numbers, city_totals = [], [], {}
    city = ""
    with pdfplumber.open(BytesIO(content)) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            if source["id"] == "shanxi":
                for table in page.extract_tables():
                    for row in table:
                        group = re.fullmatch(r"(.+市)[：:](\d+)家", row[0] or "")
                        if group:
                            city = group[1]
                            city_totals[city] = int(group[2])
                        elif (row[0] or "").isdigit():
                            if len(row) != 4 or not city or not re.fullmatch(r"[1-5]A", row[2] or ""):
                                raise ValueError(f"山西第 {page_number} 页出现无法解析的景点行")
                            numbers.append(int(row[0]))
                            records.append(dict(name=row[1].replace("\n", ""), city=city, grade=row[2],
                                                sheet=f"page-{page_number}", row=int(row[0])))
            else:
                # 此 PDF 每页第一行没有完整上边框，表格提取会漏掉 19 行，使用文本并校验连续序号。
                for line in (page.extract_text() or "").splitlines():
                    if not re.match(r"^\d+\s", line):
                        continue
                    match = re.fullmatch(r"(\d+)\s+(.+?)\s+(\S+)\s+([1-5]A?)", line)
                    if not match:
                        raise ValueError(f"内蒙古第 {page_number} 页出现无法解析的景点行：{line}")
                    numbers.append(int(match[1]))
                    grade = match[4] if match[4].endswith("A") else match[4] + "A"
                    records.append(dict(name=match[2], city=match[3], grade=grade,
                                        sheet=f"page-{page_number}", row=int(match[1])))
    if numbers != list(range(1, source["expected"] + 1)):
        raise ValueError(f"{source['province']}名录序号不完整，不生成结果")
    if city_totals and Counter(record["city"] for record in records) != city_totals:
        raise ValueError("山西各市景区数量与 PDF 汇总不一致")
    return records


def main() -> int:
    from hashlib import sha256
    items, originals, sources = [], [], []
    for source in SOURCES:
        path = CACHE / source["file"]
        if not path.exists():
            request = Request(source.get("download", source["url"]), headers={"User-Agent": "roam-public-tourism-catalog/1.0"})
            with urlopen(request, timeout=30) as response:
                content = response.read()
            extract_records(source, content)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        content = path.read_bytes()
        records = extract_records(source, content)
        converted = [to_item(source, record) for record in records]
        originals.extend(converted)
        additions = [item for item in converted if item["classifications"][0]["value"] != "5A"]
        items.extend(additions)
        sources.append({**source, "officialRows": len(records), "includedRows": len(additions),
                        "sha256": sha256(content).hexdigest(), "rawFile": str(path)})
        print(f"{source['province']}：名录 {len(records)} 条，增补候选 {len(additions)} 条", flush=True)
    if len({item["id"] for item in items}) != len(items):
        raise ValueError("PDF 名录存在同地区同名记录，需核实")
    atomic_write_json(ROOT / "tmp/01-PDF文旅增补数据.json", {
        "schemaVersion": 1, "stage": "attraction-source", "generatedAt": iso_timestamp(),
        "coverageNote": "以原始 PDF 所载日期为准，增补其中 1A–4A 景区。",
        "sources": sources, "sourceRecords": originals, "items": items,
    })
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ImportError, ValueError, OSError) as error:
        print(f"PDF 名录未生成：{error}", file=sys.stderr)
        raise SystemExit(1)
