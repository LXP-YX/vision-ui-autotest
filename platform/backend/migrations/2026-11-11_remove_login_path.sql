-- ============================================================
-- 迁移：从 test_env 表删除 login_path 字段
--
-- 背景：login_path 已废弃，登录路径已并入 base_url。
--       录制和执行直接打开 base_url 即可。
--
-- 执行步骤：
--   mysql -u root -p vision_ui_autotest < platform/backend/migrations/2026-11-11_remove_login_path.sql
-- ============================================================

USE `vision_ui_autotest`;

-- 删除 login_path 字段
ALTER TABLE `test_env` DROP COLUMN `login_path`;

-- 验证字段已删除
SELECT
  COLUMN_NAME,
  DATA_TYPE,
  IS_NULLABLE,
  COLUMN_DEFAULT
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'vision_ui_autotest'
  AND TABLE_NAME = 'test_env'
  AND COLUMN_NAME = 'login_path';

-- 如果查询结果为空，说明删除成功
-- ============================================================
