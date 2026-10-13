#!/usr/bin/env python3
"""采集已接入地方文旅局的官方 4A、3A 景区名录，不调用高德。"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
import unicodedata
from urllib.request import Request, urlopen

from fetch_mct import atomic_write_json, clean_text, iso_timestamp

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "tmp/01-地方文旅景点数据.json"
ARCHIVE = ROOT / "tmp/01-地方文旅原始页面.json"

# 仅接入已核实的官方完整名录，新增地区时在此增加来源，不另设配置文件。
SOURCES = [
    {"id": "shanghai-4a", "name": "上海市文化和旅游局", "province": "上海市", "city": "上海市",
     "url": "https://whlyj.sh.gov.cn/4ajjq/20191008/0022-28627.html", "grade": "4A"},
    {"id": "shanghai-3a", "name": "上海市文化和旅游局", "province": "上海市", "city": "上海市",
     "url": "https://whlyj.sh.gov.cn/3ajjq/20191008/0022-29707.html", "grade": "3A"},
    {"id": "weihai", "name": "威海市文化和旅游局", "province": "山东省", "city": "威海市",
     "url": "https://wlj.weihai.gov.cn/art/2026/5/8/art_162556_4824985.html"},
    {"id": "daye", "name": "大冶市文化和旅游局", "province": "湖北省", "city": "黄石市", "district": "大冶市",
     "url": "https://www.hbdaye.gov.cn/zjdy/dyly/lyxx/202605/t20260527_1331093.html"},
]
SHANGHAI_DISTRICTS = "黄浦区 徐汇区 长宁区 静安区 普陀区 虹口区 杨浦区 闵行区 宝山区 嘉定区 浦东新区 金山区 松江区 青浦区 奉贤区 崇明区".split()
WEIHAI_DISTRICTS = {"环翠": "环翠区", "文登": "文登区", "荣成": "荣成市", "乳山": "乳山市"}

# 仅整理已由官方资料核实的独立地点，原名录仍完整保存在 sourceRecords。
VERIFIED_PLACES = {
    "上海市青少年校外活动营地-东方绿舟": {
        "names": ["东方绿舟"], "reason": "营地与东方绿舟为同一园区，不拆分。",
        "evidence": ["https://www.shqp.gov.cn/shqp/tpxw/20220802/955916.html"],
    },
    "上海世茂精灵之城主题乐园—深坑秘境": {
        "names": ["深坑秘境"], "reason": "官方 3A 名录明确指深坑秘境，不扩大为含蓝精灵乐园的整个项目。",
        "evidence": ["https://whlyj.sh.gov.cn/3ajjq/20201230/623f7015d3a142dab767bb96b61a8a1e.html"],
    },
    "陈云故里·练塘古镇景区": {
        "names": ["陈云纪念馆", "练塘古镇"], "reason": "官方游览资料分别列出纪念馆和古镇，按独立地点整理。",
        "addresses": ["青浦区练塘镇朱枫公路3516号", "青浦区练塘镇朱枫公路3501号"],
        "evidence": ["https://whlyj.sh.gov.cn/gqfc/20210225/dd7783c7dd6a45db96abc1a3cbb84207.html"],
    },
    "华亭人家·毛桥村": {
        "names": ["华亭人家", "毛桥村"], "reason": "官方介绍明确由华亭人家（乡悦华亭）与毛桥村两个地点组成。",
        "addresses": ["", ""],
        "evidence": ["https://www.meet-in-shanghai.net/cn/tourist-attraction/huating-peoples-maoqiao-village-058470/"],
    },
}



# 名称与代表点仅使用已核实的官方依据；不改变原始名录。
VERIFIED_ALIASES = {
    "威海荣成赤山风景名胜区": {
        "aliases": [
            "石岛赤山风景名胜区",
            "石岛赤山风景区"
        ],
        "reason": "荣成市政府旅游景点介绍使用石岛赤山风景名胜区，地点位于荣成市石岛。",
        "url": "https://www.rongcheng.gov.cn/art/2011/12/1/art_40561_1110334.html"
    },
    "乳山银滩旅游度假区": {
        "aliases": [
            "银滩滨海旅游度假区"
        ],
        "reason": "威海市政府公开信息使用乳山市银滩滨海旅游度假区，地点与官方名录的乳山长江路一致。",
        "url": "https://www.weihai.gov.cn/art/2025/7/23/art_58820_5675246.html"
    },
    "威海荣成市那香海景区": {
        "aliases": [
            "那香海国际旅游度假区"
        ],
        "reason": "景区官网给出的中文名为那香海国际旅游度假区，地址为荣成市环海大道 6699 号。",
        "url": "https://www.naxianghai.com/content/about.aspx"
    },
    "上海海上花岛前卫村景区": {
        "aliases": [
            "前卫生态村"
        ],
        "reason": "崇明区官方名录及景区介绍将海上花岛前卫村景区定位于前卫生态村，高德使用前卫生态村旧称。",
        "url": "https://www.shcm.gov.cn/bmpd/019008/019008002/20251212/eea64452-67d6-42ce-9b68-465e2ad4d68e.html"
    },
    "上海炮台湾景区": {
        "aliases": [
            "上海吴淞炮台湾国家湿地公园"
        ],
        "reason": "上海文旅局介绍明确吴淞炮台湾国家湿地公园为该 4A 景区，补全官方名称。",
        "url": "https://whlyj.sh.gov.cn/gqfc/20230816/1a3d8136e631438abe75ddc06b32c45a.html"
    },
    "上海蓝精灵乐园": {
        "aliases": [
            "上海世茂精灵之城主题乐园-蓝精灵乐园"
        ],
        "reason": "官方旅游推广资料确认蓝精灵乐园属于世茂精灵之城主题乐园，地址为辰花路 5088 弄，与高德该独立园区一致。",
        "url": "https://www.meet-in-shanghai.net/cn/guide/spend-the-summer-in-the-cool-and-walk-the-baby-with-the-air-conditioner-in-the-indoor-park-138678/"
    },
    "上海云堡未来市艺术文创景区": {
        "aliases": [
            "云堡未来市艺术城",
            "上海云堡未来市"
        ],
        "reason": "景区官网确认云堡未来市艺术城为国家 4A 景区，地址为王家厍路 885 弄。",
        "url": "https://www.rabornova.com/"
    },
    "上海市金山区漕泾水窠景区": {
        "aliases": [
            "上海漕泾郊野公园水窠景区"
        ],
        "reason": "官方旅游资料确认水窠景区位于漕泾郊野公园，朱漕公路 3515 号与官方名录及高德景区条目一致。",
        "url": "https://www.shanghai.gov.cn/nw17239/20260407/848cc8a2f1b94d3d89cbc0d5ebd8ad28.html"
    },
    "上海仙桥生态村景区": {
        "aliases": [
            "仙桥村",
            "竖新镇仙桥村"
        ],
        "reason": "崇明区官方乡村旅游重点村介绍及景区名录确认景区为竖新镇仙桥村，补充村庄名称用于定位。",
        "url": "https://www.shcm.gov.cn/xwzx/002008/20230412/b2cf4604-3d2b-49d8-8ea6-b911b5eb01c0.html"
    }
}
VERIFIED_REPRESENTATIVES = {
    "上海廊下郊野公园景区": {
        "kind": "venue",
        "name": "廊下生态园",
        "label": "游览代表点：廊下生态园",
        "reason": "官方文旅资料将廊下生态园列为廊下郊野公园游览点，且漕廊公路 9133 号与官方景区地址一致；以该具名游览点代表公园，不当作整个公园边界。",
        "url": "https://whlyj.sh.gov.cn/gqfc/20210402/e4d782bc44ab4a1bbd7021b4237f4b38.html"
    },
    "毛桥村": {
        "kind": "village",
        "name": "华亭镇毛桥村",
        "label": "村庄代表点",
        "reason": "上海市农业农村委员会公开资料确认完整名称为嘉定区华亭镇毛桥村，补全乡镇前缀。",
        "url": "https://nyncw.sh.gov.cn/2020xczxsfc/20230320/31023513b32f468bb792ccfb74a8c3e7.html"
    },
    "上海玉海棠景区": {
        "kind": "venue",
        "name": "上海玉海棠",
        "label": "园区代表点",
        "reason": "官方名录地址为北星公路 1999 号，以同址的上海玉海棠园区条目代表景区，不采用名称明确限定为新安古街的内部游览点。",
        "url": "https://lycx.whlyj.sh.gov.cn/c/scenicArea.view.ac?id=404b846414a2497f90a5d68c5b803a2c"
    }
}


# 官方 A 级景区本身就是商业或文创场所时，核实整体名称和地址；不写死坐标。
# 名录页面是共同依据，url 仅补充更完整名称或业态说明。
VERIFIED_VENUES = {
    "上海国际时尚中心": {
        "names": ["上海国际时尚中心"], "addresses": ["杨树浦路2866号"],
    },
    "比斯特上海购物村景区": {
        "names": ["比斯特上海购物村"], "addresses": ["申迪东路88号"],
        "url": "https://sww.sh.gov.cn/swdt/20230504/d8d14b93051645e09d076fce8b04e5a6.html",
    },
    "上海云堡未来市艺术文创景区": {
        "names": ["云堡未来市艺术城"], "addresses": ["王家厍路885弄", "洞泾镇王家厍路885弄"],
        "url": "https://www.rabornova.com/",
    },
    "上海田子坊景区": {
        "names": ["上海田子坊"], "addresses": ["泰康路210号"],
    },
    "上海佛罗伦萨小镇景区": {
        "names": ["上海佛罗伦萨小镇(上海名品奥特莱斯)"], "addresses": ["卓耀路58弄1-365号"],
        "url": "https://www.florentiavillage.com/shanghai",
    },
    "上海半岛1919文创园": {
        "names": ["半岛1919文化创意产业园"], "addresses": ["淞兴西路258号"],
        "url": "https://xxgk.shbsq.gov.cn/article.html?infoid=0b5f0481-b022-49da-96d3-3ee9228085c7",
    },
    "上海云间粮仓文创园": {
        "names": ["上海云间粮仓文创园"], "addresses": ["松汇东路327号", "松金公路10053号"],
    },
    "上海香朵开心农场景区": {
        "names": ["上海香朵开心农场"], "addresses": ["小星公路1500号"],
        "url": "https://whlyj.sh.gov.cn/wlyw/20230105/92e96e63379944cf97f564144fe79b47.html",
    },
}


# 用户于 2026-09-24 确认忽略这 24 个未定位景点；重跑仍保留原始资料，但不补查、不上图。
USER_SKIPPED_IDS = {
    "local-b3632136a9cc43daa8c6",  # 上海仙桥生态村景区
    "local-ec65169f0f6f920c052d",  # 上海长泰广场
    "local-f702c4e60f60b10c9bf4",  # 上海东禾九谷开心农场旅游区
    "local-03a42126c4feefe6291e",  # 上海北外滩国航中心景区
    "local-6586ae09c6db820689ae",  # 上海工业初心文化景区
    "local-616fb4432916bea22a99",  # 上海广富林奇石馆
    "local-9ccbe54f4c623a307eb8",  # 上海市黄浦区复兴公园INS乐园
    "local-93b260b9e871aab922cc",  # 上海市浦东新区海沈村旅游景区
    "local-2a070bd6d646d99db294",  # 上海市崇明区自然回响
    "local-9d0011bf089c41d953ac",  # 环球观光台景区
    "local-90af9c1da69640acbeb1",  # 连民村旅游景区
    "local-1e433fe3192a0219bf6a",  # 龙之梦IP潮玩乐园
    "local-5281736cfba520d17afd",  # 威海市毛泽东像章博物馆
    "local-446dbace16401ad0092a",  # 威海乳山小汤温泉度假村景区
    "local-4ecae2f1647efe7c0c6c",  # 威海乳山市台依湖国际酒庄生态文化区
    "local-504a2be178035025cca2",  # 海螺湾休闲驿区
    "local-27d62af78ffcde969915",  # 威海市海丝文化博物馆
    "local-65c404fe69119e9c7096",  # 文登天福山红色旅游景区
    "local-caaa142764f7657faeb3",  # 威海市经济技术开发区东浦湾景区
    "local-0b31f66eb02c0cfdd8a3",  # 威海东南乡景区
    "local-ea11a7964c67d7c5ac76",  # 明海归谷休闲旅游风景区
    "local-aa653b6ce8d638bd619a",  # 鄂王城生态文化园
    "local-bf7851442199ec7ceb97",  # 劲牌工业旅游景区
    "local-0baed96e60a5f1f7e568",  # 三华生态园
}


def compact(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


class TableParser(HTMLParser):
    """读取 HTML 表格与发布日期，不依赖网站样式或第三方解析库。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[dict]]] = []
        self.stack: list[dict] = []
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "meta":
            self.meta[str(attributes.get("name", "")).lower()] = attributes.get("content") or ""
        if tag == "table":
            self.stack.append({"rows": [], "row": None, "cell": None})
        if not self.stack:
            return
        table = self.stack[-1]
        if tag == "tr":
            table["row"] = []
        elif tag in {"td", "th"} and table["row"] is not None:
            table["cell"] = {"parts": [], "rowspan": int(attributes.get("rowspan") or 1),
                             "colspan": int(attributes.get("colspan") or 1)}
        elif tag == "br" and table["cell"] is not None:
            table["cell"]["parts"].append(" ")

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"]["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            return
        table = self.stack[-1]
        if tag in {"td", "th"} and table["cell"] is not None:
            cell = table["cell"]
            cell["text"] = clean_text("".join(cell.pop("parts")))
            table["row"].append(cell)
            table["cell"] = None
        elif tag == "tr" and table["row"] is not None:
            table["rows"].append(table["row"])
            table["row"] = None
        elif tag == "table":
            self.tables.append(self.stack.pop()["rows"])


def expand_table(rows: list[list[dict]]) -> list[list[str]]:
    grid: dict[tuple[int, int], str] = {}
    result = []
    for y, row in enumerate(rows):
        x = 0
        for cell in row:
            while (y, x) in grid:
                x += 1
            for dy in range(cell["rowspan"]):
                for dx in range(cell["colspan"]):
                    grid[y + dy, x + dx] = cell["text"]
            x += cell["colspan"]
        width = max((column + 1 for line, column in grid if line == y), default=0)
        result.append([grid.get((y, column), "") for column in range(width)])
    return result


def parse_page(body: str) -> tuple[list[dict[str, str]], dict[str, str]]:
    parser = TableParser()
    parser.feed(body)
    candidates: dict[str, list[dict[str, str]]] = {}
    for table in parser.tables:
        headers: list[str] | None = None
        records: list[dict[str, str]] = []
        numbers: list[int] = []
        for row in expand_table(table):
            values = [compact(value) for value in row]
            if "序号" in values and any(value in {"单位名称", "景区名称"} for value in values):
                headers = values
                continue
            if headers is None or not any(values):
                continue
            index = headers.index("序号")
            if index >= len(values) or not values[index].isdigit():
                raise ValueError("景区表格包含无法识别的非序号行，停止发布结果")
            if len(row) != len(headers):
                raise ValueError("景区表格列数发生变化，停止发布结果")
            numbers.append(int(values[index]))
            records.append(dict(zip(headers, row)))
        if records:
            if numbers != list(range(1, len(records) + 1)):
                raise ValueError("景区表格序号不连续，可能漏行，停止发布结果")
            candidates[json.dumps(records, ensure_ascii=False, sort_keys=True)] = records
    if len(candidates) != 1:
        raise ValueError(f"应有一份明确的景区名录表格，实际识别 {len(candidates)} 份")
    return next(iter(candidates.values())), parser.meta


def grade_of(value: str) -> str:
    text = compact(value).upper().replace("级", "")
    if re.fullmatch(r"A{1,5}", text):
        return f"{len(text)}A"
    if re.fullmatch(r"[1-5]A", text):
        return text
    raise ValueError(f"无法识别官方等级：{value}")


def to_record(source: dict, row: dict[str, str], published: str) -> dict:
    name = clean_text(row.get("单位名称") or row.get("景区名称") or "")
    if not name:
        raise ValueError("官方名录缺少景区名称")
    grade = grade_of(row.get("等级") or row.get("A级") or source.get("grade", ""))
    if source.get("grade") and grade != source["grade"]:
        raise ValueError(f"{name} 的等级与名录栏目不符")
    address = clean_text(row.get("景区地址") or row.get("地址") or "")
    district = source.get("district", "")
    if source["province"] == "上海市":
        district = next((part for part in SHANGHAI_DISTRICTS if part in address), "")
    elif source["city"] == "威海市":
        district = WEIHAI_DISTRICTS.get(compact(row.get("区市", "")), "")
        district = district or next((part for part in WEIHAI_DISTRICTS.values() if part in address), "")
    region = source["province"] + (source["city"] if source["city"] != source["province"] else "") + district
    identity = "|".join((source["province"], source["city"], compact(name)))
    item_id = "local-" + sha256(identity.encode()).hexdigest()[:20]
    year_text = row.get("评定时间", "")
    year = re.search(r"(?:19|20)\d{2}", year_text)
    # 联合命名先保留官方记录；是否拆成独立游览点必须核实，不能直接用一个坐标。
    reason = "联合命名，需核实是否包含多个独立游览地" if re.search(r"[·•—]|-", name) else ""
    return {
        "id": item_id, "name": name, "detailName": name,
        "classifications": [{"type": "a-level", "value": grade,
                             "year": year[0] if year else "", "yearText": year_text}],
        "regionText": region, "provinceHints": [source["province"]], "address": address,
        "description": "", "officialImageUrl": "", "geocodeQuery": region + name,
        "reviewStatus": "needs_review" if reason else "ready", "reviewReason": reason,
        "sources": [{"name": source["name"], "url": source["url"],
                     "sourceId": source["id"] + ":" + compact(row["序号"]), "publishedAt": published}],
        "sourceRecordIds": [source["id"] + ":" + compact(row["序号"])],
        "rawFields": row,
    }


def prepare_items(records: list[dict]) -> list[dict]:
    items = []
    for record in records:
        if record["classifications"][0]["value"] not in {"4A", "3A"}:
            continue
        rule = VERIFIED_PLACES.get(record["name"])
        if rule:
            for index, name in enumerate(rule["names"]):
                item = deepcopy(record)
                item.update(name=name, detailName=name, geocodeQuery=item["regionText"] + name,
                            reviewStatus="ready", reviewReason="", selectionReason=rule["reason"],
                            selectionEvidence=rule["evidence"])
                if "addresses" in rule:
                    item["address"] = rule["addresses"][index]
                if len(rule["names"]) > 1:
                    item["id"] += "-" + sha256(name.encode()).hexdigest()[:10]
                    for classification in item["classifications"]:
                        classification.update(scope="component", groupName=record["name"])
                else:
                    item["aliases"] = [record["name"]]
                    item["nameEvidence"] = [{"aliases": [name, record["name"]],
                                             "regionText": item["regionText"], "url": url}
                                            for url in rule["evidence"]]
                if name == "毛桥村":
                    item["representativePoint"] = {"kind": "village", "name": "毛桥村", "label": "村庄代表点",
                                                   "evidence": rule["evidence"]}
                items.append(item)
            continue
        item = deepcopy(record)
        if item["name"] == "上海长风公园·长风海洋世界景区":
            item.update(reviewStatus="ready", reviewReason="",
                        selectionReason="海洋世界位于长风公园内，按同一园区保留一条，以长风公园作为代表点。")
            item["representativePoint"] = {
                "kind": "venue", "name": "长风公园", "label": "所属园区",
                "evidence": ["https://www.shpt.gov.cn/cfjd-jiedaozhen/cfjg-sqzc-cfjd/20171110/277404.html"],
            }
            item["geocodeQuery"] = item["regionText"] + "长风公园"
        elif item["name"] == "威海海滨生态公园风景带•侨乡号游轮":
            evidence = ["https://www.weihai.gov.cn/szdt/web/mapIndex/3?area=&mapClassificationId=&mapClassificationPid=26&name="]
            item.update(reviewStatus="ready", reviewReason="", regionText="山东省威海市环翠区",
                        address="威海市环翠区灯塔路8号",
                        geocodeQuery="山东省威海市环翠区侨乡号游轮",
                        selectionReason="用户确认使用游轮坐标；采用官方登船地址灯塔路 8 号处的侨乡号游轮地点，不表示整条风景带的范围。",
                        selectionEvidence=evidence)
            item["representativePoint"] = {
                "kind": "landmark", "name": "侨乡号游轮", "label": "游轮停靠点",
                "evidence": evidence,
            }
        items.append(item)
    for item in items:
        alias_rule = VERIFIED_ALIASES.get(item["name"])
        if alias_rule:
            item["aliases"] = list(dict.fromkeys([*item.get("aliases", []), *alias_rule["aliases"]]))
            item.setdefault("nameEvidence", []).append({"aliases": alias_rule["aliases"],
                                                       "regionText": item["regionText"], "url": alias_rule["url"]})
            item.setdefault("selectionEvidence", []).append(alias_rule["url"])
            item["selectionReason"] = alias_rule["reason"]
        point_rule = VERIFIED_REPRESENTATIVES.get(item["name"])
        if point_rule:
            item["representativePoint"] = {key: point_rule[key] for key in ("kind", "name", "label")}
            item["representativePoint"]["evidence"] = [point_rule["url"]]
            item["geocodeQuery"] = item["regionText"] + point_rule["name"]
            item["selectionReason"] = point_rule["reason"]
            item.setdefault("selectionEvidence", []).append(point_rule["url"])
        venue_rule = VERIFIED_VENUES.get(item["name"])
        if venue_rule:
            evidence = [source["url"] for source in item["sources"]]
            if venue_rule.get("url"):
                evidence.append(venue_rule["url"])
            item["verifiedVenue"] = {
                "names": venue_rule["names"], "addresses": venue_rule["addresses"],
                "evidence": list(dict.fromkeys(evidence)),
            }
            item["selectionReason"] = "官方 A 级景区名称、地区与地址已核实，采用整体场所地点，不用内部店铺、酒店或停车场代替。"
            item["selectionEvidence"] = list(dict.fromkeys([*item.get("selectionEvidence", []), *evidence]))
        if item["id"] in USER_SKIPPED_IDS:
            item.update(reviewStatus="skip", reviewReason="",
                        skipReason="2026-09-24 用户确认忽略：坐标尚未核实，不补查、不发布到地图；保留基础资料与已有请求记录。")
    return items


def get_page(source: dict, archive: dict, path: Path, refresh: bool) -> dict:
    previous = next((record for record in reversed(archive["requests"])
                     if record["url"] == source["url"] and record.get("status") == "success"), None)
    if previous and not refresh:
        print(f"复用原始页面：{source['id']}", flush=True)
        return previous
    for attempt in range(2):
        time.sleep(1)
        record = {"url": source["url"], "requestedAt": iso_timestamp(), "attempt": attempt + 1}
        try:
            request = Request(source["url"], headers={"User-Agent": "roam-public-tourism-catalog/1.0"})
            with urlopen(request, timeout=25) as response:
                content = response.read()
                charset = response.headers.get_content_charset() or "utf-8-sig"
                record.update({"httpStatus": response.status, "headers": list(response.headers.items()),
                               "finalUrl": response.url, "body": content.decode(charset),
                               "sha256": sha256(content).hexdigest(), "status": "success"})
        except Exception as error:
            record.update({"status": "failed", "error": str(error)})
        archive["requests"].append(record)
        atomic_write_json(path, archive)
        if record["status"] == "success":
            return record
        print(f"请求失败：{source['id']}：{record['error']}", file=sys.stderr, flush=True)
    raise ValueError(f"{source['id']} 请求失败；已保存断点，重跑可继续")


def collect(output: Path, archive_path: Path, refresh: bool) -> int:
    if output.resolve() == archive_path.resolve():
        raise ValueError("结果和原始页面不能使用同一文件")
    archive = json.loads(archive_path.read_text(encoding="utf-8")) if archive_path.exists() else {
        "schemaVersion": 1, "stage": "local-tourism-pages", "requests": [],
    }
    if archive.get("stage") != "local-tourism-pages" or not isinstance(archive.get("requests"), list):
        raise ValueError("原始页面存档格式不符，停止以免覆盖其他数据")
    records, sources, errors = [], [], []
    for source in SOURCES:
        try:
            page = get_page(source, archive, archive_path, refresh)
            rows, meta = parse_page(page["body"])
            published = (meta.get("pubdate") or meta.get("firstpublishedtime") or "")[:10]
            published = re.sub(r"[—–/.]", "-", published)
            page_records = [to_record(source, row, published) for row in rows]
            included = [record for record in page_records if record["classifications"][0]["value"] in {"4A", "3A"}]
            records.extend(page_records)
            sources.append({**source, "title": meta.get("articletitle", ""), "publishedAt": published,
                            "fetchedAt": page["requestedAt"], "pageSha256": page["sha256"],
                            "officialRows": len(rows), "includedRows": len(included)})
            print(f"{source['id']}：官方 {len(rows)} 条，本轮 4A/3A {len(included)} 条", flush=True)
        except Exception as error:
            errors.append(f"{source['id']}：{error}")
    if errors:
        raise ValueError("\n".join(errors) + "\n未更新景点结果文件，已有结果保留。")
    items = prepare_items(records)
    if len({record["id"] for record in items}) != len(items):
        raise ValueError("地方名录存在同名或跨等级重复记录，需核实后再发布")
    summary = {
        "officialIncluded": sum(source["includedRows"] for source in sources),
        "total": len(items), "ready": sum(item["reviewStatus"] == "ready" for item in items),
        "needsReview": sum(item["reviewStatus"] == "needs_review" for item in items),
        "skipped": sum(item["reviewStatus"] == "skip" for item in items),
        "levels": dict(Counter(item["classifications"][0]["value"] for item in items)),
    }
    atomic_write_json(output, {
        "schemaVersion": 1, "stage": "attraction-source", "generatedAt": iso_timestamp(),
        "coverageNote": "仅含已接入的上海市、山东省威海市、湖北省大冶市官方名录，不代表全国 4A/3A 全量；等级以各来源发布日期为准。",
        "summary": summary, "sources": sources, "rawArchive": str(archive_path),
        "sourceRecords": records, "items": items,
    })
    print(f"已生成：{output}\n共 {summary['total']} 条，需核实 {summary['needsReview']} 条，跳过 {summary['skipped']} 条。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT, help="地方景点资料 JSON")
    parser.add_argument("--archive", type=Path, default=ARCHIVE, help="原始页面及断点 JSON")
    parser.add_argument("--refresh", action="store_true", help="重新读取官网，旧请求仍保留")
    args = parser.parse_args()
    try:
        return collect(args.output, args.archive, args.refresh)
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
