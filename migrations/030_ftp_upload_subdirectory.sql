ALTER TABLE ftp_accounts ADD COLUMN upload_subdirectory TEXT NOT NULL DEFAULT ''
    CHECK (upload_subdirectory = '' OR (
        length(upload_subdirectory) BETWEEN 1 AND 64
        AND upload_subdirectory NOT GLOB '*[^A-Za-z0-9_-]*'
    ));
