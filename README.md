# EricMingle 京东 MCP

独立单平台的个人购物 MCP，复用你日常浏览器中的 Kimi WebBridge 任务页。代码公开可查；EricMingle 自有增量采用 PolyForm Noncommercial 1.0.0，限许可允许的非商用用途。本组合分发不是 OSI 开源项目；上游 MIT 部分继续保持原有许可及商业使用权，见 LICENSE、NOTICE.md 与 licenses/。

## 运行

需要 Python 3.12 和已连接日常浏览器的 Kimi WebBridge；本仓库不包含该扩展或浏览器 Profile。本人完成平台登录与验证。先安装 `python -m pip install -r requirements.txt`，然后 `python src/server.py --stdio`。省略 `--stdio` 使用本机 HTTP，默认端口 8842，路径 `/mcp`。

`.env.example` 是变量示例，程序从进程环境读取。可用 `set -a; . ./.env; set +a` 导出自行创建的配置，再启动服务。`WEBBRIDGE_URL`、`WEBBRIDGE_SESSION`、`MCP_HOST`、`MCP_PORT`、`MCP_DATA_DIR` 可独立设置。HTTP 默认只监听本机；可选 `MCP_AUTH_TOKEN` 保护本机 HTTP；非 loopback 监听必须设置至少 24 字符令牌，外部 Host/Origin 还需明确加入 `MCP_ALLOWED_HOSTS`/`MCP_ALLOWED_ORIGINS`。这不替代云端 OAuth/HTTPS 网关，不应直接暴露未认证的 MCP。

## 能力与当前边界

首版提供21个工具：

| 用途 | 工具 |
| --- | --- |
| 浏览器与登录 | `login_jd`、`status_jd`、`close_jd_browser` |
| 搜索与详情 | `search_jd`、`get_jd_product` |
| 平台关注 | `favorite_jd_item`、`unfavorite_jd_item`、`favorite_list` |
| 平台购物车 | `cart_list`、`add_to_cart`、`remove_from_cart` |
| 官方客服 | `conversation_list`、`merchant_messages`、`contact_merchant` |
| 本地分类清单 | `watchlist_upsert`、`watchlist_remove`、`watchlist_list`、`watchlist_check` |
| 本人邮件通知 | `notification_configure`、`notification_status`、`notify_owner` |

搜索最多20件，`page=1`为首页；其他页单次点击当前实际可见的唯一页码，再确认活动页码及SKU结果集变化。不可见页码返回unsupported，不猜URL。价格筛选与排序在当前提取页内完成，排序明确标注`sort_scope: page_local`。详情返回可取得的参数和有限评价，缺字段标missing。服务只处理京东，不做跨平台比价。

收藏/取消已有真实往返；`favorite_list(page=1)`读取官方关注页的商品编号、URL、标题及可取得的价格/状态，分页仅沿实际页面链接。`conversation_list()`提供官方客服对象、时间和短摘要，缺项标missing；进入具体交流仍使用明确商品URL。

**购物车为实验能力。** 加购实际生效、删除工具及验证后的测试清理有真实证据；读回曾误报page_changed/unknown，列表也曾出现“购物车跑丢了”，不保证全部场景或列表稳定完整读取。新版本识别cart_unavailable与风险URL并停止，失败页不冒称空车。显式购物车动作仅前置本服务任务页；qty为本次新增量，operation_id持久去重。本次新成功提示或真实计数变化已确认时优先成功，证据不足返回unknown。同ID未知操作只读核对、不重点击，也不要换新ID重复尝试。购物车仅当前渲染行，不能把部分列表当全量。

消息读取已真实验收。商家发送仅完成草稿填入、唯一发送控件检查及清空，**未实发消息**；发送另有离线契约测试。`contact_merchant`先核对明确商品、店铺与会话，单次发送后读回，已有草稿不覆盖，未知结果不重发。本服务不下单、不支付，不提供卖家发布、图片消息或文件上传。

通知只发本人邮件，没有桌面弹窗或桌面兜底。把notification-mail.example.json复制到私人数据目录的notification-mail.json，填写已有邮件MCP地址、本人发件/收件身份及必要headers；也可用MCP_MAIL_CONFIG指定私有文件。不要提交真实配置。notify_owner提供本人邮件提醒；accepted是邮件工具接受请求，送达须收件箱读回，unknown不重发。邮件链路已完成收件箱与本人确认验收。

notification_configure后台目标价监测默认关闭，每件至少30分钟刷新；同商品同目标只提醒一次，风险/认证即暂停。自动价格触发有离线测试，尚未真实条件触发验收。本地分类清单支持增删查与目标价；watchlist_remove幂等删除对应URL的本地清单、报价和通知刷新记录，不改变平台关注或购物车。

购物车写入、客服发送及搜索翻页使用短动作与Python侧限时只读确认，客服初次身份渲染最多等待10秒，不重复导航或发送。当前仍依赖本人日常Brave和WebBridge，本轮不迁NAS；验证码由本人在浏览器处理，风险锁持久保留，不绕过验证。

## Docker / NAS

`docker build -t ericmingle-jd-mcp .`，`docker run --rm -i --env-file .env -v mcp-jd-data:/app/data ericmingle-jd-mcp` 通过 stdio 使用。容器内的桥接地址必须指向实际可达的 WebBridge 宿主，不能把容器自己的 loopback 当作 Mac。当前后端仍依赖既有浏览器/WebBridge；Docker 模板不表示已完成 NAS 独立运行或 NAS 迁移。

## 验证

`python -m pip install -r requirements-dev.txt`，`python -m pytest -q tests`；`python scripts/scan_public.py .` 扫描隐私与敏感材料，命中仅输出位置和类型。DOM 合同测试还需要 PATH 中可用的 Node.js。测试不访问真实平台、不登录、不读真实凭据。

## 致谢

[上游 HaonanYu123/JD-Taobao-MCP](https://github.com/HaonanYu123/JD-Taobao-MCP) 的解析与实践提供基础，原版权与 MIT 文本完整保留。EricMingle 为个人维护标识，不代表平台官方服务。

买方功能与登录、风控、写入读回机制对照 [DoLovya/xianyu-mcp-server](https://github.com/DoLovya/xianyu-mcp-server) 独立实现，未复制其GPL代码。
