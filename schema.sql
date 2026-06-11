DROP DATABASE IF EXISTS `soap-cat-db`;

CREATE DATABASE `soap-cat-db`;

USE `soap-cat-db`;

CREATE TABLE
    donors (
        name VARCHAR(24) PRIMARY KEY,
        json_data VARCHAR(1536), -- a json without the titles section is always 1488 (movable without CMAC), 1536 is the next round number above 1488
        last_transferred INT UNSIGNED,
        uploader VARCHAR(18),
        note VARCHAR(128) DEFAULT "None",
        status INT NOT NULL
    );

/* status key
0 = ready for use
1 = manually disabled
2 = refresh last_transfer time
3 = automatically disabled due to error
4 = unprocessed donor
5 = in use
 */