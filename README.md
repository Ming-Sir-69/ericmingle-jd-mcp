# EricMingle 京东 MCP

独立单平台的个人购物 MCP，复用你日常浏览器中的 Kimi WebBridge 任务页。代码公开可查；EricMingle 自有增量采用 PolyForm Noncommercial 1.0.0，限许可允许的非商用用途。本组合分发不是 OSI 开源项目；上游 MIT 部分继续保持原有许可及商业使用权，见 LICENSE、NOTICE.md 与 licenses/。

## 运行

需要 Python 3.12 和已连接日常浏览器的 Kimi WebBridge；本仓库不包含该扩展或浏览器 Profile。本人完成平台登录与验证。先安装 `python -m pip install -r requirements.txt`，然后 `python src/server.py --stdio`。省略 `--stdio` 使用本机 HTTP，默认端口 8842，路径 `/mcp`。

`.env.example` 是变量示例，程序从进程环境读取。可用 `set -a; . ./.env; set +a` 导出自行创建的配置，再启动服务。`WEBBRIDGE_URL`、`WEBBRIDGE_SESSION`、`MCP_HOST`、`MCP_PORT`、`MCP_DATA_DIR` 可独立设置。HTTP 默认只监听本机；可选 `MCP_AUTH_TOKEN` 保护本机 HTTP；非 loopback 监听必须设置至少 24 字符令牌，外部 Host/Origin 还需明确加入 `MCP_ALLOWED_HOSTS`/`MCP_ALLOWED_ORIGINS`。这不替代云端 OAuth/HTTPS 网关，不应直接暴露未认证的 MCP。

## 能力与当前边界

京东购物车仍属实验能力：本机实际加购生效过，但读回曾误报page_changed/unknown；删除工具已通过，安全验证由本人完成后已用工具清理测试新增项，计数恢复。京东页面本轮多次出现“购物车跑丢了”，且触发过官方风控，未达长期稳定或全部场景验收。新版本识别cart_unavailable与风险URL并停止，未知操作只读核对、不重复写；显式购物车动作仅前置本服务已有任务页，后台商品读取不前置。

每个MCP独立，只处理本平台；跨平台比价由调用智能体负责。

原生购物车提供 cart_list/add_to_cart/remove_from_cart；qty为本次新增数量，operation_id持久去重。明确规格、唯一目标和单次动作后再读回，未知不自动重试；购物车仅当前渲染行，不能把部分列表当全量。

商家交流提供 merchant_messages/contact_merchant，先核对商品对应店铺与会话，单次发送后读回，已有草稿不覆盖；三站未向真实商家发送测试消息，实发仍未验收。本人完成认证、最终下单支付。

通知只发本人邮件，没有桌面弹窗分支或桌面兜底。把 notification-mail.example.json 复制到私人数据目录的 notification-mail.json，填写已有邮件MCP地址、本人发件/收件身份及必要headers；也可用MCP_MAIL_CONFIG指定私有文件。不要提交真实配置。notify_owner用于智能体确认交易条件后的邮件提醒；accepted是邮件工具报告发送，送达须收件箱读回。未知投递不盲重发。本人邮件链路已完成收件箱正文与本人确认验收。

notification_configure后台目标价监测默认关闭，每件至少30分钟刷新；同商品同目标只提醒一次，风险/认证即暂停。自动价格触发目前有离线测试，尚未长期运行或真实条件触发验收。本地分类清单与平台收藏/购物车分别存在。

已移除写动作中的网页长计时轮询，使用短动作+Python侧限时只读确认；真实站点样例成功不等于全部商品/长期免风控。当前仍依赖既有日常浏览器和WebBridge，本轮未迁NAS。

## Docker / NAS

`docker build -t ericmingle-jd-mcp .`，`docker run --rm -i --env-file .env -v mcp-jd-data:/app/data ericmingle-jd-mcp` 通过 stdio 使用。容器内的桥接地址必须指向实际可达的 WebBridge 宿主，不能把容器自己的 loopback 当作 Mac。当前后端仍依赖既有浏览器/WebBridge；Docker 模板不表示已完成 NAS 独立运行或 NAS 迁移。

## 验证

`python -m pip install -r requirements-dev.txt`，`python -m pytest -q tests`；`python scripts/scan_public.py .` 扫描隐私与敏感材料，命中仅输出位置和类型。DOM 合同测试还需要 PATH 中可用的 Node.js。测试不访问真实平台、不登录、不读真实凭据。

## 致谢

[上游 HaonanYu123/JD-Taobao-MCP](https://github.com/HaonanYu123/JD-Taobao-MCP) 的解析与实践提供基础，原版权与 MIT 文本完整保留。EricMingle 为个人维护标识，不代表平台官方服务。
