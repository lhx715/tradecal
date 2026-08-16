# ☁️ TradeCal 免费云端部署（GitHub Actions + Pages）

零成本方案：**GitHub Actions 每天定时抓数据 + 发邮件**，**GitHub Pages 托管 Web 面板**。
GitHub 服务器在海外，访问 Yahoo 数据无需代理。

---

## 架构

```
GitHub Actions（每天 08:00 北京时间自动运行）
   ├── fetch_cloud.py 抓取数据（行情/财报/宏观/新闻）
   ├── 生成摘要 → SMTP 发邮件到你的邮箱
   └── 更新 deploy/data.json → 提交回仓库
        ↓
GitHub Pages 托管 Web 面板 → 手机随时访问
```

---

## 部署步骤（约 10 分钟）

### 第 1 步：创建 GitHub 仓库

1. 登录 https://github.com ，点 **New repository**
2. 仓库名随便（如 `tradecal`），**Public**（Pages 免费需要）
3. 创建后，把 `deploy/` 里的文件上传到仓库根目录的 `deploy/` 文件夹下：
   - `fetch_cloud.py`
   - `workflow-daily.yml` → 放到 `.github/workflows/daily.yml`
   - `index.html`、`manifest.json`（放 `deploy/` 或根目录，见第 4 步）

### 第 2 步：配置邮箱密钥（Secrets）

仓库页面 → **Settings → Secrets and variables → Actions → New repository secret**，
添加以下 5 个（值填你自己的邮箱信息）：

| Secret 名 | 值 |
|---|---|
| `SMTP_HOST` | 如 `smtp.qq.com` / `smtp.163.com` / `smtp.gmail.com` |
| `SMTP_PORT` | QQ/163 用 `465`，Gmail 用 `587` |
| `SMTP_USER` | 你的邮箱地址 |
| `SMTP_PASS` | SMTP 授权码（**不是登录密码**！QQ/163 需在邮箱设置里开启 SMTP 并生成授权码） |
| `SMTP_TO` | 接收邮件的邮箱（可填自己） |
| `SMTP_SSL` | `1`（465 端口）或 `0`（587 端口用 STARTTLS） |

### 第 3 步：首次手动运行

仓库页面 → **Actions** 标签 → 左侧选中 **TradeCal Daily Digest** → **Run workflow** → 运行一次。

运行成功后你会收到第一封 TradeCal 邮件！✅

### 第 4 步：开启 GitHub Pages（Web 面板）

仓库页面 → **Settings → Pages** →
- Source 选 **Deploy from a branch** → 分支 `main` → 目录 `/`（或 `/deploy`，取决于 index.html 放哪）
- Save

等 1~2 分钟，访问 `https://<你的用户名>.github.io/<仓库名>/` 就能看到面板。

### 第 5 步：确认定时任务

Actions 页面 → **TradeCal Daily Digest** → 看是否有 **scheduled** 触发的运行记录。
定时规则：`cron: '0 0 * * *'` = 每天 00:00 UTC = **北京时间 08:00**。

---

## 验证清单

- [ ] 首次 Run workflow 成功（Actions 绿色 ✓）
- [ ] 收到第一封邮件（内容含行情/财报/新闻）
- [ ] Pages 地址能打开，显示数据
- [ ] 仓库里 `deploy/data.json` 有更新记录（commit 历史）

---

## 常见问题

| 问题 | 解决 |
|---|---|
| 邮件发不出去，报 535/认证失败 | 授权码不对，重新生成；确认 SMTP_PORT 与 SSL 匹配 |
| Gmail 需要应用专用密码 | 开启两步验证后，用 App Password |
| Actions 运行失败 | 看 Actions 日志；通常是 Secrets 没配全 |
| 想改发送时间 | 编辑 workflow 的 `cron: '0 0 * * *'`（UTC 时间，08:00 北京 = 00:00 UTC） |
| 免费额度 | GitHub Actions 免费 2000 分钟/月，这个任务每次约 1 分钟，绰绰有余 |

---

## 修改内容

- 财报/宏观数据：编辑 `fetch_cloud.py` 里的 `EARNINGS` / `MACRO`
- 利好利空关键词：编辑 `fetch_cloud.py` 里的 `BULLISH_WORDS` / `BEARISH_WORDS`
- 想加更多股票新闻：编辑 `fetch_cloud.py` 里 `build_snapshot` 的 ticker 列表
