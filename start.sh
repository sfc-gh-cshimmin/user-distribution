#!/bin/sh
# Start Streamlit on port 8080 (behind nginx)
streamlit run attendee_app.py \
    --server.port=8080 \
    --server.address=127.0.0.1 \
    --server.headless=true \
    --browser.gatherUsageStats=false &

# Start nginx in foreground
nginx -g 'daemon off;'
