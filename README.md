# Zotero MCP × Qoder 接入

让 Qoder Desktop 通过 MCP 读写本机 Zotero 文库。MCP 服务来自 [oscardvs/zoteus](https://github.com/oscardvs/zoteus)（MIT，npm 包 `@oscardvs/zoteus`）；本仓库只保存 Qoder 侧接入配置、启动器/探活脚本和实测说明，不包含上游代码，也不保存任何 API key。

链路：

```
Qoder Desktop → stdio MCP (npx @oscardvs/zoteus) → 本机 Zotero（SQLite / local API 23119）+ Zotero Web API
```

## 实测环境

| 项 | 版本 |
| --- | --- |
| OS | Windows 10/11 x64 |
| Zotero | 9.0.6（便携版） |
| zoteus | 1.23.0（npm `@oscardvs/zoteus`） |
| Node | 20+（实测 v24.12.0） |
| 工具数 | 34 |
| Qoder | Desktop，用户级 MCP |

已验证：MCP 握手、`tools/list`（34 个工具）、`zotero_whoami` 返回云端账号可用。

## 两种配置方式

| 方式 | 密钥位置 | 适用 |
| --- | --- | --- |
| **launcher（推荐）** | `%USERPROFILE%\.qoder-cn\zotero-mcp\zoteus.cmd` | 各版本 Qoder 都稳；`env` 字段被丢时仍能工作 |
| env | `settings.json` 的 `mcpServers.zotero.env` | 配置更短；当前 Qoder 版本会传 `env`，旧版本可能不传 |

两种方式都要让 Qoder 的进程拿到 `ZOTERO_API_KEY`。本仓库的脚本只生成启动器，不会把密钥写进仓库或打印到终端。

## 快速开始

### 1. 安装 Node.js

需要 Node 20+（自带 `npx`）：

```powershell
node --version
npx --version
```

### 2. 获取 Zotero API key

打开 <https://www.zotero.org/settings/keys>：

1. 点 **Create new private key**。
2. 勾选 **Allow library access**，权限选 **Read/Write**；要传附件/笔记再加 **Allow write access** 到 notes/files。
3. 保存后复制 key。页面上的 **Your userID is ...** 就是个人文库 ID（可选，用于显式固定文库）。

key 可能带有文库写权限，不要提交到 Git、不要贴进公开 issue。泄露后回同一页面撤销。

### 3. 用启动器接入（推荐）

```powershell
powershell -ExecutionPolicy ByPass -File scripts/install.ps1
```

脚本会：

- 优先读取环境变量 `ZOTERO_API_KEY`；没有时才安全提示输入（不回显、不落命令行历史）；
- 可选填个人文库 ID 和 Zotero 数据目录；
- 生成 `%USERPROFILE%\.qoder-cn\zotero-mcp\zoteus.cmd`（CRLF、含 `setlocal`）；
- 打印一段可直接粘进 `settings.json` 的 `mcpServers.zotero` 配置。

若 Zotero 数据目录不是默认的 `%USERPROFILE%\Zotero`（例如便携版装在别的盘），加参数：

```powershell
powershell -ExecutionPolicy ByPass -File scripts/install.ps1 -DataDir "D:\ZoteroData"
```

数据目录是包含 `zotero.sqlite` 和 `storage` 的那一层。填错时 zoteus 会找不到附件/全文，但基本检索仍可用。

把打印出的片段加入 `%USERPROFILE%\.qoder-cn\settings.json` 的 `mcpServers`：

```json
"zotero": {
  "command": "cmd",
  "args": ["/c", "C:\\Users\\<你>\\.qoder-cn\\zotero-mcp\\zoteus.cmd"]
}
```

完整片段见 [`qoder/zotero-launcher.mcp.json`](qoder/zotero-launcher.mcp.json)。

### 3'. 用 env 接入（备选）

在 `settings.json` 的 `mcpServers` 下加入 [`qoder/zotero.mcp.json`](qoder/zotero.mcp.json) 的内容，并把 `<...>` 换成真实值：

```json
"zotero": {
  "command": "cmd",
  "args": ["/c", "npx", "-y", "@oscardvs/zoteus"],
  "env": {
    "ZOTERO_API_KEY": "<你的 API key>",
    "ZOTERO_LIBRARY_ID": "<你的 userID，可留空>",
    "ZOTERO_LIBRARY_TYPE": "user",
    "ZOTERO_DATA_DIR": "<Zotero 数据目录>"
  }
}
```

`ZOTERO_LIBRARY_ID` 可以省略（个人文库会按 key 归属解析）；群组文库必须同时给数字 `ZOTERO_LIBRARY_ID` 和 `ZOTERO_LIBRARY_TYPE=group`。

保存后重启 Qoder 或新开一个会话；MCP 工具在会话启动时加载，旧会话里不会出现。

## 验证

launcher 方式：

```powershell
python scripts/probe_zotero_mcp.py --from-launcher "$env:USERPROFILE\.qoder-cn\zotero-mcp\zoteus.cmd"
```

env 方式（直接从 Qoder 配置里取 `mcpServers.zotero.env`）：

```powershell
python scripts/probe_zotero_mcp.py --from-qoder
```

预期输出（节选）：

```
INIT: zoteus 1.23.0 (protocol 2024-11-05)
TOOL COUNT: 34
TOOLS: zotero_whoami, zotero_search_items, ...
CALL zotero_whoami: {
  "version": "1.23.0",
  "cloud": true,
  ...
}
```

`cloud: true` 说明 API key 已生效；`localApi` 反映 Zotero 桌面端是否开着。首次 `npx` 会下载包，慢一点正常；探活脚本默认超时 180 秒，需要 Python 3.8+（只用标准库）。

再验一次检索（只读）：

```powershell
python scripts/probe_zotero_mcp.py --from-qoder --call zotero_search_items --args '{"q":"impact","limit":3}'
```

## 读写边界

| 操作 | 路径 | 说明 |
| --- | --- | --- |
| 检索、读元数据、读笔记/高亮 | 本机 SQLite + local API 23119 | Zotero 9.x 的 local API **只读** |
| 改元数据、标签、集合 | Zotero Web API | 需要 `ZOTERO_API_KEY`；写完要等 Zotero 同步才在界面出现 |
| DOI 入库、附件、笔记高亮 | Zotero 桌面端（9.x 走 connector，需开着；桌面端关着时依赖 key） | 部分字段可能缺失，入库后回读补 |
| 语义检索 | 本地向量 | 默认降级为 BM25；向量需另装 `@huggingface/transformers`（约 700 MB），非必要不装 |

## 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| Qoder 里看不到 `zotero` 工具 | 新开一个会话；MCP 工具只在会话启动时加载 |
| `cloud: false` / 提示需要 API key | key 没传到服务进程；改用 launcher 方式，或确认 Qoder 版本会传 `env` |
| `ZOTERO_LIBRARY_ID` 报错 `not usable` | 值是 0/负数/非数字，或 key 无权访问该文库；个人文库可直接删掉该变量 |
| 找不到附件/全文 | `ZOTERO_DATA_DIR` 没指向含 `zotero.sqlite` 的目录；便携版必须显式给 |
| 写入成功但界面没变 | 写走云端 Web API；打开 Zotero 等一次同步 |
| 本机 local API 连不上 | Zotero 桌面端没开，或 9.x 未开启本地 API；读仍可走 SQLite，写入走云端 |
| 语义检索一直说不可用 | 未装 `@huggingface/transformers`；默认 BM25 关键词检索不受影响 |
| `zotero_search_items` 报参数错 | 搜索参数名是 `q`，不是 `query` |
| 首次调用很慢 / 超时 | `npx` 正在下载 zoteus；先在终端跑一次 `npx -y @oscardvs/zoteus --help` 预热，或固定版本 `@oscardvs/zoteus@1.23.0` |

## 安全提示

- `ZOTERO_API_KEY` 是写权限凭据。启动器和 `settings.json` 里都是明文，别把这两个文件提交到任何仓库；本仓库的模板只有占位符。
- 探活脚本从 Qoder 配置或启动器读取环境变量，只打印变量名，不打印值；`--verbose` 输出 stderr 时也会遮掉密钥。
- 不需要写入时，给 key 只勾 **Read**；需要写时再升到 Read/Write。泄露后立刻在 <https://www.zotero.org/settings/keys> 撤销。
- 写入前先备份文库（Zotero 自带导出），批量改标签/集合尤其要小心。

## 卸载

从 `settings.json` 删除 `zotero` 条目，删除 `%USERPROFILE%\.qoder-cn\zotero-mcp\zoteus.cmd`。zoteus 自己的索引/缓存目录按官方文档清理；Zotero 文库不受影响。

## 许可与来源

本仓库的配置、脚本与文字为 MIT。MCP 服务来自 [oscardvs/zoteus](https://github.com/oscardvs/zoteus)（MIT），版权归原作者。本项目与 Zotero 官方无关联。
