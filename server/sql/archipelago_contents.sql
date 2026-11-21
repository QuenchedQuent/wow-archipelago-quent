-- What each location holds (from Archipelago LocationScouts), shown by the addon.
-- Characters DB (acore_characters). Idempotent.

CREATE TABLE IF NOT EXISTS `ap_location_contents` (
  `char_guid`   INT UNSIGNED     NOT NULL,
  `location_id` INT UNSIGNED     NOT NULL,
  `contents`    VARCHAR(160)     NOT NULL DEFAULT '',
  `flags`       TINYINT UNSIGNED NOT NULL DEFAULT 0,   -- AP item flags: 1 progression, 2 useful, 4 trap
  PRIMARY KEY (`char_guid`, `location_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
