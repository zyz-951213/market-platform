# TASK-001 — 把内嵌 git 仓库 packages/{agents,predictor} 并入 market-platform

状态: 待办(第一个真实任务,用于验证任务卡流程)

## 范围(只动这些)
- `packages/agents/.git`、`packages/predictor/.git`(仓库结构)
- 必要时新增 `packages/*/.gitignore` 内的 node_modules 规则

## 契约(是否变化)
- 无业务契约变化(纯仓库/历史整理)。
- 若采用子模块方案 → 新增 `.gitmodules`,并回写根 `AGENTS.md §1`。

## 验收(可量化)
- [ ] `git ls-files packages/agents packages/predictor` 能列出两包源码(node_modules 除外)
- [ ] `git add -A` 不再出现 "embedded git repository" 警告
- [ ] `git status` 干净
- [ ] 两包各自旧历史有明确去向(保留或放弃,写在本卡结论里)

## 禁区(不许碰)
- 不改任何业务代码逻辑
- 不把 node_modules / .env 提交进库

## 决策(二选一,先定再动手)
- **A 折叠成一库(推荐,单人平台省心)**:删掉两包内嵌 `.git` → 源码直接进主库。
  想保旧历史:`git filter-repo` 导出后 `git subtree add`(按需,非必须)。
- **B 保留为子模块**:需两包各有远端仓库 URL;当前若未推远端,先建远端。

## 上下文预算(动手前声明)
- 拟读:`git log`(两包历史长度)、`git remote -v`(是否有远端)、根 `.gitignore`
