# Release and Promotion Checklist

这个清单用于发布 `xianyu-auto-agent-local-console` 的公开版本，并把仓库从“能搜到”优化到“路人愿意点进来、愿意 Star、愿意试跑”。

## 1. 发布前检查

- README 第一屏能说明项目用途：闲鱼 AI 自动回复、本地控制台、自动发货参考、Ollama、Windows 托管。
- README 至少包含一张真实控制台截图。
- `.env.example` 不包含真实 Cookie、API Key、网盘链接、商品交付内容。
- `prompts/*_example.txt` 只保留通用提示词模板，不包含真实商品策略。
- `data/`、`logs/`、`.env`、私有提示词、聊天数据库都没有进入 Git。
- CI 能跑过单元测试和 `py_compile`。

## 2. 推荐 Release 内容

建议从 `v0.1.0` 开始发正式 Release。

Release 标题：

```text
v0.1.0 - 闲鱼 AI 自动回复本地控制台首个公开版
```

Release 摘要：

```text
首个公开整理版，包含闲鱼消息监听、AI 自动回复、本地监控面板、商品专属策略、付款后自动发货文本参考、Cookie 刷新辅助和 Windows 计划任务托管脚本。
```

Release 亮点：

- 本地 Web 控制台：查看运行状态、日志、最近消息和商品策略。
- 商品专属策略：每个商品独立配置提示词和付款后交付文本。
- OpenAI 兼容模型：可接 DashScope、Ollama、本地模型服务。
- Windows 托管脚本：用计划任务运行 Ollama、主程序和看板。
- Cookie 辅助：从本机 Chrome 登录态提取候选 Cookie，并保守更新。

Release 附件可以先不放二进制包，直接使用 GitHub 自动生成的源码包即可。后续如果做一键包，再附加：

- Windows 安装压缩包
- 示例配置包
- 演示视频或 GIF

## 3. GitHub 仓库设置

推荐仓库描述：

```text
闲鱼 / Goofish AI 自动回复本地控制台：自动回复、商品专属策略、自动发货参考、Cookie 同步辅助、Ollama 本地模型和 Windows 托管脚本。
```

推荐 Topics：

```text
xianyu
goofish
idlefish
xianyu-auto-reply
ai-customer-service
auto-reply
auto-delivery
ollama
local-first
windows
python
chatbot
```

## 4. 外部分发文案

### 短版

```text
整理了一个闲鱼 AI 自动回复本地控制台参考项目：支持商品专属提示词、付款后自动发货文本、本地 Web 看板、Cookie 辅助刷新、Ollama 本地模型和 Windows 计划任务托管。非官方项目，仅供学习和自有账号自动化辅助参考。

GitHub: https://github.com/JianGuoPaPa/xianyu-auto-agent-local-console
```

### 长版

```text
最近把自己折腾闲鱼自动回复 / 自动发货 / 本地模型客服的一套东西整理成了公开参考项目。

主要包含：
- 闲鱼 / Goofish 消息监听
- AI 自动回复和意图路由
- 每个商品独立提示词
- 付款后自动发送交付文本的参考实现
- 本地 Web 控制台
- Cookie 刷新辅助
- Ollama / OpenAI 兼容接口
- Windows 计划任务长期托管

项目不是官方 API，也不是保证开箱即用的成品，更多是给想研究闲鱼 AI 客服、本地自动化、虚拟资料交付流程的人做参考。

GitHub: https://github.com/JianGuoPaPa/xianyu-auto-agent-local-console
```

## 5. 可发布的平台

- GitHub Release
- V2EX：分享创造 / 程序员节点
- 掘金：AI 工具、本地自动化、Python 项目方向
- 知乎：闲鱼自动回复、本地模型客服、Ollama 自动化方向
- 小红书：用截图 + “本地 AI 客服看板”做轻量展示
- 闲鱼商品页：作为“技术能力展示链接”，不要宣传绕平台规则

## 6. 后续更容易拿 Star 的改进

- 增加 30 秒演示 GIF。
- 增加 Windows 从 0 安装视频。
- 增加 `docs/FAQ.md`：Cookie、滑块、Ollama、计划任务、自动发货常见问题。
- 增加 `docs/ARCHITECTURE.md`：WebSocket、mtop、AI 回复、面板、Cookie 同步的关系图。
- 增加更多单元测试，尤其是自动发货事件解析和 Cookie 合并策略。
