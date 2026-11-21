-- Archipelago bridge tables. Apply to the AzerothCore *characters* database
-- (default name: acore_characters). Eluna reads/writes them via CharDBQuery/CharDBExecute,
-- the Python bridge via MySQL.

-- One row per character linked to an Archipelago slot. Written by the bridge.
CREATE TABLE IF NOT EXISTS `ap_state` (
  `char_guid`     INT UNSIGNED     NOT NULL,
  `seed_name`     VARCHAR(64)      NOT NULL DEFAULT '',
  `slot_name`     VARCHAR(64)      NOT NULL DEFAULT '',
  `connected`     TINYINT UNSIGNED NOT NULL DEFAULT 0,
  `checked_count` INT UNSIGNED     NOT NULL DEFAULT 0,
  `total_count`   INT UNSIGNED     NOT NULL DEFAULT 0,
  `goal_level`    TINYINT UNSIGNED NOT NULL DEFAULT 0,
  `updated_at`    TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`char_guid`)
);

-- Checks found in game. Written by Eluna, consumed by the bridge (sets sent=1).
CREATE TABLE IF NOT EXISTS `ap_location_outbox` (
  `char_guid`   INT UNSIGNED     NOT NULL,
  `location_id` INT UNSIGNED     NOT NULL,
  `sent`        TINYINT UNSIGNED NOT NULL DEFAULT 0,
  `created_at`  TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`char_guid`, `location_id`),
  KEY `idx_unsent` (`char_guid`, `sent`)
);

-- Items received from Archipelago. Written by the bridge, consumed by Eluna (sets granted=1).
-- ap_index is Archipelago's ReceivedItems index, so each item is granted exactly once.
CREATE TABLE IF NOT EXISTS `ap_item_inbox` (
  `char_guid`   INT UNSIGNED     NOT NULL,
  `ap_index`    INT UNSIGNED     NOT NULL,
  `item_id`     INT UNSIGNED     NOT NULL,
  `item_name`   VARCHAR(128)     NOT NULL DEFAULT '',
  `sender_name` VARCHAR(64)      NOT NULL DEFAULT '',
  `flags`       TINYINT UNSIGNED NOT NULL DEFAULT 0,
  `granted`     TINYINT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (`char_guid`, `ap_index`),
  KEY `idx_pending` (`char_guid`, `granted`)
);

-- Every location in the slot, with its display name and checked status. Written by the
-- bridge; Eluna forwards it to the addon tracker and uses quest rows for its sweep.
CREATE TABLE IF NOT EXISTS `ap_locations` (
  `char_guid`   INT UNSIGNED     NOT NULL,
  `location_id` INT UNSIGNED     NOT NULL,
  `name`        VARCHAR(128)     NOT NULL DEFAULT '',
  `checked`     TINYINT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (`char_guid`, `location_id`)
);
