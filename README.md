# 足迹

一张只显示全国行政区划和景点的个人足迹地图。项目使用 Vite、TypeScript 和 MapLibre GL JS，景点由 PostgreSQL 和只读接口提供，行政边界使用本地文件，需要浏览器支持 WebGL。

拖动、滚轮或双指缩放即可浏览；市、区县名称随缩放自动显示。点击地图区域或全国／省／市／区县路径可定位对应范围。景点标记按等级筛选和缩放门槛显示；选中区域后，常显该区域及周边的景点名称，更远的景点悬停时显示名称。周边按区域范围向外扩展 20%，每个方向至少扩展约 20 公里；本区域名称优先显示，周边名称用较淡的颜色区分。拖动和缩放不会改变面板中的区域、列表或详情。

点击地图上的景点或列表条目，会进入该景点所属的最小可用行政区域，同步定位区域范围、更新路径和列表，并打开景点详情。缺少对应区县边界时回退到所属市或省；返回全国后恢复全国景点名称。

## 开发

```bash
npm install
npm run db:up
npm run db:migrate
npm run db:import
npm run dev
```

需要 Node.js 22.12+ 和 Docker。`db:up` 创建独立共享 PostgreSQL 18.6，地址为 `127.0.0.1:5432`，自动生成本地凭据；重复运行保留原凭据和数据。其他项目后续可以按数据库接入，配置和迁移说明见 [共享 PostgreSQL](infra/postgres/README.md)。

`npm run db:verify` 验证景点往返、区域查询和账号权限；`npm run db:backup` 备份 `roam` 和实例角色。数据库密码只存在 `.env.local` 和被 Git 忽略的秘密文件中，不能使用 `VITE_` 前缀。

## 构建

```bash
npm run build
```

Netlify 导入 GitHub 仓库并选择 `main` 分支，仓库内的 `netlify.toml` 会设置 Node.js 22、构建命令 `npm run build`、发布目录 `dist` 和函数目录 `netlify/functions`。景点函数通过 `/api/attractions` 提供接口。在 Netlify 环境变量中配置 `DATABASE_URL`，确保作用域包含 Functions；修改后重新部署。

Vercel 同样可用：选择 Vite，并保留根目录 `api/attractions.ts` 为 Node.js Function，配置仅服务端可见的 `DATABASE_URL`。

线上 `DATABASE_URL` 必须指向部署平台能访问的 PostgreSQL（例如托管数据库），并使用只读应用账号；本机 `127.0.0.1` 仅供本地开发。数据库结构和数据用独立迁移账号提前导入，构建过程不会自动改库。托管数据库初始化账号和只读权限的方式见 [共享 PostgreSQL](infra/postgres/README.md)。

需要纯静态快照时，显式使用 `VITE_ATTRACTION_DATA_SOURCE=static npm run build`；默认模式请求 `/api/attractions`，数据库出错不会静默切回旧 JSON。

## 数据

- `public/data/boundaries/`：全国省、市、区县三级行政边界。
- PostgreSQL `roam` 数据库：在线景点目录，保留来源、坐标和行政区关联。
- `public/data/attractions.json`：已核验景点导入快照，也供显式静态模式使用。
- `database/migrations/`、`tools/database/`：版本化建表、导入、校验和备份。
- `tools/attraction-generator/`：独立的一次性景点数据生成器，不参与前端开发和构建。

行政边界和景点坐标统一使用 GCJ-02。景点等级支持 0–9；官方 1A–5A 自动映射为 1–5，并保留对应 A 级标签。

导入以稳定 ID 更新，不删除快照中缺少的数据库记录；重复导入会更新快照中同 ID 的字段，因此在数据库直接校正后应先同步回采集数据或备份，再重新导入。原始行政编码与最小可用边界区域分别保存，编码不一致时标记回退关系。当前查询接口支持 `region`、`bbox`、`minimumLevel`、`limit`；地图暂时仍整批加载并沿用现有显示规则。自动缩放选区、周边合计 20＋20 等规则尚未在本次数据库接入中启用。

景点工具汇集文旅部与省级文旅官方名录，保留原件、来源日期和高德完整原始响应，支持断点续跑。全国增补使用 `publish_catalog.py` 发布已核验的新地点并保留原有点位；未确认名称、坐标或重复 POI 单独列入报告。具体运行方法见 [采集说明](tools/attraction-generator/README.md)，当前数量与缺口见 [数据覆盖报告](docs/attraction-data-coverage.md)。

坐标也支持 OpenStreetMap 离线数据：匹配官方名称和行政区，将原始 WGS84 转为 GCJ-02 后，通过 `publish_coordinates.py` 增量补充。OSM 点保留原始坐标、对象链接、数据快照摘要和 ODbL 许可；景区面数据使用范围内代表点，不表示入口。地图及详情显示坐标署名。未声明坐标系的官方经纬度先进入核对报告。
