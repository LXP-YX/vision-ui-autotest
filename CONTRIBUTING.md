# 贡献指南

感谢关注 vision-ui-autotest！

## 开发准备

```bash
pip install -r requirements.txt
#   （Homebrew 等 PEP 668 管控环境：pip install --break-system-packages -r requirements.txt）
python3 -m playwright install chromium
cd platform/frontend && npm install && cd ../..
cp config.example.yaml config.yaml   # 按你的测试站点填写，config.yaml 不会入库
```

## 提交前自查

1. `python3 -m pytest` 通过（需要可登录的测试站点）；
2. `cd platform/frontend && npm run build` 通过；
3. `git status` 确认没有把 `config.yaml`、`.env`、`*.db`、`screenshots/` 等本地文件加入提交；
4. 代码中不出现任何真实账号、密码、密钥、内网地址。

## 代码约定

- 注释 / docstring 用中文，代码与标识符用英文；
- 严禁 `time.sleep` 做同步，一律用 Playwright 状态等待；
- 配置项不硬编码：新增配置同步维护 `config.example.yaml`；
- 定位语义优先（role/文本/label），不依赖 hash 压缩类名；
- 用例守只读原则：只查看/搜索/断言，不做破坏性操作；
- 数据库字段只增不改；涉及 DB 结构变更请在 `platform/backend/migrations/` 新增 SQL 脚本并说明。

## 提 PR

1. Fork 并建分支；
2. 小步提交，说明动机与影响面；
3. 关联相关 issue。

## 报告问题

提 issue 时请附：复现步骤、期望/实际行为、日志或截图、Python/Node 版本。**注意脱敏**：不要在 issue 中粘贴真实账号、密钥或公司内网地址。
