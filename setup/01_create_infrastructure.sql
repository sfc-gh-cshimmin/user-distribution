-- =============================================================================
-- SPCS Infrastructure Setup for User Distribution App
-- Run as ACCOUNTADMIN in the product_demos account
-- =============================================================================

USE ROLE ACCOUNTADMIN;

-- Create database for the app (events will be separate schemas)
CREATE DATABASE IF NOT EXISTS USER_DISTRIBUTION;

-- Create a schema for SPCS infrastructure
CREATE SCHEMA IF NOT EXISTS USER_DISTRIBUTION.SPCS;
USE DATABASE USER_DISTRIBUTION;
USE SCHEMA SPCS;

-- =============================================================================
-- Step 1: Create Image Repository
-- =============================================================================
CREATE IMAGE REPOSITORY IF NOT EXISTS USER_DIST_REPO;

-- Show repository URL (needed for docker push)
SHOW IMAGE REPOSITORIES LIKE 'USER_DIST_REPO';

-- =============================================================================
-- Step 2: Create Compute Pool
-- =============================================================================
CREATE COMPUTE POOL IF NOT EXISTS USER_DIST_COMPUTE_POOL
  MIN_NODES = 1
  MAX_NODES = 1
  INSTANCE_FAMILY = CPU_X64_XS
  AUTO_RESUME = TRUE
  AUTO_SUSPEND_SECS = 300
  COMMENT = 'Compute pool for user distribution app';

DESCRIBE COMPUTE POOL USER_DIST_COMPUTE_POOL;

-- =============================================================================
-- Step 3: Grant permissions
-- =============================================================================
GRANT USAGE ON DATABASE USER_DISTRIBUTION TO ROLE ACCOUNTADMIN;
GRANT ALL ON ALL SCHEMAS IN DATABASE USER_DISTRIBUTION TO ROLE ACCOUNTADMIN;
GRANT USAGE ON COMPUTE POOL USER_DIST_COMPUTE_POOL TO ROLE ACCOUNTADMIN;

SELECT 'Infrastructure setup complete!' AS status;
SELECT 'Next: Build and push Docker image, then run 02_create_service.sql' AS next_step;
