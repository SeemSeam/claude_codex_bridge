# 简化 Web 客户端开发计划

Date: 2026-09-18
Role: topic-capsule
Status: Planning — shape-plan；本轮仅分析和规划，未授权正式实现或部署。
Authority: 用户要求在稳定 APK 基础上安排简化网页版，先分析计划。
Related: [Mobile roadmap](../roadmap.md)、[Mobile baseline](../../../baseline/README.md)

## 目标与边界

提供 iPhone Safari、Android Chrome 和桌面浏览器可用的轻量 CCB 客户端。
复用现有网关和 Relay，让用户配对电脑、选择项目和 Agent、查看对话并发送文本。
Web 是现有原生客户端的补充；不替换 APK，不改变电脑上的 Provider、会话或运行时权威。
不以两端版本号一致作为兼容条件，也不通过新建服务器会话代理替代现有端到端加密。

本文件是新补充方案，不把历史 Web-first 决策恢复为已批准架构。
相关背景：[Decision 002](../decisions/002-gateway-pwa-before-native-client.md)、
[Decision 005](../decisions/005-native-flutter-tmux-first-client.md)。

## 已核对的源码事实

源码基线：46f144004（v8.6.18）；用户反馈 APK 已稳定，不等于浏览器已验收。
以下路径均相对仓库根目录：

- `lib/mobile_gateway/service.py` 已有配对、项目、会话、消息和终端接口。
- `mobile/app/lib/transport/relay_socket_gateway_transport.dart` 连接 `/v2/phone`，
  在 `client_hello` 消息中提交授权，不依赖自定义 WebSocket HTTP 认证头；
  但原生 pingInterval 无法直接移植至浏览器。
- `lib/mobile_gateway/relay_service.py` 校验手机握手、授权和会话，已有服务端 heartbeat。
  浏览器 Origin 策略和反向代理兼容性仍需专门验证；CORS 不能代替 WebSocket 授权。
- `mobile/app/lib/transport/relay_crypto.dart` 使用 X25519、HKDF-SHA256、
  ChaCha20-Poly1305、主机指纹校验和有序加密信封；JS 实现必须与已有协议完全互通。
  不假设 Safari Web Crypto 原生覆盖全部算法；需评估成熟库与依赖、许可证、包体。
- `submit_agent_message` 已存在；客户端请求包含 namespace epoch 和 idempotency key。
  Web 必须继承同样的会话边界和发送语义，不能换成通用 ask 作业接口。
- Flutter 网络、缓存、附件、通知等直接使用 `dart:io`；未发现 `mobile/app/web/`。
  不能把现有 APK 当作可直接构建的 Web 工程。

历史 baseline/status 留有旧 checkout 路径、Cloudflare-first 和早期验收记录；
这些内容不能证明当前实现缺失，也不能作为浏览器发布验收。本轮不批量改写历史。

## 首版范围（建议，待冻结）

包含：导入配对信息、连接状态、已配对主机选择、项目列表、Agent 切换、
近期对话和按需历史、文本发送、Markdown/代码显示、断线提示和前台恢复。
首版以粘贴电脑生成的配对信息为必达路径，扫码作为可选增强；配对信息不得写入日志。
界面沿用 APK 的术语、状态和主要操作顺序，以手机布局为先。

暂缓：完整终端、文件上传下载、后台推送、系统常驻、语音、Provider 配置修改、
项目启停和其他破坏性管理、离线发送、多标签并行控制。
“简化”限制操作数量，不删减授权、错误状态和会话隔离。

## 推荐技术路线

建议在同仓库 `mobile/web/` 建独立 TypeScript + React + Vite 客户端；该目录目前
仅为拟议实现位置。本轮不创建脚手架或引入依赖，不改变根目录 npm 发布包。
复用 API、Relay 协议、测试向量及产品流程，不声称复用 Dart 界面源代码。
使用有明确边界的 transport、pairing、storage、conversation、UI 模块。

替代方案为 Flutter Web，优势是界面复用，代价是拆分原生依赖和验证完整插件图。
若后续要求与 APK 全功能同步，应重新比较该方案；本简化范围下优先轻量 Web。
React 和具体密码库是建议而非已批准选型，在连接验证前不锁定大规模实现。

访问路径：HTTPS 静态页面 → 浏览器 WSS + 端到端加密 → 现有 Relay → CCB 网关。
首版优先 Relay；公网 HTTPS 页面直连内网 HTTP 不纳入支持路径。
自托管静态页面可沿用协议，但自定义 Relay 域名及信任规则需独立验收。
部署域名、静态资源托管位置、TLS/CSP/connect-src 和 Origin 策略在上线前明确。
不为网页另开公网管理 API，不在本规划阶段修改生产 Relay。

## 工作包与验收顺序

| 阶段 | 产物 | 放行条件 |
| --- | --- | --- |
| P0 协议与浏览器验证 | 隔离最小页面、JS/Python/Dart 互通向量、握手与恢复报告 | 真 iPhone Safari 配对、读项目、发送一次文本并看到回复；Android Chrome 对照通过 |
| P1 只读界面 | 配对入口、项目/Agent、状态、历史和 Markdown | 主机/项目/Agent 隔离；长列表有界；内容安全渲染；断线与不兼容可解释 |
| P2 聊天闭环 | 文本发送、状态、实时变化、前台恢复 | 失败/未知结果明确；断线不自动重发输入；epoch 改变阻断旧操作；无持续全量轮询 |
| P3 PWA 与发布 | 安装元信息、静态缓存、独立版本、发布回滚说明 | 新资源完整后切换；草稿不被强制刷新清除；仅缓存静态资源；撤销设备后拒绝访问 |

P0 必须先解决 Safari 的密码库兼容、64 位序号精度、握手字节一致性、
授权续用和撤销、浏览器不可主动发协议级 ping 的恢复策略。
握手后请求关联、流控/事件恢复和响应上限也应覆盖，不能只验证 WebSocket 能打开。
不得降低序号范围或关闭指纹/密文验证以让浏览器测试通过。

测试层次：协议向量与畸形输入 → 本地模拟网关/Relay 集成 → 桌面浏览器自动化 →
真 iPhone Safari 与 Android Chrome。桌面 WebKit 自动化不替代真 iPhone 验收。
真实浏览器矩阵包含中文输入、锁屏/切后台、网络切换、重复点击发送、
多标签连接冲突、设备撤销、主机离线、旧网关缺少能力和长对话。
生产发布前还应复核安全 Markdown、URL 处理、依赖许可证和已知漏洞。

## 凭据、缓存与升级

浏览器作为单独可撤销设备，不复制 APK 内部凭据。
默认会话内持有敏感材料；“记住此设备”需要显式选择，并验证浏览器存储与清除语义。
浏览器存储不是原生 Keychain/Keystore，不能以加密 IndexedDB 宣称完全等价保护。
禁止第三方分析脚本接触会话内容；密文中转不消除前端代码供应链信任。
Service Worker 不缓存配对数据、会话响应或凭据，不排队离线发送指令。
首版后台不保持工作承诺；电脑任务继续执行，前台恢复同步状态。

Web、APK、桌面独立版本和发布门禁；Web 有变更才发布，前端显示自身构建版本。
保留旧哈希静态资源供已打开页面加载，提示用户确认刷新，兼容接口按能力协商。
回滚只切回已验证的静态版本；不撤销用户配对、不回滚 Provider 配置或电脑运行时。
APK 的独立更新入口迁移另行落地，不把该任务隐含纳入 Web 实现。

## 执行准备度与下一步

当前已完成源码可行性分析与建议计划，未运行 Web 构建、浏览器互通或真机测试。
计划尚未达到正式实现就绪；下一步先冻结首版是否排除终端/附件，再执行隔离 P0。
需要落实：真 iPhone 测试途径、支持的最低 iOS/Safari 版本、前端技术选型，
以及后续上线域名/托管责任。无真机时可推进向量和自动化，但不能宣称 iPhone 验收完成。
不在 P0 前承诺完整工期；首期风险集中在浏览器协议互通和恢复，而非普通页面。
