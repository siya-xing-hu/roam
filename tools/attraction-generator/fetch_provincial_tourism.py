#!/usr/bin/env python3
"""读取省级官方 A 级景区名录，保存原件并生成独立的增补资料。"""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from fetch_local_tourism import TableParser, compact, expand_table, grade_of
from fetch_mct import atomic_write_json, clean_text, iso_timestamp

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "tmp/expansion"
OUTPUT = ROOT / "tmp/01-省级文旅增补数据.json"

# 日期是官方名录的公布日期，不用下载日期冒充资料更新日期。
# 每个来源只读取已核对的列。原件的全部行保留在缓存中。
SOURCES = [
    dict(id="zhejiang", province="浙江省", name="浙江省文化广电和旅游厅", date="2025-01-21",
         url="https://ct.zj.gov.cn/art/2025/1/21/art_1229678763_5447295.html",
         download="https://ct.zj.gov.cn/api-gateway/jpaas-web-server/front/document/file-download?fileUrl=/cms_files/jcms1/web3214/site/attach/0/a185e69bcc144304b3bf82b9d8b8a839.xlsx",
         file="zhejiang.xlsx", columns={"name": "景区名称", "city": "市", "district": "区", "grade": "景区当前等级"}),
    dict(id="fujian", province="福建省", name="福建省文化和旅游厅", date="2026-01-05",
         url="https://wlt.fujian.gov.cn/zwgk/sjfb/cgcx/AJJQ/",
         download="https://wlt.fujian.gov.cn/zwgk/sjfb/cgcx/AJJQ/202601/P020260105588644254919.xlsx",
         file="fujian.xlsx", columns={"name": "景区名称", "city": "所在地市", "district": "县（市）区", "grade": "等级"}),
    dict(id="liaoning", province="辽宁省", name="辽宁省文化和旅游厅", date="2026-01-06",
         url="https://whly.ln.gov.cn/whly/wlzt/sjly/ajjq/2026010617062543557/index.shtml",
         download="https://whly.ln.gov.cn/whly/attachDir/2026/01/2026010616595821538.xlsx",
         file="liaoning.xlsx", columns={"name": "景区名称", "city": "市", "district": "县、区", "grade": "景区当前等级"}),
    dict(id="sichuan", province="四川省", name="四川省文化和旅游厅", date="2026-01-07",
         url="https://wlt.sc.gov.cn/scwlt/c100297/introduce.shtml", file="sichuan.html",
         columns={"name": "名称", "city": "所在市州", "district": "所在区县", "grade": "等级", "address": "地址"}),
    dict(id="jilin", province="吉林省", name="吉林省文化和旅游厅", date="2025-11-20",
         url="https://whhlyt.jl.gov.cn/ggfw/lyfw/jng/202405/t20240524_8912725.html", file="jl.html",
         columns={"name": "景区名称", "region": "所在地", "grade": "等级"}),
    dict(id="guizhou", province="贵州省", name="贵州省文化和旅游厅", date="2026-02-09",
         url="https://whhly.guizhou.gov.cn/ggfw/whlyml/lyjqml/202602/t20260209_89470788.html", file="guizhou.html",
         columns={"name": "旅游景区名称", "city": "市州", "grade": "质量等级", "address": "详细地址"}),
    dict(id="gansu", province="甘肃省", name="甘肃省人民政府一带一路网", date="2024-07-16",
         url="https://ydyl.gansu.gov.cn/gsydyl/gjjl/szsl/202407/t20240716_21279.html", file="gansu.html",
         columns={"name": "景区名称", "city": "所在市州", "grade": "等级", "address": "景区详细地址"}),
    dict(id="ningxia", province="宁夏回族自治区", name="宁夏回族自治区文化和旅游厅", date="2024-12-12",
         url="https://whhlyt.nx.gov.cn/wlgz/202412/t20241212_4756344.html", file="nx.html",
         columns={"name": "名称", "city": "地区", "grade": "当前等级"}),
    dict(id="xizang", province="西藏自治区", name="西藏自治区文化和旅游厅", date="2025-10-23",
         url="https://wlt.xizang.gov.cn/zwgk_69/gkml/ggfw/202510/t20251023_505529.html", file="xizang.html",
         columns={"name": "景区名称", "grade": "等级"}),
    dict(id="hebei", province="河北省", name="河北省文化和旅游厅", date="2026-01-30",
         url="https://whly.hebei.gov.cn/c/2026-01-30/584097.html",
         download="https://whly.hebei.gov.cn/zwf/ui/contentcore/resource/download?ID=48084", file="hebei.xlsx",
         columns={"name": "景区名称", "region": "所在地市", "grade": "等级"}),
    dict(id="shandong", province="山东省", name="山东省文化和旅游厅", date="2026-01-01",
         url="http://whhly.shandong.gov.cn/art/2026/1/1/art_100554_10354829.html?xxgkhide=1",
         download="http://whhly.shandong.gov.cn/module/download/downfile.jsp?classid=0&filename=67f824db4da14fa8a789204057d8443a.xlsx",
         file="shandong.xlsx", citySuffix="市",
         columns={"name": "景区名称", "city": "所在地市", "district": "所在区县", "grade": "等级"}),
    dict(id="heilongjiang", province="黑龙江省", name="黑龙江省文化和旅游厅", date="2024-05-24",
         url="https://wlt.hlj.gov.cn/wlt/c114268/202405/c00_31737191.shtml",
         download="https://wlt.hlj.gov.cn/wlt/c114268/202405/31737191/files/2023年全省A级旅游景区名录.xlsx",
         file="hlj.xlsx", columns={"name": "景区名称", "city": "所在地市", "grade": "等级", "address": "通讯地址"}),
    dict(id="shaanxi", province="陕西省", name="陕西省文化和旅游厅", date="2024-01-03",
         url="https://whhlyt.shaanxi.gov.cn/zfxxgk/fdzdgknr/tjxx/202401/t20240103_2578364.html",
         download="https://whhlyt.shaanxi.gov.cn/zfxxgk/fdzdgknr/tjxx/202401/P020240903542028137444.xlsx",
         file="shaanxi.xlsx", columns={"name": "景区名称", "city": "地市", "grade": "等级", "address": "景区地址"}),
    dict(id="xinjiang", province="新疆维吾尔自治区", name="新疆维吾尔自治区文化和旅游厅", date="2025-06-03",
         url="https://wlt.xinjiang.gov.cn/wlt/4ajjq/202506/ce14fc34f16a498c930658ef2713d29f.shtml",
         download="https://wlt.xinjiang.gov.cn/wlt/4ajjq/202506/ce14fc34f16a498c930658ef2713d29f/files/新疆4A级旅游景区名录（截至2025年6月3日）.xlsx",
         file="xinjiang.xlsx", columns={"name": "景区", "city": "地区自治州", "district": "区县", "grade": "景区级别"}),
    dict(id="hubei", province="湖北省", name="湖北省文化和旅游厅", date="",
         url="https://wlt.hubei.gov.cn/bsfw/bmcxfw/ajlvjqmd/",
         download="https://wlt.hubei.gov.cn/bsfw/bmcxfw/ajlvjqmd/list.json", file="hubei.json", citySuffix="市",
         cityNames={"恩施": "恩施土家族苗族自治州", "神农架": "神农架林区", "神龙架": "神农架林区"},
         columns={"name": "JQMC", "city": "DQ", "grade": "ZLDJ", "address": "TXDZ"}),
    dict(id="chongqing", province="重庆市", name="重庆市文化和旅游发展委员会", date="2026-01",
         url="https://www.cq.gov.cn/zjcq/cycq/zmjd/zqajjq/", file="cq.html", format="inline-json",
         columns={"name": "name", "district": "place", "grade": "level", "address": "linkInfo"}),
    dict(id="guangdong", province="广东省", name="广东省文化和旅游厅", date="2025-09-02",
         url="https://whly.gd.gov.cn/service_newajjq/content/post_4767139.html",
         download="https://whly.gd.gov.cn/attachment/0/591/591633/4767139.xls", file="gd.xls", citySuffix="市",
         columns={"name": "景区名称", "city": "所在地市", "grade": "等级"}),
    dict(id="henan", province="河南省", name="河南省文化和旅游厅", date="2025-01-21",
         url="https://hct.henan.gov.cn/2025/01-21/3115059.html",
         download="https://oss.henan.gov.cn/typtfile/20250121/6d86a9f1da484bebbce727db7d546e6b.xlsx", file="henan.xlsx",
         cityNames={"济源示范区": "济源市"},
         columns={"name": "景区名称", "city": "所在地市", "grade": "等级"}),
    dict(id="hunan", province="湖南省", name="湖南省文化和旅游厅", date="2025-02-25",
         url="https://whhlyt.hunan.gov.cn/whhlyt/cyfz/ggxxcx/lyajjqcx/202404/t20240407_33271070.html",
         download="https://whhlyt.hunan.gov.cn/whhlyt/cyfz/ggxxcx/lyajjqcx/202404/33271070/files/3f8384ec1a6a4cfc999fbaa7ff9a0a53.doc", file="hunan.doc",
         gradeMayPrecedeRegion=True,
         columns={"name": "景区名称", "region": "所在地市", "grade": "等级"}),
    dict(id="tianjin", province="天津市", name="天津市文化和旅游局", date="2026-03-05",
         url="https://whly.tj.gov.cn/WSBSYZXBS4230/WMFW8706/QYML408/202008/t20200817_3486782.html",
         download="https://whly.tj.gov.cn/igs/front/search/list.html?index=tjswhlyj_1&type=ajjqml&filter%5BAVAILABLE%5D=true&pageSize=50&orderProperty=JQJB&orderDirection=desc",
         file="tianjin.json", format="paged-tianjin",
         columns={"name": "DOCTITLE", "grade": "JQJB", "address": "DZYB"}),
    dict(id="guangxi", province="广西壮族自治区", name="广西壮族自治区文化和旅游厅", date="",
         url="http://wlt.gxzf.gov.cn/zfxxgk/fdzdgknr/bmcxfw/lyjdjqcx/",
         download="http://wlt.gxzf.gov.cn/irs/front/list", file="guangxi.json", format="paged-guangxi",
         columns={"name": "f_2023817820739", "region": "f_2023818369421", "grade": "f_2023818268086", "address": "f_2023818910485"}),
    dict(id="jiangxi", province="江西省", name="江西省文化和旅游厅", date="2026-04-26",
         url="https://dct.jiangxi.gov.cn/jxswhhlyt/col/col14552/content/content_2049314821572136960.html",
         download="https://dct.jiangxi.gov.cn/jxswhhlyt/col14552/2049314821572136960/vAy5IMqU.xlsx",
         file="jiangxi-202604.xlsx", gradeFromSheet=True,
         columns={"name": "景区名称", "city": "设区市", "district": "区县", "grade": "等级"},
         sheetColumns={"2A景区": {"name": "景区名称", "city": "地区", "grade": "等级"}}),
]


def column_number(value: str) -> int:
    result = 0
    for char in value:
        result = result * 26 + ord(char) - ord("A") + 1
    return result - 1


def xlsx_tables(content: bytes) -> list[tuple[str, list[list[str]]]]:
    """读取值和明确的合并单元格；不把普通空白单元格向下填充。"""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(BytesIO(content)) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(node.itertext()) for node in
                       ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("m:si", ns)]
        relationships = {node.attrib["Id"]: node.attrib["Target"] for node in
                         ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
        result = []
        for sheet in ET.fromstring(archive.read("xl/workbook.xml")).findall("m:sheets/m:sheet", ns):
            rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = relationships[rel_id]
            path = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            root = ET.fromstring(archive.read(path))
            cells = {}
            for cell in root.findall("m:sheetData/m:row/m:c", ns):
                match = re.fullmatch(r"([A-Z]+)(\d+)", cell.attrib["r"])
                if not match:
                    raise ValueError("表格单元格坐标格式不符")
                row, col = int(match[2]) - 1, column_number(match[1])
                value = cell.findtext("m:v", default="", namespaces=ns)
                if cell.attrib.get("t") == "s":
                    value = strings[int(value)]
                elif cell.attrib.get("t") == "inlineStr":
                    value = "".join(t.text or "" for t in cell.findall("m:is//m:t", ns))
                if value:
                    cells[row, col] = clean_text(value)
            for merge in root.findall("m:mergeCells/m:mergeCell", ns):
                match = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", merge.attrib["ref"])
                if not match:
                    raise ValueError("合并单元格范围不符")
                start_row, end_row = int(match[2]) - 1, int(match[4])
                start_col, end_col = column_number(match[1]), column_number(match[3]) + 1
                value = cells.get((start_row, start_col), "")
                for row in range(start_row, end_row):
                    for col in range(start_col, end_col):
                        cells[row, col] = value
            if cells:
                height = max(row for row, _ in cells) + 1
                width = max(col for _, col in cells) + 1
                result.append((sheet.attrib["name"], [[cells.get((r, c), "") for c in range(width)]
                                                     for r in range(height)]))
        return result


def xls_tables(content: bytes) -> list[tuple[str, list[list[str]]]]:
    try:
        import xlrd
    except ImportError as error:
        raise ValueError("读取旧版 .xls 名录需要安装 xlrd==2.0.2") from error
    workbook = xlrd.open_workbook(file_contents=content, formatting_info=True)
    result = []
    for sheet in workbook.sheets():
        def value(row: int, col: int) -> str:
            cell = sheet.cell_value(row, col)
            return str(int(cell)) if isinstance(cell, float) and cell.is_integer() else str(cell)
        rows = [[value(r, c) for c in range(sheet.ncols)] for r in range(sheet.nrows)]
        for r0, r1, c0, c1 in sheet.merged_cells:
            for r in range(r0, r1):
                for c in range(c0, c1):
                    rows[r][c] = rows[r0][c0]
        result.append((sheet.name, rows))
    return result


def parse_records(source: dict, content: bytes) -> list[dict]:
    if source["file"].endswith(".json") or source.get("format") == "inline-json":
        if source.get("format") == "inline-json":
            match = re.search(r"var dataList\s*=\s*(\[.*?\])\s*(?=var arr1)", content.decode("utf-8-sig"), re.S)
            if not match:
                raise ValueError("重庆名录的公开数据数组格式发生变化")
            values = json.loads(re.sub(r",\s*]$", "]", match[1]))
        else:
            values = json.loads(content)["data"]
        headers = list(source["columns"].values())
        tables = [("data", [headers] + [[clean_text(str(record.get(key, ""))) for key in headers]
                                       for record in values])]
    elif source["file"].endswith(".xlsx"):
        tables = xlsx_tables(content)
    elif source["file"].endswith(".xls"):
        tables = xls_tables(content)
    else:
        if source["file"].endswith(".doc"):
            # macOS 的只读格式转换工具；不执行文档中的宏或脚本。
            with TemporaryDirectory(prefix="roam-doc-") as directory:
                path = Path(directory) / "source.doc"
                path.write_bytes(content)
                try:
                    content = subprocess.run(["textutil", "-convert", "html", "-stdout", str(path)],
                                             check=True, capture_output=True).stdout
                except (OSError, subprocess.CalledProcessError) as error:
                    raise ValueError("读取旧版 .doc 名录需要 macOS textutil 格式转换工具") from error
        parser = TableParser()
        parser.feed(content.decode("utf-8-sig"))
        tables = [(f"table-{i + 1}", expand_table(table)) for i, table in enumerate(parser.tables)]
    records = []
    for sheet, rows in tables:
        columns = {key: compact(value) for key, value in source.get("sheetColumns", {}).get(sheet, source["columns"]).items()}
        if source.get("gradeFromSheet"):
            grade_match = re.fullmatch(r"([1-5]A)景区", sheet)
            if not grade_match:
                raise ValueError(f"无法从工作表名称确定等级：{sheet}")
            rows = [row + ["等级" if columns["name"] in [compact(v) for v in row] else grade_match[1]] for row in rows]
        indexes = None
        for row_number, row in enumerate(rows, 1):
            values = [compact(str(value)) for value in row]
            if all(header in values for header in columns.values()):
                indexes = {key: values.index(header) for key, header in columns.items()}
                continue
            if indexes is None or not any(values):
                continue
            if max(indexes.values()) >= len(row):
                if values and values[0].isdigit():
                    raise ValueError(f"{sheet} 第 {row_number} 行缺少列")
                continue
            name, grade = values[indexes["name"]], values[indexes["grade"]]
            corrected = False
            # 湖南原件的这部分新增行把所在地与等级列写反；只在明确的 A 级值下纠正。
            if source.get("gradeMayPrecedeRegion") and not re.fullmatch(r"[1-5]A", grade):
                region_index = indexes["region"]
                if re.fullmatch(r"[1-5]A", values[region_index]):
                    row = list(row)
                    row[region_index], row[indexes["grade"]] = row[indexes["grade"]], row[region_index]
                    grade = values[region_index]
                    corrected = True
            if not re.fullmatch(r"(?:[1-5]A|A{1,5})(?:级)?", grade.upper()):
                if values[0].isdigit() or (grade and name and len(set(values) - {""}) > 1):
                    raise ValueError(f"{sheet} 第 {row_number} 行无法识别等级：{grade}")
                continue
            if not name:
                raise ValueError(f"{sheet} 第 {row_number} 行缺少名称")
            record = {key: clean_text(row[index]) for key, index in indexes.items()}
            if corrected:
                record["columnCorrection"] = "原件所在地与等级列互换"
            record.update(grade=grade_of(grade), sheet=sheet, row=row_number)
            records.append(record)
    if not records:
        raise ValueError(f"{source['id']} 未识别到官方景区表格")
    return records


def to_item(source: dict, record: dict) -> dict:
    name = compact(record["name"])
    city = compact(record.get("city", ""))
    if city in source.get("cityNames", {}):
        city = source["cityNames"][city]
    elif city and source.get("citySuffix") and not city.endswith(source["citySuffix"]):
        city += source["citySuffix"]
    district = compact(record.get("district", ""))
    # 名录中的开发区/景区管委会不一定是行政区，保留原字段而不臆造区县。
    if district in {"市辖区", "市本级", "市直"} or not district.endswith(("县", "区", "旗", "市")):
        district = ""
    local_region = compact(record.get("region", "")) or city + district
    region = source["province"] + local_region
    source_id = f"{source['id']}:{record['sheet']}:{record['row']}"
    identity = "|".join((source["province"], local_region, name))
    reason = ""
    if re.search(r"[·•—]|--|、", name):
        reason = "联合命名，需核实是否包含多个独立游览地"
    if "景区名称" in name:
        reason = "原名录名称疑似混入字段标题，需核实正式名称"
    return {
        "id": "prov-" + sha256(identity.encode()).hexdigest()[:20],
        "name": name, "detailName": name, "regionText": region,
        "provinceHints": [source["province"]], "address": record.get("address", ""),
        "classifications": [{"type": "a-level", "value": record["grade"]}],
        "description": "", "officialImageUrl": "", "geocodeQuery": region + name,
        "reviewStatus": "needs_review" if reason else "ready", "reviewReason": reason,
        "sources": [{"name": source["name"], "url": source["url"], "publishedAt": source["date"],
                     "sourceId": source_id}],
        "sourceRecordIds": [source_id], "rawFields": record,
    }


def collect(sources: list[dict], cache: Path, output: Path, refresh: bool) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    all_records, items, metadata = [], [], []
    for source in sources:
        path = cache / source["file"]
        url = source.get("download", source["url"])
        if refresh or not path.exists():
            parts = urlsplit(url)
            encoded = urlunsplit((parts.scheme, parts.netloc, quote(parts.path), parts.query, parts.fragment))
            request = Request(encoded, headers={"User-Agent": "roam-public-tourism-catalog/1.0"})
            if source.get("format") == "paged-guangxi":
                fields = [("f_2023818369421_ext", ""), ("f_2023817820739", ""), ("f_2023818268086", ""),
                          ("f_2023818910485_ext", ""), ("channel_id", 189548), ("site_id", 205)]
                query = {"code": "188464f3632", "tableName": "t_189fd24bd1a", "pageSize": 15, "pageNo": 1,
                         "searchFields": [{"fieldName": key, "searchWord": value} for key, value in fields],
                         "sorts": [{"sortField": "f_2025821647609", "sortOrder": "DESC"}],
                         "customFilter": {"operator": "and", "properties": []}, "beginDateTime": "", "endDateTime": ""}
                query["searchFields"][0]["keyWordSearch"] = 1
                pages, values, total = [], [], None
                while total is None or len(values) < total:
                    page_request = Request(encoded, data=json.dumps(query).encode(),
                                           headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0", "Referer": source["url"]})
                    with urlopen(page_request, timeout=30) as response:
                        page = json.load(response)["data"]
                    if not page["pager"] or int(page["pager"]["pageNo"]) != query["pageNo"] or not page["list"]:
                        raise ValueError("广西名录分页返回缺失")
                    if total is not None and int(page["pager"]["total"]) != total:
                        raise ValueError("广西名录采集期间总数变化，请重试")
                    total = int(page["pager"]["total"])
                    pages.append(page)
                    values.extend(page["list"])
                    query["pageNo"] += 1
                    time.sleep(0.5)
                if len(values) != total or len({row["rec_id"] for row in values}) != total:
                    raise ValueError("广西名录分页存在重复或数量不符")
                content = json.dumps({"data": values, "pages": pages}, ensure_ascii=False).encode()
            elif source.get("format") == "paged-tianjin":
                pages, values, total = [], [], None
                page_number = 1
                while total is None or len(values) < total:
                    page_request = Request(encoded + f"&pageNumber={page_number}", headers=request.headers)
                    with urlopen(page_request, timeout=30) as response:
                        page = json.load(response)["page"]
                    if int(page["pageNumber"]) != page_number or not page["content"]:
                        raise ValueError("天津名录分页返回缺失")
                    if total is not None and int(page["total"]) != total:
                        raise ValueError("天津名录采集期间总数变化，请重试")
                    total = int(page["total"])
                    pages.append(page)
                    values.extend(page["content"])
                    page_number += 1
                if len(values) != total or len({row["DOCID"] for row in values}) != total:
                    raise ValueError("天津名录分页存在重复或数量不符")
                content = json.dumps({"data": values, "pages": pages}, ensure_ascii=False).encode()
            else:
                try:
                    with urlopen(request, timeout=30) as response:
                        content = response.read()
                except OSError:
                    # 部分官网与 Python 的 TLS 握手不兼容；系统 curl 仍启用证书验证。
                    try:
                        content = subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location",
                                                  "--max-time", "30", encoded], check=True, capture_output=True).stdout
                    except (OSError, subprocess.CalledProcessError) as error:
                        raise ValueError(f"无法下载 {source['id']} 官方名录") from error
            # 先成功解析，再替换缓存，避免错误页面污染断点。
            parse_records(source, content)
            path.write_bytes(content)
        content = path.read_bytes()
        records = parse_records(source, content)
        converted = [to_item(source, record) for record in records]
        all_records.extend(converted)
        # 全国 5A 沿用原有拆分及核实结果；省级名录只增补 1A–4A。
        additions = [item for item in converted if item["classifications"][0]["value"] != "5A"]
        items.extend(additions)
        metadata.append({**source, "download": url, "sha256": sha256(content).hexdigest(),
                         "rawFile": str(path), "officialRows": len(records), "includedRows": len(additions),
                         "levels": dict(Counter(record["grade"] for record in records))})
        print(f"{source['province']}：官方名录 {len(records)} 条，增补候选 {len(additions)} 条", flush=True)
    if len({item["id"] for item in items}) != len(items):
        counts = Counter(item["id"] for item in items)
        names = [item["name"] for item in items if counts[item["id"]] > 1]
        raise ValueError("名录有相同地区同名记录，需核实：" + "、".join(names))
    summary = {"total": len(items), "ready": sum(item["reviewStatus"] == "ready" for item in items),
               "needsReview": sum(item["reviewStatus"] == "needs_review" for item in items),
               "levels": dict(Counter(item["classifications"][0]["value"] for item in items))}
    atomic_write_json(output, {"schemaVersion": 1, "stage": "attraction-source", "generatedAt": iso_timestamp(),
                               "coverageNote": "只代表已接入名录及各来源日期的 1A–4A 景区，不代表全国全量或实时等级。",
                               "summary": summary, "sources": metadata, "sourceRecords": all_records, "items": items})
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    print(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", choices=[source["id"] for source in SOURCES])
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    try:
        sources = [source for source in SOURCES if not args.source or source["id"] in args.source]
        collect(sources, args.cache, args.output, args.refresh)
        return 0
    except (ValueError, OSError) as error:
        print(f"省级资料未生成：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
