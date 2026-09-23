# 景点数据分阶段生成流水线设计

日期：2026-09-23

## 背景

现有 `tools/attraction-generator/generate.py` 在一次运行中同时读取地区文旅数据、调用高德地理编码并生成前端数据。这个流程把官方数据采集、人工确认和第三方坐标补全绑在一起，首次运行难以先检查来源数据，也不便于中断续跑或继续接入新的补全脚本。

新的流程将数据处理拆成两个独立入口脚本。每个脚本读取上一阶段的 JSON，保留已有字段，只补充自己负责的数据，并写入新的阶段文件。未来如需补充图片、开放时间或其他属性，可以继续沿用同一模式增加阶段，而不修改已经确认的上游文件。

## 目标

- 从文化和旅游部数据服务栏目获取全国 5A 级旅游景区和世界文化遗产数据。
- 第一阶段完全不读取或调用高德 Key，先产出可人工检查的统一 JSON。
- 第二阶段只读取第一阶段 JSON，通过高德补充 GCJ-02 坐标和行政区划。
- 高德请求严格低于每秒 3 次，并支持中断后从缓存继续。
- 每一阶段不覆盖上游文件；正式输出只在该阶段完整成功时原子写入。
- 保留每条数据的官方来源、原始分类和原始地区文本，避免把第三方补全结果冒充为官方字段。

## 非目标

- 本次不抓取全国 1A–4A 景区。文旅部当前“A级旅游景区”数据集实际提供全国 5A 景区。
- 本次不自动下载或永久托管文旅部图片；若页面提供稳定图片地址，只保留 URL。
- 本次不把长城、大运河等多地点遗产自动压缩为一个虚构坐标。
- 本次不直接覆盖 `public/data/attractions.json`。用户检查第二阶段结果后再决定如何发布给前端。
- 本次不删除旧的 `generate.py`；新流水线验证完成后再单独决定是否移除。

## 方案选择

采用“独立脚本 + 不可变阶段文件 + 独立缓存”方案：

```text
文化和旅游部公开数据
        |
        v
fetch_mct.py
        |
        v
tmp/01-mct-attractions.json     无高德数据，可人工检查和编辑
        |
        v
geocode_amap.py  <---------->  cache/amap-geocodes.json
        |
        v
tmp/02-geocoded-attractions.json
```

未采用原地修改同一 JSON 的方案，因为它难以比对、回退和恢复。未采用单个脚本的多子命令或通用插件框架，因为前者不符合“一项补全一个脚本”的使用方式，后者对当前两阶段需求过度设计。

## 文件与命令

新增两个入口脚本：

- `tools/attraction-generator/fetch_mct.py`
- `tools/attraction-generator/geocode_amap.py`

默认运行方式：

```bash
python3 tools/attraction-generator/fetch_mct.py
python3 tools/attraction-generator/geocode_amap.py
```

默认文件：

- 第一阶段输出：`tools/attraction-generator/tmp/01-mct-attractions.json`
- 第二阶段输出：`tools/attraction-generator/tmp/02-geocoded-attractions.json`
- 第一阶段报告：`tools/attraction-generator/tmp/01-mct-report.json`
- 第二阶段报告：`tools/attraction-generator/tmp/02-amap-report.json`
- 高德断点缓存：`tools/attraction-generator/cache/amap-geocodes.json`

`fetch_mct.py` 支持显式 `--output` 路径；`geocode_amap.py` 支持显式 `--input` 和 `--output` 路径。默认值保证从项目根目录直接运行即可。`fetch_mct.py` 不接受也不检查高德 Key。`geocode_amap.py` 在存在待查询项目时才要求 `AMAP_WEB_SERVICE_KEY`。

## 第一阶段：文旅部数据采集

`fetch_mct.py` 从用户指定的文旅部数据服务入口对应的两个官方数据集读取数据：

- 全国 5A 级旅游景区；
- 世界文化遗产。

采集器使用文化和旅游部公开列表及详情页面，不使用高德或其他地图数据。对于 5A 数据，采集名称、地区、评定年份、介绍、官方详情页和可用的官方图片 URL。对于世界文化遗产，采集名称、地点、登录年份、遗产类型和官方来源页面。

分页数据必须满足“页面报告总数等于最终去重前解析总数”。页面结构变化、分页缺失或总数不一致时，脚本返回失败并保留上一版成功文件。脚本不硬编码 359 作为永久总数，以允许文旅部后续新增景区；359 仅作为当前运行结果的人工核对基线。

### 统一记录结构

第一阶段文件顶层结构：

```json
{
  "schemaVersion": 1,
  "stage": "mct-source",
  "generatedAt": "2026-09-23T00:00:00.000Z",
  "sources": [],
  "summary": {
    "total": 0,
    "ready": 0,
    "needsReview": 0
  },
  "items": []
}
```

每条记录的核心字段：

```json
{
  "id": "mct-5a-34838",
  "name": "故宫博物院",
  "classifications": [
    {
      "type": "a-level",
      "value": "5A",
      "year": "2007"
    }
  ],
  "regionText": "北京",
  "provinceHints": ["北京市"],
  "description": "官方介绍",
  "officialImageUrl": "https://...",
  "geocodeQuery": "北京市故宫博物院",
  "reviewStatus": "ready",
  "sources": [
    {
      "name": "文化和旅游部",
      "url": "https://sjfw.mct.gov.cn/...",
      "sourceId": "34838",
      "publishedAt": ""
    }
  ]
}
```

字段规则：

- 优先使用文旅部详情 ID 构造稳定 ID；没有详情 ID 时使用分类、规范化名称和原始地点文本的 SHA-256 摘要。
- `regionText` 保留文旅部原文；`provinceHints` 是从原文提取的提示，不声称是高德行政区划结果。
- `geocodeQuery` 是下一阶段使用的可编辑查询文本，不是官方地址。
- `reviewStatus` 取值为 `ready`、`needs_review` 或 `skip`。
- 官方页面缺失字段时使用空字符串或空数组，不从非官方来源猜测。

### 合并与人工审核

同一个对象同时属于 5A 景区和世界文化遗产时，应尽量形成一条记录并保留两个 `classifications`。只有规范化名称一致且地区兼容时才自动合并，不做模糊名称合并。

以下情况标记为 `needs_review`：

- 长城、大运河等跨省或多地点遗产；
- 一个遗产包含多个可独立定位的组成部分；
- 5A 名称和遗产名称疑似对应但无法可靠自动确认；
- 无法生成唯一、合理的 `geocodeQuery`。

用户可在第一阶段文件中修改 `geocodeQuery`、将记录拆成多个唯一 ID 的记录、设置 `ready`，或设置 `skip`。第二阶段不会自动处理 `needs_review` 和 `skip`。

## 第二阶段：高德坐标补全

`geocode_amap.py` 读取第一阶段文件，验证 `schemaVersion` 和 `stage`，然后只处理 `reviewStatus == "ready"` 且尚未命中缓存的记录。

第二阶段保留第一阶段的全部字段，并为成功记录增加：

```json
{
  "location": {
    "lng": 116.397,
    "lat": 39.916,
    "coordSystem": "GCJ-02",
    "province": "北京市",
    "city": "北京市",
    "district": "东城区",
    "adcode": "110101",
    "provider": "amap",
    "queryFingerprint": "...",
    "queriedAt": "2026-09-23T00:00:00.000Z"
  }
}
```

只要仍有 `needs_review`、永久查询失败或无结果记录，脚本返回非零状态，不替换上一版完整成功输出，并在报告中给出项目 ID、名称和不含 Key 的错误原因。`skip` 表示用户明确决定不把该记录纳入坐标数据：正式输出的 `items` 只包含成功地理编码的记录，另以顶层 `skippedItems` 保留被跳过记录及其原因，避免静默丢失。`needs_review` 不等同于 `skip`，必须由用户处理后才能生成新的正式第二阶段文件。

## 限速、重试与断点缓存

高德调用保持单线程。任意两次 HTTP 请求开始时间至少间隔 0.4 秒，对正常请求和重试都生效，因此理论上限约为每秒 2.5 次，低于用户账户的每秒 3 次限制。

缓存键由以下内容计算：

```text
provider version + normalized geocodeQuery
```

查询文本发生变化会产生新键并重新查询。缓存条目记录查询指纹、状态、无 Key 的结果、尝试时间和必要的错误分类。Key、含 Key 的 URL、请求头和原始鉴权错误不得写入缓存、报告或终端。

每次成功、明确无结果或确认永久失败后，立即通过临时文件加 `os.replace` 原子保存缓存。进程被中断后，下次运行跳过已成功项目。网络超时、服务端错误和高德限流属于可重试错误，初次请求失败后最多额外重试 3 次，采用 1、2、4 秒退避；无效 Key 或无权限错误立即终止，避免把所有记录误报为地址失败。

第二阶段正式输出同样采用临时文件加 `os.replace`。执行期间只更新缓存和报告，所有可处理记录成功后才替换正式输出。

## 后续阶段约定

未来增加数据脚本时遵循相同契约：

1. 明确声明接受的 `schemaVersion` 和 `stage`；
2. 不修改输入文件；
3. 保留未知字段；
4. 只增加或更新本阶段负责的命名字段；
5. 使用稳定 ID 关联记录；
6. 将断点状态放在独立缓存中；
7. 完成后原子写入新的阶段文件。

这样可以继续形成 `03-*`、`04-*` 文件，而不让某个脚本同时承担采集、坐标、图片和前端发布等职责。

## 测试与验收

测试全部使用本地 HTML/JSON 固件和模拟网络，不依赖文旅部或高德在线状态。

第一阶段测试覆盖：

- 5A 列表分页和详情解析；
- 世界文化遗产表格解析；
- 当前文旅部字段的标准化；
- 精确合并和不安全模糊合并的拒绝；
- 多地点遗产标记为 `needs_review`；
- 页面报告总数不一致时不覆盖成功输出；
- 稳定 ID 和来源追溯。

第二阶段测试覆盖：

- 无待查询项目时不要求 Key；
- 缺少 Key 时安全失败；
- 请求起始间隔不小于 0.4 秒；
- 限流和网络错误的退避重试；
- 每条结果落盘后可中断续跑；
- 查询文本改变后缓存失效；
- 报告、异常和缓存不泄漏 Key；
- 存在未审核或失败记录时不覆盖完整输出；
- 全部成功时输出 GCJ-02 坐标和行政编码。

验收时先运行离线测试，再实际运行第一阶段并人工核对文旅部页面报告总数、分类数和 `needs_review` 列表。确认第一阶段文件后再运行第二阶段；最终核对成功数、缓存数、失败数和输出条数一致。

## 工作区兼容性

当前工作区已删除原有 `tools/attraction-generator/tests/test_generate.py` 和 `inputs/.gitkeep`。实现时保留这些现有删除，不恢复旧文件；新测试使用新的文件名。除非用户另行确认，旧 `generate.py`、旧地区来源配置和现有前端数据均不删除或覆盖。
