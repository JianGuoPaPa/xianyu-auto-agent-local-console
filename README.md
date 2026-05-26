# 闲鱼 AI 自动回复本地控制台：自动回复 / 自动发货 / Ollama / Windows 托管

> 非官方项目。仅供学习、研究和自有账号的自动化辅助参考使用。

`xianyu-auto-agent-local-console` 是一个面向闲鱼 / Goofish 场景的本地 AI 客服参考项目：监听闲鱼消息，结合商品信息、历史上下文和提示词完成自动回复，并提供本地监控面板、商品专属策略、Cookie 刷新辅助、Windows 计划任务托管和付款后自动发货文本参考实现。

<p>
  <a href="./LICENSE"><img alt="License: GPL-3.0" src="https://img.shields.io/badge/license-GPL--3.0-blue"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776AB">
  <img alt="Local first" src="https://img.shields.io/badge/local--first-dashboard-111827">
  <img alt="Ollama compatible" src="https://img.shields.io/badge/Ollama-compatible-22c55e">
</p>

<p>
  <img src="./docs/images/screenshot-items.png" width="100%" alt="闲鱼自动回复本地控制台商品策略页面">
</p>

## 这个项目解决什么问题

很多闲鱼自动回复脚本只有命令行，真正长期跑起来时会遇到这些问题：Cookie 什么时候失效、AI 有没有接管、哪个商品发什么内容、最近有没有收到消息、Windows 重启后服务还在不在。

这个仓库把这些运行问题收进一个本地控制台里，更适合拿来学习和二次改造：

| 能力 | 做什么 |
| --- | --- |
| AI 自动回复 | 基于 OpenAI 兼容接口，可接 DashScope、Ollama、本地模型服务等 |
| 商品专属策略 | 每个商品可以配置独立提示词和付款后交付文本 |
| 自动发货参考 | 识别待发货类事件后，发送对应商品配置的交付文本 |
| 本地监控面板 | 查看运行状态、Cookie 状态、日志、最近消息和商品策略 |
| Cookie 刷新辅助 | 从本机 Chrome 登录态中提取候选 Cookie，并保守合并更新 |
| Windows 托管 | 使用计划任务托管 Ollama、主程序和监控面板 |
| 上下文记忆 | 使用 SQLite 保存聊天历史，回复时带入最近会话上下文 |

## 适合拿来做什么

- 学习闲鱼消息监听、会话上下文管理和 AI 自动回复的整体结构。
- 参考 AI 客服的意图分类、议价回复、技术回复和默认回复拆分方式。
- 给自己的虚拟资料、教程、软件包商品做半自动客服辅助。
- 在本地或 Windows 主机上长期运行，并用监控面板看状态、日志和最近消息。
- 参考“付款后发送交付文本”的实现方式，改造成自己的合规交付流程。
- 让 AI 根据本仓库继续改造，适配你的模型、商品、提示词和运行环境。

## 和上游项目有什么不同

本仓库是在已有开源项目基础上的整理版 / 二次改造版，重点放在“本地长期托管”和“可视化配置”。

| 方向 | 本仓库侧重点 |
| --- | --- |
| 运行方式 | 增加本地 Web 监控面板，便于查看状态、日志和商品配置 |
| 商品策略 | 支持按商品维护专属回复规则、交付文本和售罄上架配置 |
| 自动发货 | 将付款后交付文本、发货记录去重、发货事件识别拆成独立模块 |
| Cookie 维护 | 增加 Chrome Cookie 读取、合并、保守更新和 Windows 辅助脚本 |
| Windows 部署 | 提供计划任务脚本，适合放在 Windows 主机上长期托管 |
| 本地模型 | 默认兼容 OpenAI API 格式，也给出 Ollama 本地模型配置示例 |

## 运行界面

以下截图使用示例数据展示本地监控面板的主要页面。实际商品、发货内容和提示词需要按自己的场景配置。

<p>
  <img src="./docs/images/screenshot-prompts.png" width="100%" alt="全局提示词页面">
</p>

## 参考与致谢

本项目是在已有开源项目基础上整理和二次改造的参考版。感谢这些项目和作者：

- [shaxiu/XianyuAutoAgent](https://github.com/shaxiu/XianyuAutoAgent)：本仓库的主要基础项目，提供了闲鱼 AI 客服机器人的整体思路和核心结构。
- [cv-cat/XianYuApis](https://github.com/cv-cat/XianYuApis)：提供了闲鱼相关接口调用思路，本仓库中的接口适配参考了该项目。

也感谢 Python、OpenAI SDK、websockets、loguru、python-dotenv、requests、Ollama 等开源生态。

更多说明见 [ACKNOWLEDGEMENTS.md](./ACKNOWLEDGEMENTS.md)。

## 项目结构

```text
.
├── main.py                         # 闲鱼消息监听与主循环
├── XianyuAgent.py                  # AI 回复、意图路由、各类 Agent
├── XianyuApis.py                   # 闲鱼 / Goofish 相关接口封装
├── context_manager.py              # SQLite 会话上下文与自动发货记录
├── auto_delivery.py                # 待发货事件识别与发货 key 生成
├── cookie_sync.py                  # Chrome Cookie 读取与合并策略
├── monitor_panel.py                # 本地 Web 监控面板
├── prompts/                        # 提示词模板
├── scripts/windows/                # Windows 计划任务和 Cookie 辅助脚本
├── docs/images/                    # README 展示图片
├── tests/                          # 单元测试
├── .env.example                    # 通用配置模板
└── .env.windows.example            # Windows / Ollama 配置模板
```

## 本地运行

### 0. 克隆项目

```bash
git clone https://github.com/JianGuoPaPa/xianyu-auto-agent-local-console.git
cd xianyu-auto-agent-local-console
```

### 1. 准备 Python

建议使用 Python 3.10+。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 2. 创建配置

```bash
cp .env.example .env
```

然后编辑 `.env`，至少填写：

```dotenv
API_KEY=your_llm_api_key
COOKIES_STR=your_goofish_cookie_here
MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MODEL_NAME=qwen-max
```

如果你使用本地 Ollama，可以参考：

```dotenv
API_KEY=ollama
MODEL_BASE_URL=http://127.0.0.1:11434/v1
MODEL_NAME=qwen2.5:3b-instruct
ENABLE_MODEL_SEARCH=False
```

### 3. 提示词

程序会优先读取：

- `prompts/classify_prompt.txt`
- `prompts/default_prompt.txt`
- `prompts/price_prompt.txt`
- `prompts/tech_prompt.txt`

如果这些文件不存在，会回退到对应的 `_example.txt`。建议你复制模板后再改：

```bash
cp prompts/classify_prompt_example.txt prompts/classify_prompt.txt
cp prompts/default_prompt_example.txt prompts/default_prompt.txt
cp prompts/price_prompt_example.txt prompts/price_prompt.txt
cp prompts/tech_prompt_example.txt prompts/tech_prompt.txt
```

这些正式提示词文件默认被 `.gitignore` 忽略，避免把你的具体商品策略提交出去。

### 4. 启动主程序

```bash
python main.py
```

首次运行时，如果 `.env` 中缺少 `API_KEY` 或 `COOKIES_STR`，程序会提示你输入并写回 `.env`。

### 5. 打开本地控制台

```bash
python monitor_panel.py --host 127.0.0.1 --port 8765
```

然后打开：

```text
http://127.0.0.1:8765/
```

## 启动监控面板

监控面板默认只监听本机：

```bash
python monitor_panel.py --host 127.0.0.1 --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765/
```

如果你要在局域网访问，请自行评估风险，再改为：

```bash
python monitor_panel.py --host 0.0.0.0 --port 8765
```

建议设置 `DASHBOARD_TOKEN`，并只在可信网络中使用。

## 3 分钟看懂运行链路

```text
闲鱼 / Goofish 消息
        |
        v
main.py WebSocket 监听
        |
        v
商品信息 + 历史上下文 + 商品专属提示词
        |
        v
XianyuAgent.py 调用 OpenAI 兼容模型
        |
        v
发送回复 / 付款后发送交付文本
        |
        v
monitor_panel.py 本地看板查看状态、日志、商品策略
```

## Windows 托管运行

项目提供了一组 Windows 脚本，用计划任务托管：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\install-autoagent.ps1
```

安装脚本会：

- 创建 `.venv`
- 安装依赖
- 生成或补全 `.env`
- 创建三个计划任务：`XianyuAutoAgent-Ollama`、`XianyuAutoAgent-Service`、`XianyuAutoAgent-Dashboard`

手动启动：

```powershell
schtasks /Run /TN "XianyuAutoAgent-Ollama"
schtasks /Run /TN "XianyuAutoAgent-Service"
schtasks /Run /TN "XianyuAutoAgent-Dashboard"
```

如果你的 Python、Ollama 模型目录或 pip 缓存目录不在默认位置，可以传参：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\install-autoagent.ps1 `
  -PythonExe "C:\Path\To\python.exe" `
  -OllamaModelsDir "D:\Ollama\models" `
  -PipCacheDir "D:\pip-cache"
```

## Docker 运行

Docker 更适合跑主程序，不适合自动读取本机浏览器 Cookie。你仍然需要自己准备 `.env`。

```bash
cp .env.example .env
docker compose up -d --build
```

## 常见改造方向

- 接入 Ollama、LM Studio、vLLM、DashScope 等 OpenAI 兼容模型服务。
- 给不同商品配置不同提示词，例如资料包、软件源码、定制开发、售后咨询。
- 将自动发货文本改成自己的合规交付说明、网盘说明、兑换方式或售后指引。
- 扩展监控面板，例如增加订单状态、回复命中原因、模型调用统计。
- 将 Windows 计划任务改造成自己的长期托管脚本或局域网内服务。

## 搜索关键词

闲鱼自动回复、闲鱼 AI 客服、Goofish auto reply、闲鱼自动发货、闲鱼本地控制台、Ollama 自动回复、本地模型客服、AI customer service、xianyu auto agent、idlefish automation。

## 发布与推广

如果你基于本仓库发布自己的版本，可以参考 [Release and Promotion Checklist](./docs/RELEASE_AND_PROMOTION.md)。里面包含 Release 文案、GitHub Topics、外部分发短文案和后续优化清单。

## Cookie 说明

`COOKIES_STR` 需要来自你自己的闲鱼 / Goofish 网页端登录态。通常需要包含 `unb`、`_m_h5_tk` 等关键 Cookie。某些场景还会遇到 `x5sec`、滑块验证、登录过期、风控校验等问题，这些属于平台侧状态，不是代码一定能自动解决的。

Cookie 同步逻辑的原则是保守更新：

- 候选 Cookie 必须包含必要字段才会写入。
- 如果当前 Cookie 含有验证相关字段，新 Cookie 缺失时不会盲目覆盖。
- 建议只在你自己的浏览器、自己的账号、本机环境中使用。

## 自动发货说明

自动发货逻辑用于识别付款后待发货类消息，并发送你配置的交付文本。它不会自动生成资源，也不会帮你判断资源是否合法。

使用前请确认：

- 商品确实适合文本交付。
- 交付内容是你有权提供的。
- 不要把真实敏感链接硬编码到公开仓库。
- 每个商品的交付文本建议在本地面板或本地数据库中单独配置。

## 合规与边界

- 本项目不是闲鱼 / Goofish 官方项目，也不是官方 API。
- 本项目不保证开箱即用，不保证适配所有账号、浏览器、Cookie、系统和商品类型。
- 本项目没有绕过平台风控、破解权限或读取他人账号数据的能力。
- Cookie 失效、滑块验证、风控校验、接口变化都可能导致不可用，需要自行处理。
- 自动回复可能出错，涉及价格、售后、承诺、发货内容时请务必自行审核。
- 自动发货只适合发送你有权交付的文本内容，例如说明、链接、兑换方式等。
- 请遵守平台规则、法律法规和你所在地区的合规要求。

## 测试

```bash
python -m unittest discover -s tests -v
python -m py_compile auto_delivery.py cookie_sync.py context_manager.py main.py monitor_panel.py XianyuAgent.py XianyuApis.py
```

## 安全说明

本仓库只保留源码、示例配置、提示词模板、脚本和测试，不包含个人运行数据。实际使用时，`.env`、Cookie、API Key、聊天数据库、运行日志、私有提示词和交付内容都应只保存在自己的本地环境中。

仓库中的 `.gitignore` 已默认排除这些运行时文件，方便二次开发时保持公开版本干净。

## 技术交流

如果你对项目适配、AI 自动化、闲鱼托管、本地模型等方向感兴趣，可以扫码联系我或加入 AI 交流群。

<table>
  <tr>
    <td align="center"><strong>联系作者</strong></td>
    <td align="center"><strong>AI 交流群</strong></td>
  </tr>
  <tr>
    <td><img src="./docs/images/wechat-contact.jpg" width="260" alt="联系作者微信二维码"></td>
    <td><img src="./docs/images/ai-community-qr.jpg" width="260" alt="AI 交流群二维码"></td>
  </tr>
</table>

如果群二维码过期，可以先添加个人微信联系更新。

## License

本项目沿用上游项目的 GPL-3.0 协议。详见 [LICENSE](./LICENSE)。
