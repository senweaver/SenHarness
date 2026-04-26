<div align="center">

# SenHarness

**基于 Harness Engineering 范式的企业级多租户开源 AI Agent 平台。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Next.js 15](https://img.shields.io/badge/next.js-15-black.svg)](https://nextjs.org/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.117%2B-009688.svg)](https://fastapi.tiangolo.com/)

[English](README.md) · [简体中文](README_zh-CN.md)

</div>

---

## SenHarness 是什么

你是否见过这样的 AI Agent：跑了 30 分钟、忘了自己最初的计划、烧光了你的 token 预算，最后给出一个"修复"——问题本身却根本不存在？**模型本身是不够的**。

**Harness Engineering（Agent 马具工程）** 是设计模型"外面"的一切的学科：文件系统、沙箱 shell、记忆、检索、上下文治理、编排、护栏、审批。它是 AI Agent 的**马具**——让模型稳稳走在正轨上的缰绳。

SenHarness 是**面向企业的 Harness Engineering 运行时**。一套代码，两种部署模式：

- **单企业自部署**——一家公司 `docker compose up`，完事
- **多租户 SaaS**——同一份二进制，服务多个租户，物理隔离

全部采用 **MIT 许可**。

---

## 一句话讲清 Harness

SenHarness 在模型外面套了六层：上下文 · 工具 · 编排 · 记忆 · 评估 · 约束——保证 Agent 不偏题、不越权、不烧钱。

---

## SenHarness 的差异化

1. **Agent Runtime 可插拔**。`AgentBackend` 协议 + 网关，意味着任何运行时——内置的 `NativeBackend`、OpenClaw 远程 worker、未来新的 Agent 框架——都能以一个适配器文件接入。
2. **多租户原生**。所有数据表都是 `workspace_id` 隔离。同一份二进制，既能单企业 on-prem，也能上百租户 SaaS——扩展时不用重写。
3. **MIT 许可**。拿来用、商用、fork、二次开发都行。没有 patent clause 陷阱，没有 AGPL 传染性。企业版在此之上叠加合规包和 SLA——核心永久免费。

底层栈：PostgreSQL + pgvector · Redis · FastAPI · SQLAlchemy 2 async · Next.js 15 + shadcn/ui · 以及 `pydantic-ai` 生态（harness · shields · backends · skills · subagents · todo · middleware · summarization）。

---

## 谁应该用

| 你的身份 | SenHarness 能给你什么 |
|---|---|
| 企业内的**员工** | 一个记得你偏好、遵守公司规则、敏感操作前会请审的个人 AI 助手 |
| 企业的**管理员** | 10 分钟完成工作区配置——邀请成员、配置 LLM 供应商、接入飞书/Slack、打开审计留痕 |
| **开发者 / 平台工程师** | 可扩展的 Harness 层：通过 MCP 接新工具、自己写 Agent Runtime 适配器、发布自己的技能包 |
| **企业 IT 采购** | 多租户 RBAC · 审计 · HITL 审批 · 信封加密金库 · 可插拔 Keyring（env / file / passphrase / AWS KMS / GCP KMS / Azure KV / Vault）|

---

## 快速开始

**前置条件**：Docker 24+ · 空闲内存 4 GB · 一个 LLM API Key（OpenAI / Anthropic / DeepSeek / Ollama...）

```bash
git clone https://github.com/senweaver/SenHarness.git
cd SenHarness
cp .env.example .env
# 编辑 .env：设置 JWT_SECRET_KEY、DB_PASSWORD、REDIS_PASSWORD，至少一个 LLM key

docker compose up -d

# 首次初始化（在 backend 容器内）
docker compose exec backend python -m cli.commands migrate
docker compose exec backend python -m cli.commands seed
docker compose exec backend python -m cli.commands create-admin

# 打开 http://localhost:3000
```

---

## 核心概念

| 概念 | 描述 |
|---|---|
| **Workspace（工作区 / 租户）** | 治理边界。承载成员、角色、部门、agent、知识、策略、审计。单企业用一个工作区；集团 / SaaS 用多个。 |
| **Department（部门）** | 工作区内部的组织树，任意层级深度。用于归属、审批路由、按角色搜索。 |
| **Identity（身份）** | 全局账号（邮箱 + 密码 + 可选 OAuth + 可选 TOTP）。一个 Identity → 在 N 个工作区有不同角色。 |
| **Agent** | 员工对话的配置单元。UI 显示名按工作区可配（助理 / 数字员工 / 智能体 / AI 伙伴 / ...）。 |
| **Squad（团队）** | 多 Agent 协作组，含调度 Agent。 |
| **Skill Pack（技能包）** | 兼容 Anthropic Agent Skills 的 SKILL.md，渐进式加载。 |
| **Toolbox（工具箱）** | 内置 + MCP + 插件三合一工具集，按 Agent 绑定。 |
| **Flow（流程）** | 定时或触发式自动化（cron · webhook · on_message · 手动）。 |
| **Channel（通道）** | IM 集成（Slack · 飞书 · Discord · 通用 webhook，V2 补更多）。 |
| **Vault（金库）** | 信封加密凭据存储，可插拔 Keyring。 |
| **Policy（策略）** | 自主性等级（L1/L2/L3）· 护栏 · 预算 · 工具 ACL · 审批工作流。 |
| **Agent Runtime（Agent 运行时）** | 可插拔执行后端。 |

---

## 可插拔的 Agent Runtime

`AgentBackend` 协议定义在 [`backend/app/agents/kernels/base.py`](backend/app/agents/kernels/base.py)：

```python
class AgentBackend(Protocol):
    backend_kind: str
    async def run(self, req: RunRequest) -> AsyncIterator[RunEvent]: ...
    async def cancel(self, run_id: uuid.UUID) -> None: ...
    def capabilities(self) -> BackendCapabilities: ...
```

官方支持的 runtime：

| Runtime | kind | 传输方式 | 状态 |
|---|---|---|---|
| **NativeBackend** | `native` | 同进程 | ✅ 稳定 |
| **OpenClaw 远程 worker** | `openclaw` | 网关 + HTTPS 长轮询 | ✅ 稳定 |
| *自定义 runtime* | `your-backend` | *任意* | 社区贡献 |

## 内置通道 Provider

通道是第二个可插拔层——把 IM 平台接入 SenHarness。采用和 Agent Runtime 相同的注册表模式。

| Provider | kind | 入站认证 | 出站 | 状态 |
|---|---|---|---|---|
| Slack | `slack` | v0 HMAC + 5 分钟重放窗口 | chat.postMessage | ✅ |
| 飞书 / Lark | `feishu` | verification_token (1.0/2.0) | Open API 租户 token | ✅ |
| Discord | `discord` | Ed25519 | Discord REST API | ✅ |
| 钉钉 | `dingtalk` | HMAC-SHA256 + 60 秒重放窗口 | 自定义机器人 webhook | ✅ V2 |
| 企业微信 | `wecom` | SHA1 消息签名 + AES-CBC 解密 | message/send REST | ✅ V2 |
| 通用 webhook | `webhook` | 共享 `inbound_token` | — （只入站）| ✅ |
| *自定义 provider* | `your_kind` | *自定* | *自定* | 社区贡献 |

---

## 仓库结构

```
SenHarness/
├─ backend/              FastAPI + SQLAlchemy + pydantic-ai
│  ├─ app/
│  │  ├─ agents/         Agent 核心（kernels / harness / tools / skills）
│  │  ├─ api/            REST + WebSocket 路由
│  │  ├─ core/           config / security / middleware / rate limit / errors
│  │  ├─ db/             async engine / 模型 / 仓储
│  │  ├─ schemas/        Pydantic DTO
│  │  ├─ security/       JWT / keyring / 信封加密
│  │  ├─ services/       业务服务
│  │  └─ workflows/      流程 / 触发器 / 调度
│  ├─ tests/             单元 / 集成 / E2E
│  └─ scripts/           运维 / 开发辅助 / 诊断探针
├─ frontend/             Next.js 15 · shadcn/ui · Tailwind 4
├─ docs/                 架构 · 适配器 · 白皮书 · 快速开始
└─ docker-compose*.yml   dev / prod / frontend-only 三套编排
```

---

## 贡献

欢迎贡献代码！详见 [CONTRIBUTING.md](CONTRIBUTING.md)——开发流程、代码规范、如何让你的适配器进入官方注册表。

---

## 许可证

[MIT](LICENSE) © 2026 SenHarness 贡献者

---