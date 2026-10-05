# Cardinal 融合方案：A 股深度 AI 量化 Agent 系统

> **目标：** 将 Cardinal 的设计蓝图 + market-agents 的核心算法 + Vibe-Trading 的工程架构，融合为专注 A 股深度分析的 AI Agent 量化系统。

**当前状态：** 三项目并存，彼此独立
**目标状态：** 一个统一的项目，以 Cardinal 命名，承载 market-agents 的算法能力和 Vibe-Trading 的工程框架

---

## 1. 三项目能力矩阵

| 能力维度 | Cardinal（蓝图） | market-agents（算法） | Vibe-Trading（工程） |
|---------|:---:|:---:|:---:|
| **系统提示词 / Agent 身份** | ✅ 366行完整设计 | ❌ 仅 CLI 脚本 | ✅ LangChain Agent |
| **Agent Loop（ReAct）** | ✅ 自写轻量引擎 | ❌ | ✅ LangGraph |
| **Skill 系统** | ✅ 10 个规划 | ❌ | ✅ 80+ SKILL.md |
| **多 Agent 协作（Swarm）** | ❌ | ❌ | ✅ DAG 编排 |
| **数据采集** | ❌ | ✅ akshare/baostock | ✅ 18 源 fallback |
| **NMF 分解** | ✅ 规划了 | ✅ 核心能力 | ❌ 没有 |
| **Agent 市场仿真** | ✅ 规划了 | ✅ 5 类 Agent | ❌ 没有 |
| **GMM/HMM 状态分类** | ❌ | ✅ 核心能力 | ❌ |
| **因子库** | ✅ 规划了 | ❌ | ✅ 460 alpha |
| **回测引擎** | ❌ | ✅ A 股规则引擎 | ✅ 子进程沙箱 |
| **模拟盘** | ✅ 规划了 | ✅ 模拟引擎 | ✅ 10+ 券商连接器 |
| **前端 Dashboard** | ❌ | ✅ Vue 3 | ✅ React 19 |
| **IM 消息通道** | ❌ | ❌ | ✅ 16 适配器 |
| **MCP Server** | ❌ | ❌ | ✅ |
| **定时任务** | ✅ 详细流程 | ✅ 调度器 | ✅ cron 执行器 |
| **风控系统** | ✅ 详细规则 | ✅ 风控配置 | ❌ |
| **上下文压缩** | ✅ L1-L5 设计 | ❌ | ✅ 5层压缩 |
| **记忆系统** | ✅ 规划了 | ❌ | ❌ |
| **测试覆盖** | ❌ | ❌ | ✅ 4700+ |

---

## 2. 融合策略

### 核心理念：「算法自研 + 架构借鉴」

- **自己做的（核心竞争力）：** NMF 分解、Agent 仿真、GMM/HMM 状态分类、A 股风控规则
- **借鉴 Vibe-Trading 的（工程框架）：** Skill 系统设计、Swarm 多 Agent 模式、上下文管理、前端 UI 模式
- **不照搬的：** 不引入 LangChain/LangGraph 依赖链（太重），保持 Agent Loop 自研

### 架构对比

```
当前 market-agents：
  CLI → 数据采集 → NMF/仿真 → 回测 → 前端

目标 Cardinal（融合后）：
  CLI / API Server / IM Channels
       ↓
  Agent Loop（ReAct + 上下文管理 + 记忆）
       ↓
  Skill 系统（按需加载专业能力）
       ↓
  ┌──────────┬──────────┬──────────┬──────────┐
  │ 数据 Skill│ NMF Skill│ 因子 Skill│ 回测 Skill│
  │ (akshare │ (仿真引擎)│ (因子库) │ (A股规则)│
  │ baostock)│ (GMM/HMM)│          │ (风控)   │
  └──────────┴──────────┴──────────┴──────────┘
       ↓
  前端 Dashboard / 模拟盘 / 通知
```

---

## 3. 分阶段实施计划

### Phase 1：Agent 骨架 —— 让 Cardinal「活起来」

**目标：** 把现有的 CLI 脚本改造成真正的 AI Agent，能对话、能调用工具

**具体任务：**

#### 1.1 项目结构重组
- 保持中文目录名的风格
- 新增 `后端/智能体/` 目录（Agent 核心）
- 新增 `后端/技能/` 目录（Skill 系统）
- 保持现有 `后端/分析/` 算法不动

```
后端/
├── 智能体/           # 新增：Agent 核心
│   ├── 循环.py       # Agent Loop（ReAct）
│   ├── 上下文.py     # 多级上下文管理（借鉴 Cardinal L1-L5 + Vibe-Trading 5层）
│   ├── 记忆.py       # 跨会话记忆
│   ├── 工具注册.py   # 工具注册表
│   └── 提示词.md     # Cardinal 系统提示词
├── 技能/             # 新增：Skill 系统
│   ├── 数据.py       # 封装数据采集为工具
│   ├── NMF.py        # 封装 NMF/仿真为工具
│   ├── 回测.py       # 封装回测为工具
│   └── 因子.py       # 对接因子库
├── 分析/             # 现有：算法核心（不动）
├── 采集/             # 现有：数据管道（不动）
├── 存储/             # 现有
├── 接口/             # 现有：FastAPI
└── 配置/             # 现有
```

#### 1.2 Agent Loop 重写
- 基于 Cardinal 的 `agent_loop.py` 改造，**不引入 LangChain**
- 参考 Vibe-Trading 的 5 层上下文管理（microcompact → collapse → compact → explicit → iterative）
- 从 Cardinal 的 `cardinal_prompt.md` 提取系统提示词
- 对接 LLM（优先用 tuzi API，已有配置）
- 支持 function calling 格式的工具注册

#### 1.3 首批工具封装
把现有分析能力包装为 Agent 可调用的 function：

| 工具名 | 封装内容 | 来源 |
|--------|---------|------|
| `get_kline` | K线数据拉取 | 采集/管道.py |
| `run_nmf` | NMF 分解 | 分析/推断/矩阵分解.py |
| `classify_states` | GMM 状态分类 | 分析/推断/隐马尔可夫.py |
| `run_simulation` | Agent 仿真 | 分析/仿真/仿真引擎.py |
| `run_backtest` | 回测 | 分析/回测/回测引擎.py |
| `get_signal` | 当日信号 | 分析/信号/每日.py |
| `get_portfolio` | 组合持仓 | 接口/组合.py |

#### 1.4 交互模式
- CLI 模式：`cardinal chat` 进入对话
- 单次模式：`cardinal ask "分析茅台最近走势"`
- 保持现有批处理模式：`cardinal batch`

**验证标准：** 终端里输入 "帮我分析 000001.SZ 最近 60 天的 NMF 状态并给出交易建议"，Agent 能自动调用 get_kline → run_nmf → classify_states → 生成建议

---

### Phase 2：Skill 系统 —— 让 Cardinal「专业起来」

**目标：** 建立可扩展的 Skill 体系，每个 Skill 是一个独立的能力模块

#### 2.1 Skill 规范
借鉴 Vibe-Trading 的 SKILL.md 格式，每个 Skill 包含：
- 元数据（名称、描述、触发条件）
- 工具列表
- 工作流程
- 参考文档

#### 2.2 首批 A 股专属 Skills

| Skill | 内容 | 优先级 |
|-------|------|--------|
| `ashare-data` | A 股数据（对标 Vibe-Trading 但有 NMF 特征） | P0 |
| `nmf-signal` | NMF 模式分解 + 信号生成 | P0 |
| `market-state` | GMM/HMM 市场状态分类 | P0 |
| `agent-simulation` | Agent 群体仿真 + 买卖压力分析 | P1 |
| `a-share-backtest` | A 股回测（已有引擎，包装为 Skill） | P1 |
| `sector-analysis` | 行业轮动/板块热度（已有行业数据） | P1 |
| `fund-flow` | 资金流向（北向/主力/融资融券） | P2 |
| `risk-control` | 风控检查 | P2 |
| `paper-trading` | 模拟盘 | P2 |
| `daily-report` | 日报生成 | P2 |

#### 2.3 Skill 按需加载
- Agent 根据用户问题自动判断需要加载哪个 Skill
- 不预加载全部（节省 token）
- 实现方式：工具描述里写清楚触发场景

---

### Phase 3：多 Agent 协作 —— 让 Cardinal「分工协作」

**目标：** 引入轻量级多 Agent 协作（不做完整的 Swarm DAG，先做串行管道）

#### 3.1 场景化 Agent 编排

三个预设协作模式：

**模式 A：快速诊断（单 Agent）**
```
用户 → Cardinal → 调用工具 → 回复
```
用于日常问答、单一分析任务。

**模式 B：深度分析（串行管道）**
```
用户 → 数据 Agent → NMF Agent → 回测 Agent → 汇总 Agent → 回复
```
用于 "全面分析某只股票" 类任务。

**模式 C：对比研究（并行 + 汇总）**
```
用户 → ┌ 标的技术 Agent
       ├ 标的基本面 Agent
       └ 标的资金面 Agent
              ↓
         汇总 Agent → 回复
```
用于 "茅台 vs 五粮液对比" 类任务。

#### 3.2 实现方式
- 不引入 LangGraph（太重）
- 在 Agent Loop 内实现简单的子任务分发
- 每个子 Agent 共享同一个 LLM client，但有独立的系统提示词和工具集

---

### Phase 4：前端升级 —— 对标 Vibe-Trading UI

**目标：** 让 Dashboard 不只是展示，而是交互式的 AI 对话 + 可视化

#### 4.1 现有前端评估
market-agents 已有 Vue 3 前端（总览/行业/标的/模拟盘/持仓/组合页），不足：
- 没有聊天界面
- 没有多轮对话历史
- 没有 Skill 管理界面
- 没有回测对比页面

#### 4.2 升级方向
- 新增 **AI 对话面板**（左侧对话 + 右侧图表，类似 Vibe-Trading Home 页）
- 新增 **回测 Run 详情页**（对标 Vibe-Trading RunDetail）
- 新增 **策略对比页**（对标 Vibe-Trading Compare）
- 新增 **NMF 可视化**（agent 活动热力图、状态转移图）
- 保持 Vue 3 技术栈（不动摇）

---

### Phase 5：工程化完善 —— 让项目「拿来就能跑」

**目标：** 达到 "clone 下来就能跑" 的交付标准

- 完善 pyproject.toml（参考 Vibe-Trading 的 extras 结构）
- 添加测试（至少覆盖 Agent Loop、工具调用、回测引擎）
- 完善 README（中英文）
- Docker 一键部署
- 环境变量管理（.env 模板）
- CI/CD

---

## 4. 差异化竞争点（vs Vibe-Trading）

| 能力 | Vibe-Trading | Cardinal（融合后） |
|------|:---:|:---:|
| A 股市场规则深度 | 通用 | **T+1/涨跌停/印花税/ST 全支持** |
| NMF 微观结构 | ❌ | **核心能力** |
| Agent 仿真 | ❌ | **5 类 Agent 群体行为模拟** |
| 市场状态分类 | ❌ | **GMM/HMM 双引擎** |
| 因子库 | 460 alpha | 精选 A 股有效因子（不走量） |
| 缠论 | 有 SKILL | 可对接 market-predictor 的缠论引擎 |
| LLM 解释 | 通用 | **残差异常的 LLM 解释（已有）** |
| 上下文压缩 | 5 层 | **保留 Cardinal L1-L5 + 迭代更新** |

---

## 5. 技术决策

### 5.1 不引入的

| 决策 | 理由 |
|------|------|
| 不引入 LangChain/LangGraph | 依赖太重，Agent Loop 自己写足够；现有代码无此依赖 |
| 不迁到 React | Vue 3 已就绪，前端重写成本高且无收益 |
| 不照搬 Vibe-Trading 的因子库 | 460 因子对 A 股有效的不多，精选优于堆量 |
| 不引入 IM 通道（Phase 1-4） | 目前无即时通讯需求，后期可加 |

### 5.2 可以借鉴的

| 借鉴点 | 来源 | 做法 |
|--------|------|------|
| SKILL.md 格式 | Vibe-Trading | 每个 Skill 一个目录，含 SKILL.md + references/ |
| 上下文压缩 | Cardinal + Vibe-Trading | 融合两者设计 |
| 回测沙箱 | Vibe-Trading | 子进程隔离执行（已有 Runner） |
| 前端布局 | Vibe-Trading | 对话面板 + 图表区域的布局 |
| API Server 模块化 | Vibe-Trading | 路由拆分，避免单体 api_server.py |

---

## 6. 风险与注意事项

1. **过度工程化风险：** 不要一开始就做 Swarm/IM/MCP，先让单 Agent 跑通
2. **兼容性风险：** 改造 Agent Loop 时不要破坏现有的批处理/回测流程
3. **依赖膨胀风险：** 严格控制新增依赖，保持 pyproject.toml 清爽
4. **提示词维护：** Cardinal 的系统提示词很长（366行），要持续迭代精简

---

## 7. 下一步行动

建议立即启动 **Phase 1**：

1. 在 market-agents 项目下创建 `后端/智能体/` 目录
2. 迁移 Cardinal 的 system prompt
3. 重写 Agent Loop（基于现有的循环逻辑 + Cardinal/Vibe-Trading 的上下文管理）
4. 封装 3-5 个首批工具
5. 跑通第一个端到端对话

**预计工作量：** Phase 1 约 2-3 天（核心逻辑），后续每 Phase 约 1-2 周
