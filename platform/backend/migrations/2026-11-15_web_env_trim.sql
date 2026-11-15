-- ============================================================
-- 迁移：从 test_env 表删除 username / password 字段
--
-- 背景：web 环境已简化为「环境名 + 环境URL」，不再维护账号密码。
--       登录由站点会话 / 免登状态承担；默认环境 URL 是 web 录制与用例执行的起点。
--       （db.py 启动自动迁移对已连接的库执行同样的 DROP COLUMN；
--         本脚本供生产环境或其他未触发自动迁移的库手动执行。）
--
-- 执行步骤：
--   mysql -u root -p vision_ui_autotest < platform/backend/migrations/2026-11-15_web_env_trim.sql
-- ============================================================

USE `vision_ui_autotest`;

-- 删除账号 / 密码字段（IF EXISTS 兼容已删/未建列的情况）
ALTER TABLE `test_env` DROP COLUMN `username`;
ALTER TABLE `test_env` DROP COLUMN `password`;

-- 验证字段已删除（username / password 应不再出现）
SELECT
  COLUMN_NAME,
  DATA_TYPE,
  IS_NULLABLE,
  COLUMN_DEFAULT
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'vision_ui_autotest'
  AND TABLE_NAME = 'test_env'
  AND COLUMN_NAME IN ('username', 'password');

-- 如果查询结果为空，说明删除成功
-- ============================================================
