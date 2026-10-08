# EricMingle 京东 MCP

独立单平台的个人购物 MCP，复用你日常浏览器中的 Kimi WebBridge 任务页。代码公开可查；EricMingle 自有增量采用 PolyForm Noncommercial 1.0.0，限许可允许的非商用用途。本组合分发不是 OSI 开源项目；上游 MIT 部分继续保持原有许可及商业使用权，见 LICENSE、NOTICE.md 与 licenses/。

## 运行

需要 Python 3.12 和已连接日常浏览器的 Kimi WebBridge；本仓库不包含该扩展或浏览器 Profile。本人完成平台登录与验证。先安装 `python -m pip install -r requirements.txt`，然后 `python src/server.py --stdio`。省略 `--stdio` 使用本机 HTTP，默认端口 8842，路径 `/mcp`。

`.env.example` 是变量示例，程序从进程环境读取。可用 `set -a; . ./.env; set +a` 导出自行创建的配置，再启动服务。`WEBBRIDGE_URL`、`WEBBRIDGE_SESSION`、`MCP_HOST`、`MCP_PORT`、`MCP_DATA_DIR` 可独立设置。HTTP 默认只监听本机；可选 `MCP_AUTH_TOKEN` 保护本机 HTTP；非 loopback 监听必须设置至少 24 字符令牌，外部 Host/Origin 还需明确加入 `MCP_ALLOWED_HOSTS`/`MCP_ALLOWED_ORIGINS`。这不替代云端 OAuth/HTTPS 网关，不应直接暴露未认证的 MCP。

## 能力与当前边界

首轮实测因不完整购物车前态被拒绝、零点击；该门槛已修，修复后的真实加购往返尚未验证。

通知通道测试已由本人确认看见；自动目标价监测仅经过离线验证，尚未真实触发。

本版购物车与商家发送为实验性适配：离线测试通过，完整真实购物车往返尚未通过；商家发送未实发验收。遇unknown/unverified须核对页面，不应盲重试。主动通知已通过本人可见验收。

每个MCP只负责本平台，跨平台比价由调用智能体组合查询。搜索、详情、原生收藏及本地分类清单分别存在，缺失字段明确标记。

已实现 cart_list、add_to_cart、remove_from_cart。qty为本次新增数量；operation_id用于重试去重，结果未知不重复加购。购物车列表只表示当前渲染行，不能视为全量。

商家交流提供 merchant_messages 和 contact_merchant：先从目标商品的原生客服入口验证对应会话，正文显式传入，发送一次并核对新增消息；已有草稿不覆盖，对象不符拒绝，未知发送结果不自动重发。接口按真实网页结构实现并经过离线测试，本轮未向真实商家发送测试消息，不能把离线测试当作真实送达保证。

主动通知提供 notification_configure、notification_status、notify_owner。默认关闭自动监测；显式开启后每件清单商品至少30分钟刷新，共用本平台业务间隔与风险锁。真实展示价命中目标后自动发本人通知；同商品同目标价只提醒一次（价格回升再跌不重发）。智能体判断交易条件达成时也可调用 notify_owner 提交摘要与依据，由本人最终核对和下单支付。accepted仅证明通道接受，不证明本人已看到。

Mac复用现有macOS通知通道；其他宿主可用MCP_NOTIFY_COMMAND_JSON配置本人控制的命令（stdin接收JSON title/body），没有内置个人地址或密钥。通知持久去重，中断结果unknown不盲重发。后台刷新只处理有目标价的本平台本地清单，不扫描全站；风险/认证阻断即暂停。notification_status可查开关及回执。

不会自动下单支付，不处理验证码；低频操作仍不能保证平台永不风控。通知、消息正文及账号运行状态只保存在本人数据目录，不应提交到仓库。尚未部署NAS或云端，当前仍依赖现有浏览器/WebBridge。详见现有测试与工具描述，具体真实验收范围以版本说明为准。

## Docker / NAS

`docker build -t ericmingle-jd-mcp .`，`docker run --rm -i --env-file .env -v mcp-jd-data:/app/data ericmingle-jd-mcp` 通过 stdio 使用。容器内的桥接地址必须指向实际可达的 WebBridge 宿主，不能把容器自己的 loopback 当作 Mac。当前后端仍依赖既有浏览器/WebBridge；Docker 模板不表示已完成 NAS 独立运行或 NAS 迁移。

## 验证

`python -m pip install -r requirements-dev.txt`，`python -m pytest -q tests`；`python scripts/scan_public.py .` 扫描隐私与敏感材料，命中仅输出位置和类型。DOM 合同测试还需要 PATH 中可用的 Node.js。测试不访问真实平台、不登录、不读真实凭据。

## 致谢

[上游 HaonanYu123/JD-Taobao-MCP](https://github.com/HaonanYu123/JD-Taobao-MCP) 的解析与实践提供基础，原版权与 MIT 文本完整保留。EricMingle 为个人维护标识，不代表平台官方服务。
