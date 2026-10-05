# 统一站点 —— 一个入口、三个域

```
web/
├─ index.html          站点壳(挂载 Vue 应用)
├─ 源码/               壳代码:入口.ts / 应用.vue / 域登记.ts / 资源/
├─ vite.config.ts      仅壳的构建配置(dev :18000)
├─ 构建.mjs            一次构建:壳 + 三域子应用 → dist/
└─ 模块/
   ├─ 数据底座/         原 database 前端(原生 JS + Vite)
   ├─ 行为分析/         原 market-agents 前端(Vue3 + naive-ui + pinia)
   └─ 指数预测/         原 market-predictor 前端(原生 JS + Vite)
```

## 构建

```bash
cd web
node 构建.mjs              # 全部:dist/index.html + dist/{数据底座,行为分析,指数预测}/
node 构建.mjs 行为分析      # 只构建某域
node 构建.mjs --壳          # 只构建站点壳
```

产物 `web/dist/` 由统一门户(`market-platform web`)托管,**不入库**。

> 沙箱/离线环境下 `npm install` 不可用:构建脚本直接调用
> `web/模块/行为分析/node_modules/vite/bin/vite.js`;壳依赖用 `web/node_modules/` 里的软链复用同一份。

## 三个域如何拼到一个站点

1. 站点壳(顶栏导航 + 容器)内嵌三个子应用页面,地址分别是 `/数据底座/`、`/行为分析/`、`/指数预测/`。
2. 每个子应用以 `--base=/<域>/` 构建,静态资源因此带域前缀。
3. 接口前缀通过构建期环境变量注入:`VITE_API_BASE=/<域>`。三个域的 API 客户端本来就读取
   `import.meta.env.VITE_API_BASE`,**业务页面代码零改动**。
4. 统一门户把三个 FastAPI 应用分别挂到 `/<域>`,于是 `/<域>/api/health` 直达对应域,
   页面与接口同源、同端口、同进程。

## 开发模式

- 站点壳:`cd web && ./node_modules/.bin/vite`(:18000),壳内嵌各域自己的 dev server
  (数据底座 :19081、行为分析 :8080、指数预测 :19080;地址见 `源码/域登记.ts`)。
- 单域开发:进入 `web/模块/<域>/`,`node_modules/.bin/vite` 直接跑该域(各自配置保留着 dev 代理)。

## 增删域

改 `web/源码/域登记.ts`(名称/端口/开发地址/主题色)与 `web/构建.mjs` 的 `域列表`,
并在 `src/market_platform/门户/应用.py` 的 `域装配表` 中登记该域应用工厂。
