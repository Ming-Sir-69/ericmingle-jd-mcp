# EricMingle 京东 MCP

独立单平台的个人购物 MCP，复用你日常浏览器中的 Kimi WebBridge 任务页。代码公开可查；EricMingle 自有增量采用 PolyForm Noncommercial 1.0.0，限许可允许的非商用用途。本组合分发不是 OSI 开源项目；上游 MIT 部分继续保持原有许可及商业使用权，见 LICENSE、NOTICE.md 与 licenses/。

## 运行

需要 Python 3.12 和已连接日常浏览器的 Kimi WebBridge；本仓库不包含该扩展或浏览器 Profile。本人完成平台登录与验证。先安装 `python -m pip install -r requirements.txt`，然后 `python src/server.py --stdio`。省略 `--stdio` 使用本机 HTTP，默认端口 8842，路径 `/mcp`。

`.env.example` 是变量示例，程序从进程环境读取。可用 `set -a; . ./.env; set +a` 导出自行创建的配置，再启动服务。`WEBBRIDGE_URL`、`WEBBRIDGE_SESSION`、`MCP_HOST`、`MCP_PORT`、`MCP_DATA_DIR` 可独立设置。HTTP 默认只监听本机；可选 `MCP_AUTH_TOKEN` 保护本机 HTTP；非 loopback 监听必须设置至少 24 字符令牌，外部 Host/Origin 还需明确加入 `MCP_ALLOWED_HOSTS`/`MCP_ALLOWED_ORIGINS`。这不替代云端 OAuth/HTTPS 网关，不应直接暴露未认证的 MCP。

## 能力与当前边界

2026-10-08 已在一件真实商品上完成收藏→取消收藏往返，前后状态读回一致，最终恢复未收藏。此结果仅证明该页面样例，不保证所有页面样式。

搜索、详情与状态返回结构化结果。平台原生收藏/取消收藏已实现；实际能力以工具描述及平台当前页面为准，平台要求 App 或验证时明确停止。购物车仍未完成，不应通过其他控件代替加购。字段缺失/不支持会明确标记。风控状态持久化；重启或关闭任务页不能清除风控。本人处理后由状态工具核对登录与风控。不会下单、付款、绕过验证码或自动发送商家消息。

离线测试与上面明确列出的真实样例分别记录；未列出往返成功的购物操作仍未验收。价格/库存等以平台当前页面为准。本地分类购物清单支持目标价保存与调用时检查。观测仅来自本服务真实搜索/详情读取的展示价；只接受一小时内观测，命中在工具返回结果中列出，不联网刷新，不做后台监控或主动推送。此清单与平台原生收藏/购物车分别存在。跨平台比较可由调用方组合各 MCP 的结果；自动同 SKU 匹配、主动通知、商家询价发送、购物车和云端部署未完成。

## Docker / NAS

`docker build -t ericmingle-jd-mcp .`，`docker run --rm -i --env-file .env -v mcp-jd-data:/app/data ericmingle-jd-mcp` 通过 stdio 使用。容器内的桥接地址必须指向实际可达的 WebBridge 宿主，不能把容器自己的 loopback 当作 Mac。当前后端仍依赖既有浏览器/WebBridge；Docker 模板不表示已完成 NAS 独立运行或 NAS 迁移。

## 验证

`python -m pip install -r requirements-dev.txt`，`python -m pytest -q tests`；`python scripts/scan_public.py .` 扫描隐私与敏感材料，命中仅输出位置和类型。DOM 合同测试还需要 PATH 中可用的 Node.js。测试不访问真实平台、不登录、不读真实凭据。

## 致谢

[上游 HaonanYu123/JD-Taobao-MCP](https://github.com/HaonanYu123/JD-Taobao-MCP) 的解析与实践提供基础，原版权与 MIT 文本完整保留。EricMingle 为个人维护标识，不代表平台官方服务。
