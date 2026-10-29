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

## 离线回归

在文档所在目录执行，无需安装 Python 第三方包：

```sh
python3 -m unittest discover -s vpn-deploy/tests -p 'test_*.py'
node vpn-deploy/tests/test_client.js
node --check vpn-deploy/client-script.js
```

部署时仍须分别运行实际最新稳定 Mihomo/sing-box 的配置校验，做独立客户端的分层 TCP/UDP、证书、路由、私网拒绝及持久化测试。编译或解析通过不能替代功能验收。
