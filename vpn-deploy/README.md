# 通用部署配套文件

本目录与同级 `../VPN-deploy.md` 一起分发。文件只有占位符，不包含任何真实节点、密码或用户环境依赖。

## 使用顺序

1. 按主指南盘点/准备资源，检查授权，验证住宅代理和机场能力。
2. 复制 `inputs.example.json` 到仓库外私有目录，填入已知参数；证书生成后计算指纹。空入口密码/控制器密钥可自动生成，住宅账号则必须由供应商提供。
3. 使用 Python 3 标准库运行 `render.py --inputs ... --output ...`，输出目录必须在仓库外且不存在。工具只生成文件，不安装、不联网、不 SSH、不申请证书、不验证套餐，也不启动服务。
4. 将输出 `resolved-inputs.json` 作为本次凭据记录，后续渲染复用它。原示例中空密码每次都会随机生成，不应拿原示例再次渲染后直接覆盖正式服务。
5. 按本次实际项目/API/配置版本调整安装路径并校验。JSON 同时是合法 YAML；`cn-mixin.yaml` 用 JSON 序列化以保留类型和特殊字符，不需要增加 YAML 依赖。

## 文件职责

| 文件 | 内容 |
| --- | --- |
| `inputs.example.json` | 非敏感字段示例；连接参数留空、端口为示例默认值，不可直接用于部署 |
| `render.py` | 校验必填项/类型/TLS分支，安全渲染并生成受限私有结果，拒绝覆盖已有输出 |
| `cn-mixin.template.json` | 国内 `override`，含认证 HY2、首跳、美国出口、DNS、规则和仅限公共入口的内部访问拒绝 |
| `us-sing-box.template.json` | 美国最小 HY2 → SOCKS5，不含备用 DIRECT，也不对客户端开放住宅凭据 |
| `us-sing-box.service` | 新部署 systemd 模板，按实际 binary/config 路径适配；官方安装已有单元就复用，不创建重复实例 |
| `client-script.js` | 完整全局增强模板，未渲染前不可直接启用；AI域名和普通Google边界的单一维护位置 |
| `chain_health.py`、`chain-health.example.json` | 国内机场质量巡检及非敏感参数示例；独立探测完整 HY2/住宅链路，不自动直连兜底 |
| `clash-chain-health.service`、`clash-chain-health.timer` | 三分钟巡检单元模板，路径和生产服务名须按现场适配 |
| `probe_server.py`、`probe-server.service`、`probe-nginx.*.conf` | 美国自有 HTTPS 健康及定速流探测端点，不依赖公共 drip 服务 |
| `server-tun.md` | 需要主机透明接管时的前提、规则所有权和验收分支 |
| `udp-compat.md` | 真实住宅 UDP 失败时的条件诊断和源码兼容要求，不绑定旧版本或旧二进制 |
| `tests/` | 离线参数/模板回归测试，不证明公网或任何未安装版本的功能 |

默认路径约定：国内管理项目 `/opt/clash-for-linux`、美国配置 `/etc/sing-box/config.json`，美国核心 `/usr/local/bin/sing-box`。这些是部署时创建或适配的目标，不要求新用户预先已有。公信美国证书默认路径按 SNI 生成，自签分支默认 `/etc/sing-box/tls/`；证书必须另外真实生成。

国内控制器密钥仅写入项目原生状态源，来源为私有 `resolved-inputs.json`，模板不保留第二份 secret。原生工具改密后同步这份私有记录。新机通过项目关闭 TUN，模板不写死 `tun.enable`；已有机器先读取其状态。

`RES_UDP_SUPPORTED=false` 时生成美国 `network: tcp`；只有真实 UDP 往返确认后设 true，模板才保留 SOCKS5 默认的 TCP+UDP，不设置会禁用 TCP 的 `network: udp`。自签美国 TLS 必须配置其指纹，公信分支保持正常校验。

## 私有资料边界

- 分发目录只保留模板和测试。渲染结果、输入、日志和私钥放在仓库外；禁止上传到公开仓库或第三方转换网站。
- 工具在 POSIX 上创建目录700、文件600。Windows 的 chmod 不等于完整 ACL 防护，AI 必须额外设置并验证只允许目标用户/必要管理员访问；不把权限测试结果推广到所有平台。
- 生成器不读取用户现有 `.env`、Clash 目录或 SSH 凭据，也不自动覆盖现有部署。未知字段保留在私有解析结果但不会自动变为安装行为。

## 国内首跳质量巡检

`chain_health.py` 替代仅在 gstatic 延迟测试失败后遍历换节点的旧巡检。它默认只报告，只有 `--apply` 才修改生产选择。示例 IP 和 `probe.example.com` 均须在部署前替换；配置不包含机场、HY2 或住宅密码，脚本从已有受限运行配置与 provider 缓存读取凭据。服务器需已有 Python 3、PyYAML、curl 和 Mihomo，不会安装依赖。

- 每轮最多 8 个候选、并行 3 个临时内核：当前节点、近期验证优胜者和轮转候选；协议交错避免订阅前半段垄断探测。
- 独立实例仅监听回环随机端口，不启用 TUN、公网入口或控制器；使用现场已验证的路由 mark/interface 绕过生产 TUN。每个候选仍经美国 HY2 和原住宅出站，不测试机场单跳后就判定可用。
- 短测包含美国自有端点和 Anthropic 两次 HTTPS，评分综合成功率、中位耗时和抖动。只有短测全通过、且服务端持续发送测试通过的节点才可上任；近期持续流失败的节点会降权。流测试需 HTTP 200、12 字节完整响应、实际耗时至少 4.5 秒，不能用 curl 限速代替。所有候选失败时保持当前选择并记录事件，不宣称故障已恢复。
- 健康状态下需至少改善 25% 且 80ms、相同候选连续两轮胜出，切换后 10 分钟冷却。当前硬故障可跳过冷却，但候选仍需额外复核；正常周期不会重置生产连接。
- 切换会中断原 HY2 transport 上的既有流。脚本仅删除匹配 `Inner/udp + 美国入口 IP/端口 + 首跳组 + 旧节点` 的底层 tracker，不删除全部业务连接；验证新 tracker 的节点和完整链路，不只确认组名。失败恢复原选择；在 PUT 前原子写入受限事务记录，控制器响应丢失或进程终止后由下轮恢复。
- 此精确重建机制已针对 Mihomo v1.19.23 源码和现场连接结构核对。其他版本需重新验收；匹配不到正确 tracker 时拒绝切换，不以全局 reload/restart 代替。人工停止服务或选择 REJECT/DIRECT 时不擅自恢复。
- 运行预算 145 秒，systemd 硬限 170 秒、按 cgroup 清理子进程，文件锁防重入；`OnUnitActiveSec=3min` 为开始到开始的周期，不是执行完成后再等待三分钟。

部署前备份旧脚本和同名单元、停止旧 timer 并等待旧 oneshot 结束。把脚本安装到 `/opt/clash-for-linux/scripts/server-chain-health.py`，私有 JSON 配置安装到 `/etc/clash-chain-health.json`（600）。先校验真实内核、路由标记和出口地址，再不带 `--apply` 试跑；不能把 `routing_mark=6666` 当作所有服务器都成立的默认路由事实。

```sh
python3 /opt/clash-for-linux/scripts/server-chain-health.py \
  --config /etc/clash-chain-health.json
# 核对试跑结果后，通过已适配的 service 启用 --apply。
systemctl daemon-reload
systemctl start clash-chain-health.service
systemctl enable --now clash-chain-health.timer
systemctl list-timers clash-chain-health.timer
journalctl -u clash-chain-health.service -n 40 --no-pager
```

`/var/lib/clash-chain-health/state.json` 保存历史、轮转和冷却，`transaction.json` 仅在未完成的切换期间存在；目录 700，数据文件 600。临时含凭据配置在探测结束后删除。脚本不调用计费模型 API，也不能替代上线时真实 Claude 长会话验收；短时探测通过不承诺未来网络稳定。

### 自有美国测试端点

`probe_server.py` 提供固定的健康检查和服务端定速响应，不调用模型 API：

- `/chain-probe/health` 返回 HTTP 204。
- `/chain-probe/stream` 固定返回 12 字节，首字节立即发送、最后一个字节约第 6 秒发送。查询参数不能修改长度、时长或转发目标。
- 后端仅监听 `127.0.0.1:18089`，最多 8 个并发连接；通过 nginx 既有 TLS 证书对外提供 HTTPS，禁用缓冲、gzip 和缓存。不是任意目标的开放代理，也不完全模拟 Claude 的应用协议。

安装脚本到 `/opt/chain-probe/probe_server.py`（644，父目录 755），安装 `probe-server.service` 到 `/etc/systemd/system/chain-probe.service`。`probe-nginx.location.conf` 安装到 `/etc/nginx/snippets/chain-probe.location.conf` 并在 TLS 虚拟主机内引用；若该域名尚无 TCP 443 虚拟主机，可适配 `probe-nginx.server.conf`。先备份，避免覆盖已有站点；TCP 443 与 HY2 的 UDP 443 不冲突。替换示例域名和证书路径后执行：

```sh
nginx -t
systemd-analyze verify /etc/systemd/system/chain-probe.service
systemctl daemon-reload
systemctl enable --now chain-probe.service
systemctl reload nginx
curl --max-time 12 -w '\n%{http_code} %{size_download} %{time_total}\n' \
  https://probe.example.com/chain-probe/stream
```

巡检配置中的 `probe.example.com` 也须替换为真实美国域名。保留域名及证书校验，不用裸 IP 或 `-k` 绕过 TLS；通过机场、美国 HY2 和住宅出口访问该美国端点，避免只测机场单跳就误判 Claude 链路。端点迁移后应重新采集历史分数，避免与旧目标耗时混算。

## 离线回归

在文档所在目录执行，无需安装 Python 第三方包：

```sh
python3 -m unittest discover -s vpn-deploy/tests -p 'test_*.py'
node vpn-deploy/tests/test_client.js
node --check vpn-deploy/client-script.js
```

部署时仍须分别运行实际最新稳定 Mihomo/sing-box 的配置校验，做独立客户端的分层 TCP/UDP、证书、路由、私网拒绝及持久化测试。编译或解析通过不能替代功能验收。
