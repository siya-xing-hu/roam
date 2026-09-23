# 景点资源生成器

这是一个与前端应用隔离的一次性数据工具。它不会被项目的 `dev`、`test` 或 `build` 命令调用。

## 数据来源

- 上海：脚本直接读取上海市文化和旅游局公开的 5A、4A、3A HTML 名录；
- 江苏：脚本自动翻页读取江苏省政务服务网“省文化和旅游厅旗舰店”的景区名录；
- 浙江：脚本直接下载浙江省数据开放平台公开的嘉兴市“A级景区基本信息”JSON。

江苏名录公开名称、地市、区县和等级，但不包含门牌地址。生成器将这些官方字段组合成地理编码检索文本，不会把第三方补齐的门牌信息标成官方地址。

当前浙江官方公开文件只覆盖嘉兴市，并非全省完整名录。若以后取得省级或其他地市官方数据，可保存为 `inputs/zhejiang.json` 并在 `sources.json` 中增加一个 `json` 数据源。JSON 可以是数组或带有 `items` 数组的对象，支持官方中文字段，也支持下面的英文规范字段：

```json
[
  {
    "name": "景区名称",
    "grade": "4A",
    "address": "景区地址",
    "city": "城市",
    "district": "区县",
    "publishedAt": "2026-01-01",
    "sourceUrl": "https://官方来源页面"
  }
]
```

等级字段既支持 `2A`、`5A`，也支持官方文件中的 `AA`、`AAAAA` 写法。

## 运行

```bash
AMAP_WEB_SERVICE_KEY=你的高德Web服务Key python3 tools/attraction-generator/generate.py
```

命令从项目根目录运行。脚本仅使用 Python 标准库，无需安装额外依赖。生成器读取官方来源，将 1A–5A 映射为数字等级 1–5，使用高德地理编码补充 GCJ-02 坐标和行政编码，应用可选的人工覆盖，校验后原子写入 `public/data/attractions.json`，同时输出 `generation-report.json`。

任何数据源、地址或校验失败都会令命令返回失败，并保留上一版可用数据。高德 Key 不会写入项目文件。地理编码缓存位于 `cache/`，默认不提交。

运行生成器单元测试：

```bash
python3 -m unittest discover -s tools/attraction-generator/tests -p 'test_*.py'
```

## 人工覆盖

`overrides.json` 以稳定景点 ID 为键：

```json
{
  "sh-0123456789abcd": {
    "level": 8,
    "tags": ["亲子"]
  }
}
```

人工提升至 6–9 级后，原始 `5A` 等官方标签仍会保留。
