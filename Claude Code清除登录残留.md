# Claude Code 清除登录残留

> 根据文末两篇清理文章整理，并使用 Claude Code 官方文档核对关键路径和卸载方式；后续补充小小东长文与用户下载的 Lark 逆向探查文档，新增逆向结论未独立复核源码。核对日期：2026\-10\-10。适用于 macOS、Linux、Windows；WSL 按 Linux 操作。
> 
> 
> 
> 本文提供操作说明。生成本文不会执行卸载或删除你的本机数据。
> 
> 



## 1\. 先确定清理目标



不同问题需要不同程度的清理，不必每次都完整卸载。



|目标|建议操作|影响|
|---|---|---|
|修复登录异常、切换正常可用的账号|暂停后台任务 → 退出登录 → 检查凭据来源 → 重新登录|通常可以保留配置和历史|
|排查配置冲突、恢复初始设置|备份 → 退出登录 → 重置用户配置|MCP、插件、权限、历史等需要重新配置或恢复|
|从 npm 切换到原生安装|备份 → 卸载 npm 包 → 安装原生版 → 检查命令路径|通常无需删除用户配置|
|完整卸载|备份 → 停止相关程序 → 退出登录 → 卸载程序 → 清理配置及凭据 → 验证|删除范围内的本地数据会丢失|



**清理本地文件不等于注销账号、取消订阅或撤销服务端的全部令牌。** 如有这些需求，应在对应账户后台单独处理。



第二篇文章涉及封号、设备标识和账号关联。本文只采用其中可以用于日常维护的检查项；本地清理不能解除服务端封禁，也不能保证账号不会触发风控。



## 2\. 清理前：确认安装方式、配置位置与备份



### 2\.1 确认命令来自哪里



macOS / Linux / WSL：



```Bash
type -a claude
command -v claude
claude --version

# 使用 npm 时检查
npm list -g --depth=0

# macOS 安装了 Homebrew 时检查
brew list --cask
```



Windows PowerShell：



```PowerShell
Get-Command claude -All -ErrorAction SilentlyContinue |
    Select-Object CommandType, Name, Source, Definition
claude --version

# 根据本机实际安装的包管理器执行
npm list -g --depth=0
winget list --id Anthropic.ClaudeCode --exact
```



判断时结合命令路径和包管理器记录：



|安装方式|典型线索|
|---|---|
|原生安装|macOS / Linux 启动文件常在 `~/.local/bin/claude`；版本文件在 `~/.local/share/claude`|
|Windows 原生安装|启动文件常在 `%USERPROFILE%\.local\bin\claude.exe`；版本文件在 `%USERPROFILE%\.local\share\claude`|
|npm|当前 Node 环境的全局包列表包含 `@anthropic-ai/claude-code`|
|Homebrew|cask 列表包含 `claude-code` 或 `claude-code@latest`|
|WinGet|安装列表包含 `Anthropic.ClaudeCode`|



`/usr/local/bin/claude` 等路径不能单独证明安装方式。如果存在多个安装来源，应逐一处理；不要直接删除所有搜索到的同名文件。



### 2\.2 检查自定义配置目录



macOS / Linux / WSL：



```Bash
printenv CLAUDE_CONFIG_DIR
```



Windows PowerShell：



```PowerShell
$env:CLAUDE_CONFIG_DIR
```



没有输出通常表示未设置此变量。设置了 `CLAUDE_CONFIG_DIR` 时，配置、会话、插件和凭据文件可能位于自定义目录；macOS 钥匙串条目也可能与该目录关联。



下面的配置备份与清理示例针对默认位置。使用自定义目录时，先确认它的绝对路径和内容，再单独备份和处理；不要把未经检查的环境变量直接传给递归删除命令。



### 2\.3 备份配置和历史



默认用户数据主要位于 `~/.claude/` 与 `~/.claude.json`。会话历史通常在 `~/.claude/projects/`；用户设置常在 `~/.claude/settings.json`。



macOS / Linux / WSL：



```Bash
backup_dir="$HOME/claude-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -m 700 "$backup_dir"

if [ -f "$HOME/.claude.json" ]; then
  cp -p "$HOME/.claude.json" "$backup_dir/"
fi
if [ -d "$HOME/.claude" ]; then
  cp -Rp "$HOME/.claude" "$backup_dir/"
fi

printf '备份目录：%s\n' "$backup_dir"
```



Windows PowerShell：



```PowerShell
$backupDir = Join-Path $env:USERPROFILE ("claude-backup-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Path $backupDir -ErrorAction Stop | Out-Null

foreach ($name in @(".claude.json", ".claude")) {
    $sourcePath = Join-Path $env:USERPROFILE $name
    if (Test-Path -LiteralPath $sourcePath) {
        Copy-Item -LiteralPath $sourcePath -Destination $backupDir -Recurse -ErrorAction Stop
    }
}

Write-Output "备份目录：$backupDir"
```



备份可能包含凭据、代码片段和对话内容，应保存在私人目录中，不要上传公共仓库。清理原目录后，备份仍然保留这些数据；它不属于“已经删除”的范围。



## 3\. 先停止后台调用，再退出登录



### 3\.1 停止会话、插件和自动化任务



1. 保存正在进行的任务，退出 Claude Code 会话。

2. 关闭使用 Claude Code 的 IDE 插件及相关桌面应用。

3. 暂停调用 `claude` 的计划任务、服务或自动化脚本，避免清理时重新生成文件。

    

检查入口：



|平台|检查方式|
|---|---|
|macOS|`crontab -l`、`~/Library/LaunchAgents/`，以及自己配置的 launchd 任务|
|Linux / WSL|`crontab -l`、`systemctl --user list-timers --all`，以及相关用户服务|
|Windows|任务计划程序，以及自行配置的后台服务或脚本|



仅停止已确认调用 Claude Code 的任务。第二篇文章使用的 `pkill -9 -f "claude"` 匹配范围较大，也会强制终止进程，本文不将它作为默认操作。



### 3\.2 退出登录



在仍然安装着 Claude Code 时执行：



```Bash
claude auth logout
```



如果当前版本不支持该子命令，可以进入 Claude Code 交互界面执行：



```Plain Text
/logout
```



退出登录处理的是认证状态，不代表删除用户设置、MCP 配置、历史记录或程序本身。程序已经卸载或无法启动时，可继续检查下面的凭据文件及钥匙串。



## 4\. 按安装方式卸载程序



只执行与实际安装来源匹配的分支；多种安装并存时分别卸载。



### 4\.1 原生安装：macOS / Linux / WSL



先核对第 2 节中的路径与实际安装一致，再执行：



```Bash
rm -f "$HOME/.local/bin/claude"
rm -rf "$HOME/.local/share/claude"
```



以上移除启动文件和原生版本目录，用户配置留到第 5 节处理。



### 4\.2 原生安装：Windows PowerShell



以下示例先检查目标位于当前用户目录内，再删除默认安装文件：



```PowerShell
$profileRoot = [System.IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\')
$profilePrefix = $profileRoot + '\'
$targets = @(
    (Join-Path $profileRoot ".local\bin\claude.exe"),
    (Join-Path $profileRoot ".local\share\claude")
)

foreach ($target in $targets) {
    $fullPath = [System.IO.Path]::GetFullPath($target)
    if (-not $fullPath.StartsWith($profilePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "目标超出用户目录：$fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        $item = Get-Item -LiteralPath $fullPath -Force
        if ($item.PSIsContainer) {
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw "目标为目录链接，请先核对实际位置：$fullPath"
            }
            Remove-Item -LiteralPath $fullPath -Recurse -Force -ErrorAction Stop
        } else {
            Remove-Item -LiteralPath $fullPath -Force -ErrorAction Stop
        }
    }
}
```



### 4\.3 npm 全局安装



各平台均可执行：



```Bash
npm uninstall -g @anthropic-ai/claude-code
```



使用 nvm 等 Node 版本管理器时，全局包可能按 Node 环境分别安装。先用 `nvm list` 列出环境，再切换到实际安装过该包的版本，重复检查和卸载。



无需为了卸载 Claude Code 执行 `npm cache clean --force`；npm 缓存由多个包共用。



### 4\.4 Homebrew



根据已安装的 cask 选择命令：



```Bash
brew uninstall --cask claude-code

# 安装的是 @latest cask 时，改用下面这一条
# brew uninstall --cask claude-code@latest
```



无需额外执行针对 formula 的 `brew unlink`。



### 4\.5 WinGet



```PowerShell
winget uninstall --id Anthropic.ClaudeCode --exact
```



如果来自其他系统包管理器，使用对应包管理器卸载。不要通过手动删除受包管理器管理的文件替代卸载流程。



## 5\. 重置用户配置与本地历史



**本节会清除默认目录中的设置、插件、MCP 配置、权限记录、会话历史及可能存在的凭据文件。请先检查备份。** 只更换安装方式、希望保留设置时，可以跳过本节。



### 5\.1 macOS / Linux / WSL



```Bash
rm -rf "$HOME/.claude"
rm -f "$HOME/.claude.json"
```



### 5\.2 Windows PowerShell



```PowerShell
$profileRoot = [System.IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\')
$profilePrefix = $profileRoot + '\'
$targets = @(
    (Join-Path $profileRoot ".claude"),
    (Join-Path $profileRoot ".claude.json")
)

foreach ($target in $targets) {
    $fullPath = [System.IO.Path]::GetFullPath($target)
    if (-not $fullPath.StartsWith($profilePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "目标超出用户目录：$fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        $item = Get-Item -LiteralPath $fullPath -Force
        if ($item.PSIsContainer) {
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw "目标为目录链接，请先核对实际位置：$fullPath"
            }
            Remove-Item -LiteralPath $fullPath -Recurse -Force -ErrorAction Stop
        } else {
            Remove-Item -LiteralPath $fullPath -Force -ErrorAction Stop
        }
    }
}
```



### 5\.3 文章提及的其他目录：先核实归属



第一篇文章还列出了以下可能位置，但它们不是本次核对的官方卸载文档所列必删目录：



|平台|文章提到的路径|
|---|---|
|macOS / Linux|`~/.claude-code/`、`~/.config/claude-code/`、`~/.cache/claude-code/`|
|macOS|`~/Library/Application Support/claude-code/`、`~/Library/Caches/claude-code/`|
|Windows|`%APPDATA%\claude-code`、`%LOCALAPPDATA%\claude-code`|



这些目录是否存在、属于哪个版本或第三方封装，需要本机确认。不要根据名称批量删除 `*claude*` 或 `com.anthropic.*`；这可能影响 Claude Desktop、浏览器集成或其他应用。



## 6\. 清理认证残留与环境变量



### 6\.1 凭据保存在哪里



|平台|官方文档说明的存储位置|
|---|---|
|macOS|通常在系统钥匙串；钥匙串写入失败时可能回退到 `~/.claude/.credentials.json`|
|Linux / WSL|`~/.claude/.credentials.json`|
|Windows|`%USERPROFILE%\.claude\.credentials.json`|



使用 `CLAUDE_CONFIG_DIR` 时，凭据文件改到自定义目录中。删除默认 `~/.claude/` 不能证明自定义目录里的凭据已删除。



### 6\.2 macOS 钥匙串



先退出登录。如果仍存在相关凭据，可以在“钥匙串访问”中搜索并核对 `Claude Code-credentials`。



第二篇文章给出了下面的命令，适用于确认服务名完全匹配的条目：



```Bash
security delete-generic-password -s "Claude Code-credentials"
```



出现“找不到条目”可能意味着凭据已移除，也可能是服务名不同。使用自定义配置目录时，应检查实际钥匙串条目，不要假设上面的固定名称涵盖所有情况。



文章同时提到 `Claude Safe Storage`，它可能关联桌面应用；仅清理 Claude Code CLI 时，不把它作为默认删除对象。



### 6\.3 检查其他凭据来源



即使已经退出登录，环境变量、设置文件或脚本仍可能提供认证信息。检查以下变量是否设置，以及它们来自哪里：



- `ANTHROPIC_API_KEY`

- `ANTHROPIC_AUTH_TOKEN`

- `CLAUDE_CODE_OAUTH_TOKEN`

- `ANTHROPIC_BASE_URL`：不是凭据，但可能将请求指向旧网关。

- `CLAUDE_CONFIG_DIR`：决定实际配置目录。

    

另外检查用户和项目设置中的 `env`、`apiKeyHelper`，以及后台任务、CI 和 IDE 启动环境。使用云服务或企业认证时，还需核对对应的配置来源。



仅清除当前 macOS / Linux / WSL 终端中的三个常见凭据变量：



```Bash
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN
```



仅清除当前 PowerShell 进程中的对应变量：



```PowerShell
Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:ANTHROPIC_AUTH_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:CLAUDE_CODE_OAUTH_TOKEN -ErrorAction SilentlyContinue
```



这些命令不会删除持久化定义。若需要长期清除，应编辑相关 shell 配置、Windows 用户环境变量或任务配置，然后打开新终端。不要把密钥内容贴到日志或公开文档中。



## 7\. 项目配置、PATH 与其他客户端



### 7\.1 项目配置按需处理



用户配置清理不会自动移除每个仓库里的配置：



|文件或目录|内容|建议|
|---|---|---|
|`<项目>/.claude/`|项目设置、个人设置、技能、子代理等|查看后按需清理|
|`<项目>/.mcp.json`|项目 MCP 配置|可能属于团队共享配置，先确认|
|`<项目>/CLAUDE.md`|项目约定和说明|通常保留|



不要递归扫描磁盘并批量删除所有 `.claude` 目录。需要重置某个项目时，先核对项目绝对路径、文件内容和 Git 状态，再只处理该项目。源码、任务交接文档和 `CLAUDE.md` 不属于默认清理范围。



### 7\.2 PATH、别名与命令缓存



检查 `~/.zshrc`、`~/.bashrc`、`~/.bash_profile` 或 Windows 用户 PATH 中的旧配置。



- 可以移除已确认只用于旧 Claude 安装的路径或别名。

- 不要仅因 Claude Code 使用过它就删除 `~/.local/bin`、npm 全局 bin、Homebrew bin 等共享 PATH 条目。

- 打开新终端；Bash 也可以用 `hash -r`，Zsh 可以用 `rehash` 刷新命令缓存。

    

### 7\.3 IDE、桌面端和浏览器



官方文档指出，VS Code 扩展、JetBrains 插件和 Desktop 应用也会写入 `~/.claude/`。若它们继续运行，清理过的目录可能重新出现；完整移除时也应处理这些入口。



第二篇文章还提到浏览器 Native Messaging、macOS plist、缓存及最近文档记录。这些属于更广的客户端清理范围。仅清理 CLI 时跳过；如果同时卸载桌面应用或浏览器扩展，应先确认每个文件的归属。



清除浏览器网站数据会影响网页登录；删除 CLI 配置也不等于清除浏览器数据。手机应用、支付方式和 App Store 订阅不属于本教程的清理步骤。



## 8\. 验证清理结果



### 8\.1 完整卸载后的验证



macOS / Linux / WSL：



```Bash
type -a claude
command -v claude

for path in "$HOME/.local/bin/claude" "$HOME/.local/share/claude" "$HOME/.claude" "$HOME/.claude.json"; do
  if [ -e "$path" ] || [ -L "$path" ]; then
    printf '仍存在：%s\n' "$path"
  fi
done
```



Windows PowerShell：



```PowerShell
Get-Command claude -All -ErrorAction SilentlyContinue

@(
    "$env:USERPROFILE\.local\bin\claude.exe",
    "$env:USERPROFILE\.local\share\claude",
    "$env:USERPROFILE\.claude",
    "$env:USERPROFILE\.claude.json"
) | ForEach-Object {
    [PSCustomObject]@{ Path = $_; Exists = (Test-Path -LiteralPath $_) }
}
```



预期结果：



- 当前 shell 找不到 `claude`，也不存在相关别名或函数。

- 选择清除的文件与目录已不存在。

- 对应包管理器不再列出已卸载的 Claude Code 包。

- 已确认的后台任务停止，相关凭据与持久化变量按目标处理完毕。

    

自定义配置目录、项目配置、其他 Node 环境和钥匙串需分别验证。上述结果只能证明已检查的范围，不等于磁盘、云备份或服务端“没有任何痕迹”。



### 8\.2 只重置配置或准备重装时



保留程序的配置重置场景中，`claude --version` 仍能运行是正常的。需要重装时，按官方安装文档选择一种安装方式，再验证：



```Bash
claude --version
claude doctor
claude auth login
claude auth status
```



若当前版本不支持相应 `auth` 子命令，可进入交互界面使用 `/login` 和 `/status`。核对实际账号、组织和认证方式；确认调用正常后，再恢复后台任务。



不要直接覆盖恢复整份旧配置来验证“全新环境”。先测试默认环境，再逐项恢复必要的 MCP、技能和设置，以便定位原来的问题。



## 9\. 历史会话与原文结论的适用范围



第二篇文章建议在账号变化后，用 Markdown 任务交接单接续工作。这也是避免将旧会话数据带入新环境的实用方法：



```Markdown
# 任务交接

## 目标
当前需要完成的功能或问题。

## 已完成
已修改的文件、实现结果和验证情况。

## 待完成
剩余步骤、已知错误和必要约束。

## 关键文件
列出继续工作所需的文件路径。
```



导出时去掉令牌、密钥和无关个人信息。删除 `~/.claude/projects/` 后，原会话通常无法再从该目录恢复；需要记录时应提前备份或导出。



对两篇文章中的关键说法，本文作如下处理：



|原文说法或做法|本教程的处理|
|---|---|
|使用 `claude uninstall` 卸载原生版|本次核对的官方卸载文档使用手动移除启动文件和版本目录，本文采用官方步骤|
|Windows 原生程序位于 `%LOCALAPPDATA%\Programs\claude\claude.exe`|不作为默认路径；官方示例为 `%USERPROFILE%\.local\bin\claude.exe`|
|改写 `machineID`、`userID` 可以避免账号关联|属于文章作者的分析，未取得官方证据；本文不提供手动伪造标识的步骤|
|恢复旧会话必然导致新账号被封|缺少可核验的官方依据，不写成确定结论；按数据隔离需求选择新会话和任务交接|
|使用 `com.anthropic.*` 通配符删除系统文件|可能影响多个客户端，改为核对具体文件归属后处理|
|清理后重装仍报错就是配置残留|还可能涉及网络、权限、认证来源、服务端账号状态或组织策略，应按错误信息排查|



### 9.1 新来源补充：清理“痕迹”的边界与服务器资料

补充日期：2026-10-10。[小小东的 X 长文](https://x.com/xiaoxiaodong/status/2108873683494478141?s=46) 把“封过号后让 Codex 清理 Claude 痕迹”列在“可做可不做”的细节中，没有提供新的文件路径、设备标识或清理命令。本文据此补充适用范围，不增加未经验证的删除项：

1. **按维护目标清理。** 登录异常先按第 3、6、8 节处理认证和验证；需要完整卸载时再执行相应分支。无需仅因作者的账号经历就默认删除全部配置、历史或重装系统。
2. **由其他工具清理也要明确范围。** 若委托 Codex 或脚本执行，先要求列出目标绝对路径、归属、备份位置和预期影响，保存任务并停止相关 Claude 会话后再处理；不能用“清理所有痕迹”作为批量删除同名文件的依据。
3. **服务器资料不属于默认 CLI 清理范围。** 作者链接的 [xiaoxiaodong-vps README](https://github.com/nevertoday/xiaoxiaodong-vps) 说明，部署会在桌面生成 `小小东VPS-你的名字-登录信息.md`，包含订阅地址、管理面板凭据和 API Key，还使用本机 SSH 密钥。这些资料与 `~/.claude/`、钥匙串登录凭据分别管理。仅退出 Claude 登录时通常保留；退役服务器或处理泄露时，应另行确认资料位置及远端撤销、轮换需求。
4. **本地删除与远端撤销分开验收。** 删除上述登录信息文件，不会自动撤销订阅链接、面板密码或 API Key；删除 SSH 私钥，也不会自动删除服务器 `authorized_keys` 中的公钥。处理时先确认远端访问和恢复方式，避免失去服务器访问权限。备份、副本及其他工具的任务记录也需按实际目标检查，不能声称已彻底无痕。

长文的“账号全部存活”是作者自述，不能证明清理是原因，也不改变第 9 节对设备标识、旧会话关联和服务端封禁的证据限制。配套仓库包含 CLIProxyAPI，网络部署和服务端清理属于单独任务，本文不执行仓库脚本。

### 9.2 Lark 逆向探查补充：持久标识与恢复检查

本节依据用户下载的 [Claude Code 封号机制逆向探查.md](./Claude%20Code%20封号机制逆向探查.md)；[Lark 原文](https://bytedance.larkoffice.com/docx/E2JudVzf7oCNfhxyxaQcZIW1n0g) 未直接访问成功，但本地正文已读。该文未指定源码提交或对应客户端版本，以下是作者报告的行为，不作为当前版本已验证的结论。

**同级 `.claude.json` 的作用得到补充。** 作者称该文件保存随机生成的持久设备标识（32 字节、64 字符十六进制），跨会话及账号保留；文中混用 `deviceID` 与 `deviceId`，应以实际结构为准。它与 `~/.claude/` 是两个目标，第 2、5、8 节已包含两者，无需新增一套删除命令。只退出登录、删除 `.credentials.json` 或卸载程序，不能据此证明 `.claude.json` 已清除；使用自定义目录时继续按第 2.2 节核对实际位置。

|新增检查项|如何纳入本教程|
|---|---|
|选择性保留配置|原文建议单独备份 `~/.claude/skills/`、`settings.json`、`CLAUDE.md`、`rules/`。可在私人完整备份之外列出恢复清单；恢复前检查 `env`、凭据、网关地址及脚本路径。这些目录并不涵盖所有有用配置，仍按第 2.3 节备份实际需要的数据。|
|历史、缓存与遥测队列|原文称 `.claude/` 含这些持久数据，但未给出完整子目录清单。默认目录重置已覆盖其中实际存在的数据，不补造队列路径，也不删除名称相似的共享缓存。|
|旧身份与属性来源|作者报告 OAuth 邮箱、Git 邮箱回退、组织/账号 UUID、自定义 API 主机等属性；按第 6.3 节检查认证来源、用户和项目设置，另核对全局与仓库 Git 配置。不要将 Git 提交身份当成登录凭据删除。|
|CI 与远程环境|作者列出 GitHub Actions 的 actor/repository/owner 及对应 ID 元数据。本机清理不覆盖 CI secrets、环境、日志、缓存或远程主机；确需处理时逐项确认范围，不批量删除团队资料。|
|遥测开关残留|检查进程环境、shell/Windows 持久变量、用户和项目 `settings.json` 中的 `DISABLE_TELEMETRY`、`DISABLE_ERROR_REPORTING`、`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`。它们不是登录凭据；仅在排查功能问题、且与隐私和组织要求一致时调整，不为“清干净”无条件移除。|
|企业托管设置|原文报告 Remote Managed Settings 和 Policy Limits；若恢复默认环境后仍有限制，应核对组织管理配置。本地删除不能解除远端策略，不能据此判断清理失败。|

**备份与恢复也属于验收范围。** 备份整份 `.claude.json` 会保留其中已有数据；直接恢复它可能重新引入旧状态。先验证默认环境，再恢复必要设置；不要手动改写标识、伪造请求指纹或恢复不需要的认证状态。日志和任务交接文件中也不应公开设备标识、邮箱、令牌或完整配置。

逆向文档称清理后等同“换了一台新设备”，且不清理会使“风险分直接拉满”；这超出了客户端持久化信息能够证明的范围。删除文件不会撤销服务端历史、账号封禁或所有关联。该文又称关闭遥测可能影响 GrowthBook、Fast Mode、长上下文模型和 Remote Control，但“关闭本身触发风控”仍是推测，具体副作用需按版本核对。小小东称清理可选，与逆向文档“必须清理”存在分歧；本教程继续按登录修复、配置重置或卸载目标选择范围。

## 10\. 完成检查表



* [ ] 已明确是登录修复、配置重置、安装迁移还是完整卸载。

* [ ] 已确认安装来源，以及是否使用自定义配置目录。

* [ ] 已备份需要保留的设置、历史和任务进度。

* [ ] 已停止相关会话、插件及后台任务。

* [ ] 已退出登录，核对凭据文件、钥匙串及环境变量。

* [ ] 已按安装方式卸载，或明确保留程序。

* [ ] 已按目标处理用户和项目配置。

* [ ] 重置用户数据时已分别核对 `.claude/` 与同级 `.claude.json`；选择性恢复未带回不需要的认证、网关或旧状态。

* [ ] 已打开新终端并验证命令路径、包管理器及残留位置。

* [ ] 若另有自建服务器登录资料，已明确保留还是退役；本地文件清理与远端凭据撤销分别核对。

* [ ] 若重装，已验证默认环境和认证状态，再恢复必要设置与自动化任务。

    

## 参考资料



1. [彻底卸载指南：Claude Code 和 OpenCode 完全清理教程 — CSDN / AI Agent 技术社区](https://agent.csdn.net/6a323bde662f9a54cb8084b9.html)。本文仅整理其中与 Claude Code 有关的内容。

2. [Claude 封号后，我们彻底清干净了这几处残留 — SegmentFault 思否](https://segmentfault.com/a/1190000048329091)。本文参考其凭据、后台任务和历史数据检查项，对账号风控相关结论保留证据限制。

3. [Claude Code 官方：安装与卸载](https://code.claude.com/docs/en/setup)。用于核对卸载方式、默认安装路径和配置清理范围。

4. [Claude Code 官方：认证](https://code.claude.com/docs/en/authentication)。用于核对凭据存储与认证来源。

5. [Claude Code 官方：设置](https://code.claude.com/docs/en/settings)。用于核对用户设置、项目设置及 `CLAUDE_CONFIG_DIR`。

6. [小小东：开源：12个Claude账号，全健在的诀窍！](https://x.com/xiaoxiaodong/status/2108873683494478141?s=46)。用于补充清理的可选性与适用边界；账号存活为作者自述。

7. [小小东 VPS：仓库 README](https://github.com/nevertoday/xiaoxiaodong-vps)。用于核对部署后生成的本地登录资料及其敏感性；核对日期为 2026-10-10，未运行部署脚本。

8. [Claude Code 封号机制逆向探查：本地下载正文](./Claude%20Code%20封号机制逆向探查.md)（[Lark 来源](https://bytedance.larkoffice.com/docx/E2JudVzf7oCNfhxyxaQcZIW1n0g)）。用于补充持久标识、配置恢复、身份来源、遥测与托管策略的检查边界；未独立复核其源码及服务端推断。

