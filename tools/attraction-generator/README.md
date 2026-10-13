# 景点数据脚本

不同网站使用不同的采集脚本，输出统一的景点格式，再汇总、补坐标、生成地图数据。扩充地方景点使用下面的「全国增补」流程；文旅部及早期地方流程保留，用于维护原有已确认资料。

以下命令在项目根目录执行。输出中的 `tmp/` 位于 `tools/attraction-generator/` 下。

## 全国增补

`fetch_provincial_tourism.py` 接入 22 个省区市的官方网页、表格和公开分页目录；`fetch_pdf_tourism.py` 另接入山西、内蒙古。全国 5A 沿用原来已核实的组成景点，省级名录增补 1A–4A。来源日期、原件及哈希均保留，收录范围见 [覆盖报告](../../docs/attraction-data-coverage.md)。

旧版 Excel 需要 `xlrd==2.0.2`，PDF 需要 `pdfplumber`；可安装到独立 Python 虚拟环境。湖南旧版 Word 使用 macOS 自带 `textutil` 读取；其他来源不依赖它。普通 XLSX 使用标准库读取，无需 Excel。

### 1. 获取名录并合并

```bash
python3 tools/attraction-generator/fetch_provincial_tourism.py
python3 tools/attraction-generator/fetch_pdf_tourism.py
python3 tools/attraction-generator/merge_sources.py \
  --input 'tools/attraction-generator/tmp/01-汇总景点数据（待确认）.json' \
  'tools/attraction-generator/tmp/01-省级文旅增补数据.json' \
  'tools/attraction-generator/tmp/01-PDF文旅增补数据.json' \
  --output tools/attraction-generator/tmp/expansion/merged-next.json
```

保留原来的汇总文件，其中包含已核实名称、代表点和明确跳过的记录。增补结果写入独立文件，不覆盖这些确认结果。省级采集默认复用原件；`--refresh` 重新下载，`--source zhejiang` 等参数可指定来源。解析失败、分页重复或数量不符时停止，不发布残缺名录。

### 2. 分轮补充坐标

```bash
python3 tools/attraction-generator/geocode_catalog.py \
  --input tools/attraction-generator/tmp/expansion/merged-next.json \
  --archive-dir tools/attraction-generator/tmp/expansion/amap-shards \
  --reuse tools/attraction-generator/tmp/02-高德原始数据.json \
  --reuse tools/attraction-generator/tmp/expansion/sample-amap.json \
  --reuse tools/attraction-generator/tmp/expansion/reused-pois.json \
  --passes 1
```

需要环境变量 `AMAP_WEB_SERVICE_KEY`。不存在的可选 `--reuse` 文件应从命令中去掉。每轮对尚需查询的景点执行一个查询，按省份轮转、在省内优先高等级景点；完成一轮后仍有缺口可再次运行。保持每秒最多 2 次请求，禁止并发采集。日额度耗尽会立即停止；额度恢复后重跑同一命令，从已保存的断点继续，不把失败请求视为有效坐标。

完整请求与响应按 80 次一片保存在 `amap-shards/amap-*.json`，密钥已脱敏；`progress.json` 是匹配进度。脚本返回 1 表示仍有未完成/待核实记录，返回 2 表示输入或运行错误。采集缓存位于 Git 忽略的 `tmp/`，需保留以便续跑。

### 3. 复用本地 POI 并增量发布

```bash
python3 tools/attraction-generator/reuse_catalog_pois.py \
  --source tools/attraction-generator/tmp/expansion/merged-next.json \
  --archive tools/attraction-generator/tmp/02-高德原始数据.json \
  --archive tools/attraction-generator/tmp/expansion/sample-amap.json \
  --archive-dir tools/attraction-generator/tmp/expansion/amap-shards \
  --output tools/attraction-generator/tmp/expansion/reused-pois.json

python3 tools/attraction-generator/publish_catalog.py \
  --source tools/attraction-generator/tmp/expansion/merged-next.json \
  --archive tools/attraction-generator/tmp/02-高德原始数据.json \
  --archive tools/attraction-generator/tmp/expansion/sample-amap.json \
  --archive tools/attraction-generator/tmp/expansion/reused-pois.json \
  --archive-dir tools/attraction-generator/tmp/expansion/amap-shards \
  --report tools/attraction-generator/tmp/expansion/publication.json
```

这两步不调用外部接口。缓存复用只补充名称、地区和地点类型相符的关联，完整响应不变；发布时仍核对分页完整性和候选唯一性。只允许有明确 POI ID 的新景点上图，排除地址中心点、重复 POI 和未确认景点；保留所有现有点位。`publication.json` 列出新增/更新 ID，以及未解决项的来源和原因。它的 before/after 是本次发布数，累计增量见覆盖报告。

原有 `build_map_data.py` 是全量严格发布：有任一未解决记录就不覆盖地图。扩充过程中使用 `publish_catalog.py`，不要用仅包含原来两份来源的旧汇总重建扩展后的地图。

### 4. 使用 OSM 离线坐标补缺

在项目根目录运行。此流程不消耗高德额度，也不调用 Nominatim 公共查询服务。下载约 1.5 GB 的中国快照，或将 URL 和文件改成 [Geofabrik 的省级文件](https://download.geofabrik.de/asia/china.html)。离线解析需要额外 Python 依赖：

```bash
python3 -m venv tools/attraction-generator/tmp/geo-venv
source tools/attraction-generator/tmp/geo-venv/bin/activate
python -m pip install -r tools/attraction-generator/requirements-geospatial.txt
curl --fail --location --output tools/attraction-generator/tmp/expansion/china-latest.osm.pbf.part \
  https://download.geofabrik.de/asia/china-latest.osm.pbf
mv tools/attraction-generator/tmp/expansion/china-latest.osm.pbf.part \
  tools/attraction-generator/tmp/expansion/china-latest.osm.pbf

python tools/attraction-generator/offline_coordinates.py extract \
  --pbf tools/attraction-generator/tmp/expansion/china-latest.osm.pbf \
  --source-url https://download.geofabrik.de/asia/china-latest.osm.pbf \
  --source tools/attraction-generator/tmp/expansion/merged-next.json \
  --output tools/attraction-generator/tmp/expansion/osm-china-candidates.json

python tools/attraction-generator/offline_coordinates.py match \
  --source tools/attraction-generator/tmp/expansion/merged-next.json \
  --candidates tools/attraction-generator/tmp/expansion/osm-china-candidates.json \
  --output tools/attraction-generator/tmp/expansion/osm-china-bindings.json

python tools/attraction-generator/publish_coordinates.py \
  --source tools/attraction-generator/tmp/expansion/merged-next.json \
  --archive tools/attraction-generator/tmp/expansion/osm-china-bindings.json \
  --report tools/attraction-generator/tmp/expansion/osm-publication.json
```

提取时的 `--source` 可省略；提供时只保留可能与当前名录名称匹配的对象，加快行政区和几何核对。名录扩大后应重新提取。输入 PBF 的 SHA-256、对象类型/ID、原始标签、原始 WGS84 坐标和面几何存入候选文件；每条匹配保留源记录指纹。生成脚本将 OSM 与高德原始响应分开保存。

匹配要求名称和省市区一致、有明确旅游/历史/公园/宗教场所标签。镇村中心、单纯山峰、道路、停车场、入口和酒店不能直接当作景区坐标。同名多候选需核实；同名点完全落入唯一同名面时可归为该景区。面状景区取几何范围内部的代表点，非中心质点或入口。

发布前重新检查原始候选、源记录指纹、坐标转换和行政区，排除复用同一 OSM 对象及疑似已有同名地点。所有已有地图条目保持原样；重跑不重复添加。OSM 衍生坐标按 [ODbL 1.0](https://www.openstreetmap.org/copyright) 保留署名、对象链接和许可，前端详情与地图显示来源。转换算法来自 MIT 许可的 `coordTransform_py`，许可见 `LICENSE-coordtransform`；转换仅供地图展示，不用于测绘。

### 5. 核对官方自带坐标

广西名录的坐标字段未声明坐标系，页面虽然直接交给天地图显示，但不能据此证明每条原始值都使用同一坐标系。以下命令保留原始值，分别列出 WGS84、GCJ-02、BD-09 假设下的行政区和 OSM 代表点距离，供人工核对；不会直接发布，也不会用距离自动推断坐标系。

```bash
python tools/attraction-generator/audit_guangxi_coordinates.py \
  --raw tools/attraction-generator/tmp/expansion/guangxi.json \
  --osm-candidates tools/attraction-generator/tmp/expansion/osm-china-candidates.json \
  --output tools/attraction-generator/tmp/expansion/guangxi-coordinate-audit.json

python -m unittest discover -s tools/attraction-generator/tests -v
npm run build
```

## 一、获取全国 5A 景区和世界文化遗产

`fetch_mct.py`：从文旅部获取基本资料。

```bash
python3 tools/attraction-generator/fetch_mct.py
```

输出：`tmp/01-文旅部景点数据.json`、`tmp/01-文旅部采集报告.json`。

复用已采集资料、不重新请求网站：在命令后加 `--reuse-source`。

## 二、获取地方 4A、3A 景区

`fetch_local_tourism.py`：从地方文旅官网获取名录，目前仅接入上海、威海、大冶，不是全国全量。

```bash
python3 tools/attraction-generator/fetch_local_tourism.py
```

输出：`tmp/01-地方文旅景点数据.json`、`tmp/01-地方文旅原始页面.json`。

重跑复用已保存页面；需要重新采集时，在命令后加 `--refresh`。

## 三、汇总景点资料

`merge_sources.py`：合并前两份景点数据，去重并保留分类和来源，待确认的问题写在结果中。

```bash
python3 tools/attraction-generator/merge_sources.py
```

输出：`tmp/01-汇总景点数据（待确认）.json`。确认资料后再进行下一步。

## 四、通过高德补充坐标

`geocode_amap.py`：读取汇总资料，查询坐标并保存每次完整请求和返回。使用系统环境变量 `AMAP_WEB_SERVICE_KEY`，每秒最多 2 次请求。

```bash
python3 tools/attraction-generator/geocode_amap.py \
  --input "tools/attraction-generator/tmp/01-汇总景点数据（待确认）.json"
```

输出：`tmp/02-高德原始数据.json`。保留此文件，中断后重跑可续接，已缓存的相同请求不重复获取。

## 五、生成地图数据

`build_map_data.py`：整合景点资料与高德数据，生成程序使用的文件，不调用接口。

```bash
python3 tools/attraction-generator/build_map_data.py \
  --base "tools/attraction-generator/tmp/01-汇总景点数据（待确认）.json" \
  --amap tools/attraction-generator/tmp/02-高德原始数据.json
```

输出：`public/data/attractions.json`。明确标记跳过的景点不发布；其他景点存在待确认问题或缺少坐标时，不覆盖原地图数据。
