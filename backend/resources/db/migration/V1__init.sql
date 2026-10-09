-- Retrenchment Laptop Tracker schema (OceanBase / MySQL dialect).

CREATE TABLE departments (
    id          VARCHAR(64)  NOT NULL,
    name        VARCHAR(80)  NOT NULL,
    sort_order  INT          NOT NULL DEFAULT 0,
    created_by  VARCHAR(255) NOT NULL DEFAULT '',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE laptops (
    id             BIGINT       NOT NULL AUTO_INCREMENT,
    dept_id        VARCHAR(64)  NOT NULL,
    employee_name  VARCHAR(255) NOT NULL DEFAULT '',
    employee_id    VARCHAR(64)  NOT NULL DEFAULT '',
    sub_dept       VARCHAR(255) NOT NULL DEFAULT '',
    manufacturer   VARCHAR(80)  NOT NULL DEFAULT '',
    model          VARCHAR(120) NOT NULL DEFAULT '',
    hostname       VARCHAR(80)  NOT NULL DEFAULT '',
    inventory_tag  VARCHAR(80)  NOT NULL DEFAULT '',
    serial         VARCHAR(120) NOT NULL DEFAULT '',
    return_status  VARCHAR(20)  NOT NULL DEFAULT 'Pending',
    date_returned  VARCHAR(10)  NOT NULL DEFAULT '',
    snipeit        VARCHAR(40)  NOT NULL DEFAULT '',
    win11          VARCHAR(10)  NOT NULL DEFAULT '',
    win11_version  VARCHAR(40)  NOT NULL DEFAULT '',
    remarks        TEXT,
    sort_order     INT          NOT NULL DEFAULT 0,
    updated_by     VARCHAR(255) NOT NULL DEFAULT '',
    updated_at     DATETIME     NULL,
    version        INT          NOT NULL DEFAULT 1,
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_laptops_dept (dept_id)
) DEFAULT CHARSET=utf8mb4;

INSERT INTO departments (id, name, sort_order) VALUES ('rtm', 'RTM', 1);
INSERT INTO departments (id, name, sort_order) VALUES ('ninjamart', 'Ninja Mart', 2);
