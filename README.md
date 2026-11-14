# 车间能耗驱动归因

特征选择与回归的真实业务分析小型项目。完整模拟客户、痛点、上下游、算法公式与一天工作剧本见 [BUSINESS.md](BUSINESS.md)。

## 模块与业务流程
数据接入（JSON/CSV、字段验证、批次指纹）→ 参数化分析（异步 Python worker）→ 结果复核（指标、图形、明细、解释、批准）→ 定时分发（本地收件箱、明细 CSV＋完整结论 JSON、SHA256 清单）；另有业务总览和审计轨迹。
三个贯穿全栈的实体：Dataset 数据批次、Job 分析任务、Report 分发计划；辅助 User、Audit、Delivery（每期归档）。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18215；后端文档 http://localhost:19215/docs；数据库宿主端口 17715。
`SEED_DEMO=true` 自动幂等 seed；不覆盖用户修改。手动 seed 命令可重复执行。

## 演示账号与权限
|账号|密码|真实权限|
|---|---|---|
|admin@demo.local|Demo123!|接入、执行、批准、分发、暂停、读取与下载|
|analyst@demo.local|Demo123!|接入、执行、读取与下载；不能批准或安排分发|
|viewer@demo.local|Demo123!|只读与下载|

## 技术与本地开发
Python 3.12 / FastAPI / SQLAlchemy / NumPy / SciPy / scikit-learn；MySQL 8.0；Redis 7；Nginx；原生 HTML CSS JavaScript ES modules（用户指定）。后端所有校验、持久化和算法在服务端。无前端打包步骤。
后端：安装 backend/requirements.txt，设置 DATABASE_URL、REDIS_URL、JWT_SECRET、ARTIFACT_DIR 后在 backend/ 执行 `python -m migrations.initial`、`python -m scripts.seed_demo`、`uvicorn src.main:app`，另开 `python -m src.worker`。前端使用带 /api 反向代理的 Nginx，直接打开 index.html 不支持 API。

## 目录与工程约定
backend/src/{models,schemas,services,routes,controllers,middlewares,constants,config,utils}；backend/scripts/seed_demo.py；backend/migrations/initial.py。
frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}。
Role 枚举：admin/analyst/viewer；JobStatus：queued/running/completed/failed。后端 backend/src/constants/enums.py，前端 frontend/src/constants/enums.js；路由权限在 middlewares/auth.py；页面按相同角色控制操作入口。

## 环境变量与 Docker
COMPOSE_PROJECT_NAME=energy-ridge；FRONTEND_PORT=18215；BACKEND_PORT=19215；DB_PORT=17715；DB_PASSWORD、JWT_SECRET 见 .env.example；SEED_DEMO 默认 true。共享命名产物卷供后端下载与 worker 写入，数据库/Redis 各持久卷。
恰好五服务均有健康检查。Redis 队列加 SQL 排队状态兜底；worker 按 next_run 生成本地收件箱文件。周期 60 秒至 30 天。分发结果为批准分析的固定快照；若需新数据重新计算并建立新计划。当前不连接邮件或企业聊天。

## 归档一致性（期次快照）
同一计划的同一应发期次只产生一套归档：`deliveries` 以 `(report_id, period)` 唯一约束声明期次，并以 `reports.period` 的比较交换认领到期槽，因此并发 worker 或重复 tick 都不会重复建档。
每期归档包含明细 CSV 与完整结论 JSON（含计划名、期次、应发时间、批准参数与结果、CSV 指纹）；两份文件各自做 SHA256，`checksum` 为两文件指纹的清单指纹，下载前必须两份齐全且与登记一致。
- 先登记、后写文件：进程在登记前中断，下一 tick 从 `pending` 状态重新生成；文件已写但未登记，同样按待完成恢复。文件采用临时文件 + 原子替换，校验通过后才标记 `complete`。
- 登记后文件缺失、被截断或被改写，worker 在下一 tick 依据当期冻结快照重建；收件箱也可手动「从当期快照修复」。修复次数与时间可在收件箱核对。
- 每期冻结到期时已批准的分析参数与结果；后续重算、重新批准或更换计划引用的新分析都不会混入旧期次，重试与修复只复用当期快照。
- 收件箱按 `ready`／不一致原因展示期次、应发时间、两份文件大小与 SHA256；未通过完整性核对时下载返回 409。旧格式归档保留可下载明细，但标记为 legacy 且不可重建。
错误响应、请求编号、Redis 每 IP 每分钟 240 次限流、JWT 和数据库操作审计。演示密钥与密码仅供本机模拟；生产部署前配置独立密钥与账号。

## 数据与验收
所有数据为固定随机种子生成的模拟业务数据，不能作为实际生产决策证据。项目独立算法、输入边界及业务局限详见 BUSINESS.md。测试与截图统一存放 ../../test-artifacts/gb-1016/，源码不含日志/截图/测试报告。
清理本项目演示数据：`docker compose down -v --remove-orphans`（会删除本项目命名卷）。

## License
MIT，见 LICENSE。
