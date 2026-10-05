# Security Policy

## 报告漏洞

如发现安全问题，请通过 GitHub Issues 私下说明（勿在 Issue 中粘贴 API Key 或 `.env` 内容）。

## 密钥管理

- **切勿**将 `.env` 或真实 API Key 提交到仓库
- 仅提交 `.env.example` 作为模板
- DeepSeek API Key 通过环境变量 `DEEPSEEK_API_KEY` 注入
- 若 Key 曾意外泄露，请立即在 DeepSeek 控制台轮换

## 发布前检查

```bash
# 确认 .env 未被 git 跟踪
git status
git check-ignore -v .env

# 扫描仓库内是否残留密钥（示例）
grep -r "sk-" --include="*.py" --include="*.yaml" --include="*.md" . \
  | grep -v ".env.example"
```

## 免责声明

本项目仅供研究与学习，回测结果不构成投资建议。实盘使用需自行承担风险。
