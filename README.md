# 闲鱼托管自动回复参考项目

> 非官方项目。仅供学习、研究和自有账号的自动化辅助参考使用。

这是一个面向闲鱼 / Goofish 场景的 AI 自动回复与本地托管参考项目。它可以监听闲鱼消息，结合商品信息、历史上下文和提示词，完成基础咨询、议价咨询、技术咨询等自动回复；也包含本地监控面板、Cookie 刷新辅助、Windows 计划任务脚本和付款后自动发货文本的参考实现。

这个仓库不是通用成品，也不是官方 API。不同账号、商品、Cookie 状态、平台风控、浏览器环境和操作系统环境都可能不一样。拿去使用时，大概率需要你根据自己的业务、账号状态和运行环境继续适配。

## 适合拿来做什么

- 学习闲鱼消息监听、会话上下文管理和自动回复的整体结构。
- 参考 AI 客服的意图分类、议价回复、技术回复和默认回复拆分方式。
- 基于自己的商品信息改造提示词，实现半自动客服辅助。
- 在本地或 Windows 主机上长期运行，配合监控面板查看状态、日志和最近消息。
- 为虚拟资料、教程、软件包等商品参考“付款后发送交付文本”的实现方式。
- 让 AI 根据本仓库继续改造，适配你自己的模型、商品、发货规则和部署方式。

## 明确边界

- 本项目不保证开箱即用。
- 本项目不保证适配所有闲鱼账号、浏览器、Cookie、系统和商品类型。
- 本项目依赖网页端 Cookie / 登录态，Cookie 失效、风控验证、接口变化都会导致不可用。
- 本项目没有绕过平台风控、破解权限或读取他人账号数据的能力。
- 自动回复可能出错，涉及价格、售后、承诺、发货内容时请务必自行审核。
- 自动发货只适合发送你有权交付的文本内容，例如说明、链接、兑换方式等。
- 请遵守平台规则、法律法规和你所在地区的合规要求。

## 功能概览

- AI 自动回复：基于 OpenAI 兼容接口，可接 DashScope、Ollama、本地模型服务等。
- 意图路由：将消息分为议价、技术咨询、默认咨询、无需回复等类型。
- 上下文记忆：使用 SQLite 保存聊天历史，回复时带入最近上下文。
- 人工接管：通过指定关键词切换某个会话的人工 / AI 接管状态。
- 自动发货参考：识别“待发货”类事件后，发送对应商品配置的交付文本。
- 本地监控面板：查看运行状态、配置状态、日志、最近消息和商品回复配置。
- Cookie 刷新辅助：可从本机 Chrome 登录态中提取候选 Cookie 并谨慎更新。
- Windows 托管脚本：提供计划任务方式启动 Ollama、主程序和监控面板。

## 运行界面

以下截图使用示例数据展示本地监控面板的主要页面，实际商品、发货内容和提示词需要按自己的场景配置。

<p>
  <img src="./docs/images/screenshot-items.png" width="100%" alt="商品策略页面">
</p>

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
