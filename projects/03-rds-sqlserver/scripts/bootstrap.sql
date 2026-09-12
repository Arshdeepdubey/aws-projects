/* ---------------------------------------------------------------------------
   Bootstrap an application database with a least-privilege login.
   Run as the master user:
     sqlcmd -S 127.0.0.1,1433 -U admin -P '<password>' -i bootstrap.sql
   RDS note: you are not sysadmin on RDS SQL Server; the processadmin/setupadmin
   style statements below are the ones RDS actually permits.
--------------------------------------------------------------------------- */

:setvar AppDb      AppDb
:setvar AppLogin   app_user
:setvar AppPassword ChangeMe_ThisIsNotASecret_1!

IF DB_ID('$(AppDb)') IS NULL
BEGIN
    PRINT 'Creating database $(AppDb)';
    CREATE DATABASE [$(AppDb)];
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = '$(AppLogin)')
BEGIN
    PRINT 'Creating login $(AppLogin)';
    CREATE LOGIN [$(AppLogin)] WITH PASSWORD = '$(AppPassword)', CHECK_POLICY = ON;
END
GO

USE [$(AppDb)];
GO

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = '$(AppLogin)')
BEGIN
    CREATE USER [$(AppLogin)] FOR LOGIN [$(AppLogin)];
END
GO

/* Least privilege: read/write data, execute procedures, no DDL. */
ALTER ROLE db_datareader ADD MEMBER [$(AppLogin)];
ALTER ROLE db_datawriter ADD MEMBER [$(AppLogin)];
GRANT EXECUTE TO [$(AppLogin)];
GO

/* Example schema so there is something to query. */
IF OBJECT_ID('dbo.Customers', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.Customers (
        CustomerId   INT IDENTITY(1,1) PRIMARY KEY,
        Name         NVARCHAR(120)  NOT NULL,
        Email        NVARCHAR(256)  NOT NULL UNIQUE,
        CreatedAtUtc DATETIME2(3)   NOT NULL CONSTRAINT DF_Customers_CreatedAt DEFAULT SYSUTCDATETIME()
    );

    CREATE INDEX IX_Customers_CreatedAtUtc ON dbo.Customers (CreatedAtUtc DESC);

    INSERT INTO dbo.Customers (Name, Email) VALUES
        (N'Ada Lovelace',  N'ada@example.com'),
        (N'Grace Hopper',  N'grace@example.com');
END
GO

SELECT @@VERSION AS ServerVersion;
SELECT name, state_desc, recovery_model_desc FROM sys.databases WHERE name = '$(AppDb)';
SELECT TOP 10 * FROM dbo.Customers ORDER BY CustomerId;
GO
