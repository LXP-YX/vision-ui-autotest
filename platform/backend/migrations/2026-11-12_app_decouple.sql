-- ============================================================
-- 迁移：WEB / APP 自动化解耦 —— 自愈知识库拆分 + APP 测试环境
--
-- 背景：
--   1) 自愈知识库表级拆分：locator 只存 WEB（Playwright 选择器），
--      新建 app_locator 存 APP（{"by","value"} JSON），同名锚点不再互撞；
--      两张表都加 env_id 环境 维度（WEB→test_env.id，APP→app_env.id）。
--   2) 新建 app_env 表：APP 测试环境（平台 + 设备 + 被测 App + 可选账号），
--      test_case 加 app_env_id 绑定环境；device_serial 保留为临时手动覆盖。
--
-- 执行步骤（重复执行安全：已存在的列/表报错可跳过，MySQL 不支持 IF NOT EXISTS 补列）：
--   mysql -u root -p vision_ui_autotest < platform/backend/migrations/2026-11-12_app_decouple.sql
-- ============================================================

USE `vision_ui_autotest`;

-- 1. locator（WEB 自愈知识库）加环境维度
ALTER TABLE `locator` ADD COLUMN `env_id` INT NULL COMMENT '关联 test_env.id，NULL=全局通用';

-- 2. test_case 绑定 APP 测试环境
ALTER TABLE `test_case` ADD COLUMN `app_env_id` INT NULL COMMENT '关联 app_env.id（APP 测试环境）';

-- 3. APP 自愈知识库表
CREATE TABLE IF NOT EXISTS `app_locator` (
  `id`         INT NOT NULL AUTO_INCREMENT,
  `anchor`     VARCHAR(300) NOT NULL COMMENT '自然语言锚点描述',
  `selector`   VARCHAR(500) DEFAULT '' COMMENT '结构化定位 JSON {"by","value"}',
  `strategy`   VARCHAR(20) DEFAULT 'store' COMMENT 'store|xml|vlm',
  `platform`   VARCHAR(10) NULL COMMENT 'android|ios，NULL=平台通用',
  `env_id`     INT NULL COMMENT '关联 app_env.id，NULL=全局通用',
  `hit_count`  INT DEFAULT 0,
  `fail_count` INT DEFAULT 0,
  `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_app_loc_anchor` (`anchor`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='APP 脚本自愈知识库';

-- 4. APP 测试环境表
CREATE TABLE IF NOT EXISTS `app_env` (
  `id`           INT NOT NULL AUTO_INCREMENT,
  `name`         VARCHAR(100) NOT NULL COMMENT '环境名',
  `platform`     VARCHAR(10) DEFAULT 'android' COMMENT 'android|ios',
  `device_id`    VARCHAR(100) DEFAULT '' COMMENT '设备序号 serial / iOS 设备标识 udid',
  `app_id`       VARCHAR(200) DEFAULT '' COMMENT '被测 App：package / bundle_id',
  `app_activity` VARCHAR(200) DEFAULT '' COMMENT 'Android 启动 Activity，iOS 留空',
  `username`     VARCHAR(100) DEFAULT '' COMMENT '可选登录账号',
  `password`     VARCHAR(200) DEFAULT '' COMMENT '可选登录密码',
  `remark`       VARCHAR(300) DEFAULT '' COMMENT '备注',
  `is_default`   TINYINT(1) DEFAULT 0 COMMENT '是否默认环境',
  `created_at`   DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_at`   DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='APP 测试环境';

-- 5. 存量迁移：locator 里旧 APP 数据（selector 为 {"by","value"} JSON）搬到 app_locator，
--    并从 locator 删除，保证 WEB 知识库干净。APP 侧按锚点重新学习后即恢复。
INSERT INTO `app_locator` (`anchor`, `selector`, `strategy`, `platform`, `env_id`,
                           `hit_count`, `fail_count`, `updated_at`)
SELECT `anchor`, `selector`, `strategy`, NULL, NULL,
       `hit_count`, `fail_count`, `updated_at`
FROM `locator`
WHERE JSON_VALID(`selector`)
  AND JSON_EXTRACT(`selector`, '$.by') IS NOT NULL;

DELETE FROM `locator`
WHERE JSON_VALID(`selector`)
  AND JSON_EXTRACT(`selector`, '$.by') IS NOT NULL;

-- 验证：查询结果应包含 locator.env_id / test_case.app_env_id / app_locator / app_env
SELECT TABLE_NAME, COLUMN_NAME
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'vision_ui_autotest'
  AND ((TABLE_NAME = 'locator' AND COLUMN_NAME = 'env_id')
    OR (TABLE_NAME = 'test_case' AND COLUMN_NAME = 'app_env_id'));
SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = 'vision_ui_autotest' AND TABLE_NAME IN ('app_env', 'app_locator');
-- ============================================================
