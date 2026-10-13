# 共享 PostgreSQL

本配置独立于其他项目的容器。PostgreSQL 18.6 实例名为 `shared-postgres`，先创建足迹数据库 `roam`；未来将两个现有服务的数据逻辑迁移进不同数据库。

| 项目 | 配置 |
| --- | --- |
| 本机地址 | `127.0.0.1:5432` |
| Docker 网络 | `shared-postgres-network` |
| 同网络容器地址 | `shared-postgres:5432` |
| 数据卷 | `shared-postgres18-data`，PostgreSQL 18 挂载到 `/var/lib/postgresql` |
| 管理账号 | `shared_admin`，只用于实例管理 |
| 导入账号 | `roam_owner`，只管理 `roam` 库 |
| 应用账号 | `roam_app`，只读取 `roam` schema |

```bash
# 在项目根目录执行
npm run db:up
npm run db:migrate
npm run db:import
npm run db:verify
docker compose -f infra/postgres/compose.yaml ps
```

初始化脚本仅在空卷首次启动时执行。之后通过迁移文件升级表结构，不通过重新生成密码或删除卷处理问题。密码随机生成，存于本目录 `.secrets/`（目录 700、文件 600）；项目根目录 `.env.local` 保存足迹连接串。重复启动保留这些文件，不向终端打印密码。

网络和卷由启动脚本独立创建，Compose 标记为 external，普通 `down` 和 `down -v` 均不删除它们。`restart: unless-stopped` 在 Docker 启动时恢复此前运行的服务。数据库未绑定局域网或公网。

可使用 `docker exec -it shared-postgres psql -U shared_admin -d postgres` 管理实例。管理账号不要交给业务服务。实例当前未安装 PostGIS；GCJ-02 坐标和边界保存在显式标注坐标系的列中，后续启用 PostGIS 时需正确处理坐标系，不能冒充 WGS84。

## 备份与恢复

```bash
npm run db:backup
```

生成 `backups/<时间>-roam.dump`（自定义格式）和 `backups/<时间>-roles.sql`。角色备份包含密码校验值，按敏感文件保存；备份文件为 600 权限且被 Git 忽略。该命令只备份 `roam` 数据，未来每增加一个业务库，需要将该库加入备份范围。

备份需另存到独立磁盘或备份服务；容器数据卷持久化不等于备份。恢复时先新建一个空数据库，在该库执行 `pg_restore --exit-on-error`，核验数量与关键记录后再切换连接。不要在现有业务库直接执行带 `--clean` 的恢复。

## 后续合并其他两个服务

1. 检查各服务的扩展、排序规则和角色权限，为每个服务建立独立库与账号。新建库后 `REVOKE ALL ON DATABASE <库名> FROM PUBLIC`，仅给本项目账号 `CONNECT`，按角色授予 schema/table 权限。
2. 使用 PostgreSQL 18 的 `pg_dump` 客户端从旧 15/18 实例导出，自定义格式恢复到新库；不要直接复用旧版本的数据目录。逐个映射角色，保留原有 API、worker、迁移账号的职责。
3. 迁移演练和数据检查完成后，在最终切换窗口暂停相关写入，再导出/恢复或采用适当增量同步，避免初次导出之后的新数据丢失。
4. 在各项目 Compose 中声明外部网络 `shared-postgres-network`，将需要数据库连接的服务接入，地址设为 `shared-postgres:5432/<各自数据库>`。主机程序继续用 `127.0.0.1`。
5. 检查读写、任务和回滚路径后，再决定是否停旧实例。切换后若新库已有写入，回滚需要同步这些新增数据。

本次没有迁移、停止或修改现有两个服务的数据库。分库和独立账号提供数据权限边界；CPU、内存、连接数和数据库维护窗口仍由整个实例共享。

## Vercel / 托管 PostgreSQL

Vercel 上的 `127.0.0.1` 不是这台电脑。上线时使用可访问的托管 PostgreSQL，或经过专门网络配置的服务；不要为此直接将本地数据库端口公开到公网。

数据库和账号在托管服务控制台创建：迁移账号拥有 `roam` schema 的建表权限，运行账号只授予 `USAGE ON SCHEMA roam` 和 `SELECT ON ALL TABLES IN SCHEMA roam`。设置迁移账号的默认权限，使今后新增表也可被运行账号读取。不要给应用账号数据库所有者或超级用户权限。

在迁移命令运行环境设置 `DATABASE_MIGRATION_URL`，运行 `db:migrate` 和 `db:import`；在 Vercel 服务端环境配置运行账号的 `DATABASE_URL`，使用供应商要求的 TLS 参数。两个连接均不加 `VITE_` 前缀。托管环境迁移脚本只创建 `roam` schema 和表，不创建容器或角色。

接口入口为 `api/attractions.ts`，数据库连接池每个实例最多 5 个连接；大规模部署优先使用供应商的连接池地址。本地 Vite 开发及预览共享同一套接口逻辑。

## 数据边界

此次入库的是已发布景点和行政边界。采集工具中的待匹配名录、原始接口响应继续留在现有采集目录，不会被误作已核验景点发布。区域几何和来源元信息已存入数据库，前端边界渲染仍走现有静态 GeoJSON。
