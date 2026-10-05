# TASK-001 — 把内嵌 git 仓库 packages/{agents,predictor} 并入 market-platform

状态: **已完成**(2026-10-05,commit `21c1b6e`)

## 结论(决策 A:折叠成一库)

1. **历史去向:保留,但以"归档引用"形式**。删 `.git` 之前先把两仓执行
   `git fetch ./packages/<pkg> 'refs/heads/*:refs/archive/<pkg>/*'`,对象进入主库对象库:
   - `refs/archive/market-agents/main` → `9dd9ed1`(重构为中文目录结构,并迁移 Vue 3 前端)
   - `refs/archive/market-predictor/main` → `74a74d5`(Point repository URLs to …)
   其中 `9dd9ed1` 是**未推送到 GitHub 的本地提交**,已随本次归档保住。
   需要时可 `git show refs/archive/market-agents/main` 或 `git subtree add` 复盘。
2. **未采用子模块**(决策 B):两包 origin 虽在 GitHub,但 `packages/agents` 存在未推送提交,
   子模块会引入"远端为准"的隐患;单人平台下折叠更省心。
3. **验收结果**:
   - `git ls-files packages/agents packages/predictor` → 89 个文件(源码/前端/文档/脚本/测试)✓
   - `git add -A` 无 `embedded git repository` 警告 ✓
   - `git status` 干净 ✓
   - `node_modules/`(20,270 个文件)与 `dist/`、`*.log` 均按 .gitignore 排除 ✓
4. **顺带加固**:`.gitignore` 增补 `*.tsbuildinfo`、`.pnpm-store/`、`.ruff_cache/`、`.hermes/`
   与运行态产物规则(`数据/raw|cache|processed`、`模型/检查点/*.pt`、`outputs/`)。

## 原始卡片(留档)

## 范围(只动这些)
- `packages/agents/.git`、`packages/predictor/.git`(仓库结构)
- 必要时新增 `packages/*/.gitignore` 内的 node_modules 规则

## 契约(是否变化)
- 无业务契约变化(纯仓库/历史整理)。
- 若采用子模块方案 → 新增 `.gitmodules`,并回写根 `AGENTS.md §1`。

## 验收(可量化)
- [x] `git ls-files packages/agents packages/predictor` 能列出两包源码(node_modules 除外)
- [x] `git add -A` 不再出现 "embedded git repository" 警告
- [x] `git status` 干净
- [x] 两包各自旧历史有明确去向(保留或放弃,写在本卡结论里)

## 禁区(不许碰)
- 不改任何业务代码逻辑
- 不把 node_modules / .env 提交进库

## 决策(二选一,先定再动手)
- **A 折叠成一库(推荐,单人平台省心)**:删掉两包内嵌 `.git` → 源码直接进主库。
  想保旧历史:`git filter-repo` 导出后 `git subtree add`(按需,非必须)。
- **B 保留为子模块**:需两包各有远端仓库 URL;当前若未推远端,先建远端。

## 上下文预算(动手前声明)
- 拟读:`git log`(两包历史长度)、`git remote -v`(是否有远端)、根 `.gitignore`

## 范围(只动这些)
- `packages/agents/.git`、`packages/predictor/.git`(仓库结构)
- 必要时新增 `packages/*/.gitignore` 内的 node_modules 规则

## 契约(是否变化)
- 无业务契约变化(纯仓库/历史整理)。
- 若采用子模块方案 → 新增 `.gitmodules`,并回写根 `AGENTS.md §1`。

## 禁区(不许碰)
- 不改任何业务代码逻辑
- 不把 node_modules / .env 提交进库

## 决策(二选一,先定再动手)
- **A 折叠成一库(推荐,单人平台省心)**:删掉两包内嵌 `.git` → 源码直接进主库。
  想保旧历史:`git filter-repo` 导出后 `git subtree add`(按需,非必须)。
- **B 保留为子模块**:需两包各有远端仓库 URL;当前若未推远端,先建远端。

## 上下文预算(动手前声明)
- 拟读:`git log`(两包历史长度)、`git remote -v`(是否有远端)、根 `.gitignore`
