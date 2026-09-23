# 行政边界数据

- 来源：DataV.GeoAtlas `areas_v3`
- 坐标系：GCJ-02
- 生成日期：2026-09-23
- 覆盖：全国省级边界、33 个省级区域的下级边界，以及 335 个地级区域的区县边界

数据按父级行政编码拆分，供前端按需加载：

- `provinces.geojson`：全国省级边界；
- `cities/{provinceAdcode}.geojson`：省内地级边界；
- `counties/{parentAdcode}.geojson`：直辖市或地级区域内区县边界；
- `index.json`：本次生成摘要和无法继续下钻的数据项。

DataV 官方说明其数据来自高德开放平台，仅供学习和交流使用。本项目当前为个人本地项目。台湾省下级边界接口在本次生成时返回 404；部分省直辖县级行政单位和不设区的地级市没有更下一级数据，均记录在 `index.json`。
