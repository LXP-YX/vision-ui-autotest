-- ============================================================
-- vision-ui-autotest 自动化测试平台 —— MySQL 建表脚本
-- 手动执行步骤：
--   1) 创建数据库：
--        CREATE DATABASE `vision_ui_autotest` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
--   2) 执行本脚本：
--        mysql -u root -p `vision_ui_autotest` < platform/backend/schema.sql
--   3) 在 config.yaml 的 database 段填连接信息并置 enabled: true
--
-- 说明：应用启动时也会用 SQLAlchemy 自动建表（create_all），
--       本脚本用于需要手动/受控建表的场景（如生产、DBA 审核）。
-- 字段约定：只增不改现有字段，不含账户敏感数据。
-- ============================================================

SET NAMES utf8mb4;

-- 用例表
CREATE TABLE IF NOT EXISTS `test_case` (
  `id`          INT NOT NULL AUTO_INCREMENT,
  `name`        VARCHAR(200) NOT NULL COMMENT '用例名',
  `module`      VARCHAR(100) DEFAULT '' COMMENT '所属模块',
  `tags`        JSON NULL COMMENT '标签数组',
  `steps`       JSON NULL COMMENT '中文步骤数组',
  `allow_risky` TINYINT(1) DEFAULT 0 COMMENT '是否允许破坏性操作',
  `status`      VARCHAR(20) DEFAULT 'active' COMMENT 'draft|active',
  `source`      VARCHAR(30) DEFAULT 'human' COMMENT 'human|ai_*',
  `script`      JSON NULL COMMENT '结构化执行脚本（编译产物），null=未编译',
  `script_updated_at` DATETIME NULL COMMENT '脚本编译/自愈时间',
  `created_at`  DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_at`  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='自然语言测试用例';

-- 运行记录表
CREATE TABLE IF NOT EXISTS `test_run` (
  `id`           INT NOT NULL AUTO_INCREMENT,
  `case_id`      INT NOT NULL COMMENT '关联用例 id',
  `mode`         VARCHAR(20) DEFAULT 'visual',
  `status`       VARCHAR(20) DEFAULT 'queued' COMMENT 'queued|running|passed|failed|error',
  `step_results` JSON NULL COMMENT '各步骤结果',
  `screenshots`  JSON NULL COMMENT '失败截图相对路径数组',
  `error`        TEXT NULL,
  `started_at`   DATETIME NULL,
  `ended_at`     DATETIME NULL,
  PRIMARY KEY (`id`),
  KEY `idx_case_id` (`case_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用例运行记录';

-- 大模型配置表
CREATE TABLE IF NOT EXISTS `llm_config` (
  `id`           INT NOT NULL AUTO_INCREMENT,
  `name`         VARCHAR(100) NOT NULL COMMENT '配置别名',
  `base_url`     VARCHAR(300) DEFAULT '' COMMENT 'OpenAI 兼容端点',
  `api_key`      VARCHAR(300) DEFAULT '' COMMENT '密钥密文（Fernet 加密，前缀 enc:v1:）',
  `chat_model`   VARCHAR(100) DEFAULT '' COMMENT '对话模型名',
  `vision_model` VARCHAR(100) DEFAULT '' COMMENT '多模态模型名',
  `timeout`      INT DEFAULT 60,
  `verify_ssl`   TINYINT(1) DEFAULT 1,
  `is_default`   TINYINT(1) DEFAULT 0 COMMENT '是否默认配置',
  `enabled`      TINYINT(1) DEFAULT 1,
  `created_at`   DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_at`   DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大模型配置（页面维护）';

-- 自愈知识库表（自然语言锚点 → 稳定选择器）
CREATE TABLE IF NOT EXISTS `locator` (
  `id`         INT NOT NULL AUTO_INCREMENT,
  `anchor`     VARCHAR(300) NOT NULL COMMENT '自然语言锚点描述',
  `selector`   VARCHAR(500) DEFAULT '' COMMENT '稳定选择器',
  `strategy`   VARCHAR(20) DEFAULT 'store' COMMENT 'store|semantic|vlm',
  `hit_count`  INT DEFAULT 0,
  `fail_count` INT DEFAULT 0,
  `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_anchor` (`anchor`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='脚本自愈知识库';

-- 接口库表（可复用的单个接口定义）
CREATE TABLE IF NOT EXISTS `api_definition` (
  `id`         INT NOT NULL AUTO_INCREMENT,
  `name`       VARCHAR(200) NOT NULL COMMENT '接口名',
  `module`     VARCHAR(100) DEFAULT '' COMMENT '所属模块',
  `method`     VARCHAR(10) DEFAULT 'GET' COMMENT '请求方法',
  `path`       VARCHAR(500) DEFAULT '' COMMENT '相对 base_url 或完整 URL',
  `headers`    JSON NULL COMMENT '请求头 {k:v}',
  `params`     JSON NULL COMMENT 'query 参数 {k:v}',
  `body`       TEXT NULL COMMENT '请求体（支持 ${var}）',
  `body_type`  VARCHAR(10) DEFAULT 'json' COMMENT 'json|form|raw',
  `extractors` JSON NULL COMMENT '提取器 [{name, jsonpath}]',
  `assertions` JSON NULL COMMENT '断言 [{type, ...}]',
  `retry_interval` FLOAT DEFAULT 1.0 COMMENT '断言失败重试间隔（秒）',
  `retry_max`   INT DEFAULT 1 COMMENT '请求次数上限（含首次，1=不重试）',
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='接口库（可复用接口）';

-- 环境级全局变量模板表（绑定 test_env，场景运行变量初始值）
CREATE TABLE IF NOT EXISTS `api_global_var` (
  `id`          INT NOT NULL AUTO_INCREMENT,
  `env_id`      INT NOT NULL COMMENT '关联 test_env.id',
  `name`        VARCHAR(100) NOT NULL COMMENT '变量名',
  `value`       TEXT NULL COMMENT '变量值',
  `module`      VARCHAR(100) DEFAULT '' COMMENT '所属模块',
  `description` VARCHAR(300) DEFAULT '' COMMENT '说明',
  `updated_at`  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_env_id` (`env_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='环境级全局变量模板';

-- 接口场景表（编排多接口：前置步骤 + 主步骤，v1 串行）
CREATE TABLE IF NOT EXISTS `api_scenario` (
  `id`          INT NOT NULL AUTO_INCREMENT,
  `name`        VARCHAR(200) NOT NULL COMMENT '场景名',
  `module`      VARCHAR(100) DEFAULT '' COMMENT '所属模块',
  `description` VARCHAR(500) DEFAULT '' COMMENT '描述',
  `setup_steps` JSON NULL COMMENT '前置步骤 [{api_id, name, group}]',
  `steps`       JSON NULL COMMENT '主步骤 [{api_id, name, group}]',
  `status`      VARCHAR(20) DEFAULT 'active' COMMENT 'draft|active',
  `created_at`  DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_at`  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='接口场景（多接口编排）';

-- 接口场景运行记录表
CREATE TABLE IF NOT EXISTS `api_scenario_run` (
  `id`           INT NOT NULL AUTO_INCREMENT,
  `scenario_id`  INT NOT NULL COMMENT '关联场景 id',
  `status`       VARCHAR(20) DEFAULT 'queued' COMMENT 'queued|running|passed|failed|error',
  `step_results` JSON NULL COMMENT '各步骤结果',
  `variables`    JSON NULL COMMENT '运行结束时变量快照',
  `error`        TEXT NULL,
  `started_at`   DATETIME NULL,
  `ended_at`     DATETIME NULL,
  PRIMARY KEY (`id`),
  KEY `idx_scenario_id` (`scenario_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='接口场景运行记录';

-- 测试环境表（多套测试数据，页面维护，全落库）
CREATE TABLE IF NOT EXISTS `test_env` (
  `id`         INT NOT NULL AUTO_INCREMENT,
  `name`       VARCHAR(100) NOT NULL COMMENT '环境名',
  `base_url`   VARCHAR(300) DEFAULT '' COMMENT '测试站点 URL',
  `login_path` VARCHAR(100) DEFAULT '/login' COMMENT '登录路径',
  `username`   VARCHAR(100) DEFAULT '' COMMENT '测试账号',
  `password`   VARCHAR(200) DEFAULT '' COMMENT '测试密码',
  `is_default` TINYINT(1) DEFAULT 0 COMMENT '是否默认环境',
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='测试环境（多套测试数据）';

-- ============================================================
-- 用例执行模板：把若干条用例编排成一次批量运行
-- 注意：使用 LONGBLOB 存截图，请确认 my.cnf 的 max_allowed_packet >= 64M
-- ============================================================

-- 执行模板表（物理删，级联删对应 execution_run/case_result/screenshot）
CREATE TABLE IF NOT EXISTS `execution_template` (
  `id`           INT NOT NULL AUTO_INCREMENT,
  `name`         VARCHAR(100) NOT NULL COMMENT '模板名',
  `case_ids`     JSON NULL COMMENT '用例 id 数组，按顺序串行执行',
  `on_failure`   VARCHAR(20) DEFAULT 'continue' COMMENT 'continue|abort',
  `case_timeout` INT DEFAULT 180 COMMENT '单条用例超时（秒）',
  `description`  VARCHAR(300) DEFAULT '',
  `created_at`   DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_at`   DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='执行模板';

-- 执行记录表（逻辑删除，超 7 天由列表接口触发物理清理）
CREATE TABLE IF NOT EXISTS `execution_run` (
  `id`                INT NOT NULL AUTO_INCREMENT,
  `template_id`       INT NULL COMMENT '关联 execution_template.id，删模板后置 null',
  `template_name`     VARCHAR(100) DEFAULT '' COMMENT '冗余名字段',
  `template_snapshot` JSON NULL COMMENT '模板 + 用例全量快照（时间点冻结）',
  `status`            VARCHAR(20) DEFAULT 'queued' COMMENT 'queued|running|passed|failed|error',
  `summary`           JSON NULL COMMENT '{total,passed,failed,skipped,timeout}',
  `error`             TEXT NULL,
  `started_at`        DATETIME NULL,
  `ended_at`          DATETIME NULL,
  `is_deleted`        TINYINT(1) DEFAULT 0,
  `deleted_at`        DATETIME NULL,
  PRIMARY KEY (`id`),
  KEY `idx_exec_run_deleted` (`is_deleted`, `deleted_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='执行记录（软删）';

-- 单条用例结果表（跟随 run 物理删）
CREATE TABLE IF NOT EXISTS `execution_case_result` (
  `id`           INT NOT NULL AUTO_INCREMENT,
  `run_id`       INT NOT NULL COMMENT '关联 execution_run.id',
  `case_index`   INT DEFAULT 0 COMMENT '该 run 内顺序',
  `case_id`      INT NULL COMMENT '关联 test_case.id，删用例后置 null',
  `case_name`    VARCHAR(200) DEFAULT '',
  `status`       VARCHAR(20) DEFAULT 'queued' COMMENT 'queued|running|passed|failed|error|timeout|skipped',
  `error`        TEXT NULL,
  `step_results` JSON NULL,
  `logs`         JSON NULL COMMENT '[{level,text,ts}]',
  `started_at`   DATETIME NULL,
  `ended_at`     DATETIME NULL,
  PRIMARY KEY (`id`),
  KEY `idx_exec_case_run` (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用例执行结果';

-- 截图 BLOB 表（跟随 case_result 物理删）
CREATE TABLE IF NOT EXISTS `execution_screenshot` (
  `id`             INT NOT NULL AUTO_INCREMENT,
  `case_result_id` INT NOT NULL COMMENT '关联 execution_case_result.id',
  `name`           VARCHAR(200) DEFAULT '' COMMENT '原文件名',
  `data`           LONGBLOB NOT NULL COMMENT '图片二进制',
  `content_type`   VARCHAR(50) DEFAULT 'image/png',
  `created_at`     DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_exec_shot_result` (`case_result_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='执行截图（BLOB）';
