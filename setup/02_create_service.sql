-- =============================================================================
-- Create SPCS Service for User Distribution App
-- Run AFTER pushing the Docker image to the repository
-- =============================================================================

USE ROLE ACCOUNTADMIN;
USE DATABASE USER_DISTRIBUTION;
USE SCHEMA SPCS;

-- =============================================================================
-- Step 1: Create the SPCS Service
-- =============================================================================
CREATE SERVICE IF NOT EXISTS USER_DIST_SERVICE
  IN COMPUTE POOL USER_DIST_COMPUTE_POOL
  FROM SPECIFICATION $$
spec:
  containers:
    - name: attendee
      image: /user_distribution/spcs/user_dist_repo/user-distribution:latest
      env:
        SNOWFLAKE_DATABASE: USER_DISTRIBUTION
        SNOWFLAKE_WAREHOUSE: COMPUTE_WH
        SNOWFLAKE_ROLE: ACCOUNTADMIN
      resources:
        requests:
          cpu: 1
          memory: 2Gi
        limits:
          cpu: 2
          memory: 4Gi
      readinessProbe:
        port: 8501
        path: /
  endpoints:
    - name: attendee
      port: 8501
      public: true
$$
  MIN_INSTANCES = 1
  MAX_INSTANCES = 1
  COMMENT = 'User distribution attendee app';

-- =============================================================================
-- Step 2: Check Service Status
-- =============================================================================
CALL SYSTEM$WAIT_FOR_SERVICES(120, 'USER_DIST_SERVICE');
SELECT SYSTEM$GET_SERVICE_STATUS('USER_DIST_SERVICE');

-- =============================================================================
-- Step 3: Get Public Endpoint URL
-- =============================================================================
SHOW ENDPOINTS IN SERVICE USER_DIST_SERVICE;

-- The 'ingress_url' column shows the public URL for attendees
-- It will look like: <random>.snowflakecomputing.app

SELECT 'Service created!' AS status;
SELECT 'Share the public endpoint URL with attendees as: https://<endpoint>/EVENT_NAME' AS next_step;
