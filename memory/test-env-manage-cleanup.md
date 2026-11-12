---
name: test-env-manage-cleanup
description: 测试环境管理页面改造，删除登录路径字段，菜单名已统一为「测试环境管理」
metadata:
  type: project
---

## 修改内容

### 1. schema.sql
- 删除 `test_env` 表中的 `login_path` 字段
- 保留 `base_url`、`username`、`password` 字段

### 2. 数据库迁移脚本
- 路径：`platform/backend/migrations/2026-11-11_remove_login_path.sql`
- 作用：从现有数据库中删除 `test_env.login_path` 列
- 执行方式：`mysql -u root -p vision_ui_autotest < platform/backend/migrations/2026-11-11_remove_login_path.sql`

### 3. 功能确认
- ✅ 菜单名称已是「测试环境管理」（路由和页面已统一）
- ✅ 编辑环境页面已无登录路径输入框
- ✅ 录制用例直接打开 `base_url`（recorder.py 已正确使用 base_url）
- ✅ 数据库字段 `login_path` 已废弃但保留用于兼容旧数据（待迁移后彻底删除）

## 如何应用

1. 手动执行 SQL 迁移脚本：
   ```bash
   mysql -u root -p vision_ui_autotest < platform/backend/migrations/2026-11-11_remove_login_path.sql
   ```

2. 验证字段已删除（查询结果应为空）：
   ```sql
   SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = 'vision_ui_autotest'
     AND TABLE_NAME = 'test_env'
     AND COLUMN_NAME = 'login_path';
   ```

3. 重新启动平台应用
