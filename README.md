# 旅行地图

一张只显示全国行政区划和景点的个人旅行地图。项目使用 Vite、TypeScript 和 ECharts，运行时不依赖在线地图、后端或第三方数据接口。

## 开发

```bash
npm install
npm run dev
```

## 构建与测试

```bash
npm test
npm run build
```

## 数据

- `public/data/boundaries/`：全国省、市、区县三级行政边界。
- `public/data/attractions.json`：前端直接加载的景点数据。
- `tools/attraction-generator/`：独立的一次性景点数据生成器，不参与前端开发和构建。

行政边界和景点坐标统一使用 GCJ-02。景点等级支持 0–9；官方 1A–5A 自动映射为 1–5，并保留对应 A 级标签。

当前仓库内的 `attractions.json` 是可运行的少量预览数据。完整生成需要一个高德 Web 服务 Key；上海与江苏名录会直接从官方网页读取，浙江当前使用官方开放平台提供的嘉兴市数据文件。具体限制和运行方法见 `tools/attraction-generator/README.md`。
