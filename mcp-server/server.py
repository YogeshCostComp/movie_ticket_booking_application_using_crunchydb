"""
MCP Server for SRE Agent
Provides tools for monitoring Movie Ticket App, querying logs, and checking health
"""

import os
import json
import logging
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration from environment variables
IBM_API_KEY = os.environ.get('IBM_API_KEY', '')
CLOUD_LOGS_INSTANCE_ID = os.environ.get('CLOUD_LOGS_INSTANCE_ID', '0e3d840c-d8fd-40bc-a27c-c35d762ec2d7')
CLOUD_LOGS_REGION = os.environ.get('CLOUD_LOGS_REGION', 'us-south')
APP_URL = os.environ.get('APP_URL', 'https://movie-ticket-app.260duz8s94f7.us-south.codeengine.appdomain.cloud')
DB_HOST = os.environ.get('DB_HOST', 'ep-dry-breeze-aig3i25p-pooler.c-4.us-east-1.aws.neon.tech')

@app.route('/')
def root():
    """Root endpoint - health check for the MCP server itself"""
    return jsonify({
        'status': 'healthy',
        'service': 'SRE MCP Server',
        'version': '1.0.0',
        'api_key_configured': bool(IBM_API_KEY),
        'api_key_length': len(IBM_API_KEY) if IBM_API_KEY else 0,
        'api_key_preview': f"{IBM_API_KEY[:5]}...{IBM_API_KEY[-5:]}" if IBM_API_KEY and len(IBM_API_KEY) > 10 else "NOT SET",
        'endpoints': [
            '/tools/check_app_health',
            '/tools/check_database_health',
            '/tools/get_recent_logs',
            '/tools/get_error_logs',
            '/tools/query_logs',
            '/tools/get_seat_status',
            '/tools/get_bookings',
            '/tools/get_system_status'
        ]
    })

# Token cache
_token_cache = {
    'token': None,
    'expires_at': None
}

def get_bearer_token():
    """Get or refresh IBM Cloud IAM Bearer token"""
    global _token_cache
    
    # Check if we have a valid cached token
    if _token_cache['token'] and _token_cache['expires_at']:
        if datetime.now() < _token_cache['expires_at'] - timedelta(minutes=5):
            return _token_cache['token']
    
    # Get new token
    try:
        response = requests.post(
            'https://iam.cloud.ibm.com/identity/token',
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data=f'grant_type=urn:ibm:params:oauth:grant-type:apikey&apikey={IBM_API_KEY}'
        )
        response.raise_for_status()
        token_data = response.json()
        
        _token_cache['token'] = token_data['access_token']
        # Token typically expires in 1 hour
        _token_cache['expires_at'] = datetime.now() + timedelta(seconds=token_data.get('expires_in', 3600))
        
        logger.info("Successfully obtained new Bearer token")
        return _token_cache['token']
    except Exception as e:
        logger.error(f"Failed to get Bearer token: {e}")
        raise

def query_cloud_logs(query, start_date=None, end_date=None, limit=100):
    """Query IBM Cloud Logs using DataPrime syntax"""
    token = get_bearer_token()
    
    if not start_date:
        start_date = (datetime.utcnow() - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    if not end_date:
        end_date = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
    
    # Correct URL format for IBM Cloud Logs API - instance ID in subdomain
    url = f"https://{CLOUD_LOGS_INSTANCE_ID}.api.{CLOUD_LOGS_REGION}.logs.cloud.ibm.com/v1/query"
    
    payload = {
        "query": query,
        "metadata": {
            "start_date": start_date,
            "end_date": end_date,
            "tier": "frequent_search",
            "syntax": "dataprime",
            "limit": limit,
            "strict_fields_validation": False
        }
    }
    
    response = requests.post(
        url,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream'
        },
        json=payload,
        timeout=30
    )
    response.raise_for_status()
    return response.text


# ============== MCP Tools ==============

@app.route('/health', methods=['GET'])
def mcp_health():
    """MCP Server health check"""
    return jsonify({"status": "healthy", "service": "SRE MCP Server"})


@app.route('/tools/check_app_health', methods=['GET', 'POST'])
def check_app_health():
    """Check if the Movie Ticket App is running and healthy"""
    try:
        response = requests.get(f"{APP_URL}/health", timeout=10)
        if response.status_code == 200:
            return jsonify({
                "status": "healthy",
                "app_url": APP_URL,
                "response_time_ms": response.elapsed.total_seconds() * 1000,
                "message": "Movie Ticket App is running and healthy"
            })
        else:
            return jsonify({
                "status": "unhealthy",
                "app_url": APP_URL,
                "status_code": response.status_code,
                "message": f"App returned status code {response.status_code}"
            }), 500
    except requests.exceptions.Timeout:
        return jsonify({
            "status": "critical",
            "app_url": APP_URL,
            "message": "App is not responding (timeout)"
        }), 503
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/tools/check_database_health', methods=['GET', 'POST'])
def check_database_health():
    """Check database connectivity by calling the app's /get endpoint"""
    try:
        response = requests.get(f"{APP_URL}/get", timeout=15)
        if response.status_code == 200:
            return jsonify({
                "status": "healthy",
                "database_host": DB_HOST,
                "message": "Database connection is working",
                "response_time_ms": response.elapsed.total_seconds() * 1000
            })
        else:
            return jsonify({
                "status": "unhealthy",
                "database_host": DB_HOST,
                "status_code": response.status_code,
                "message": "Database query failed"
            }), 500
    except Exception as e:
        return jsonify({
            "status": "error",
            "database_host": DB_HOST,
            "message": str(e)
        }), 500


@app.route('/tools/get_recent_logs', methods=['GET', 'POST'])
def get_recent_logs():
    """Get recent application logs from the last hour"""
    try:
        data = request.get_json() or {}
        limit = data.get('limit', 20)
        
        # Simple query to get recent logs - filter by movie-ticket app if available
        query = f"source logs | limit {limit}"
        logs = query_cloud_logs(query, limit=limit)
        
        return jsonify({
            "status": "success",
            "query": query,
            "logs": logs
        })
    except Exception as e:
        logger.error(f"Error getting recent logs: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/tools/get_error_logs', methods=['GET', 'POST'])
def get_error_logs():
    """Get error logs from the application"""
    try:
        data = request.get_json() or {}
        hours = data.get('hours', 1)
        limit = data.get('limit', 50)
        
        start_date = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        end_date = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        # Use severity filter for errors - ERROR level is 5 in IBM Cloud Logs
        query = f"source logs | filter $m.severity == 'ERROR' | limit {limit}"
        logs = query_cloud_logs(query, start_date=start_date, end_date=end_date, limit=limit)
        
        return jsonify({
            "status": "success",
            "query": query,
            "time_range": f"Last {hours} hour(s)",
            "logs": logs
        })
    except Exception as e:
        logger.error(f"Error getting error logs: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/tools/query_logs', methods=['POST'])
def query_logs():
    """Query logs with custom DataPrime query"""
    try:
        data = request.get_json() or {}
        query = data.get('query', 'source logs | limit 10')
        hours = data.get('hours', 1)
        limit = data.get('limit', 100)
        
        start_date = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        end_date = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        logs = query_cloud_logs(query, start_date=start_date, end_date=end_date, limit=limit)
        
        return jsonify({
            "status": "success",
            "query": query,
            "time_range": f"Last {hours} hour(s)",
            "logs": logs
        })
    except Exception as e:
        logger.error(f"Error querying logs: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/tools/get_seat_status', methods=['GET', 'POST'])
def get_seat_status():
    """Get current seat availability from the Movie Ticket App"""
    try:
        response = requests.get(f"{APP_URL}/get", timeout=15)
        response.raise_for_status()
        
        seats = response.json()
        available = sum(1 for s in seats.values() if s == 'available')
        booked = sum(1 for s in seats.values() if s == 'booked')
        
        return jsonify({
            "status": "success",
            "total_seats": len(seats),
            "available": available,
            "booked": booked,
            "seats": seats
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/tools/get_bookings', methods=['GET', 'POST'])
def get_bookings():
    """Get all booking details from the Movie Ticket App"""
    try:
        response = requests.get(f"{APP_URL}/getUsersDetails", timeout=15)
        response.raise_for_status()
        
        bookings = response.json()
        return jsonify({
            "status": "success",
            "total_bookings": len(bookings),
            "bookings": bookings
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/tools/get_system_status', methods=['GET', 'POST'])
def get_system_status():
    """Get comprehensive system status including app, database, and recent errors"""
    status = {
        "timestamp": datetime.utcnow().isoformat(),
        "app": None,
        "database": None,
        "recent_errors": None,
        "overall_status": "unknown"
    }
    
    # Check app health
    try:
        response = requests.get(f"{APP_URL}/health", timeout=10)
        status["app"] = {
            "status": "healthy" if response.status_code == 200 else "unhealthy",
            "response_time_ms": response.elapsed.total_seconds() * 1000
        }
    except Exception as e:
        status["app"] = {"status": "error", "message": str(e)}
    
    # Check database
    try:
        response = requests.get(f"{APP_URL}/get", timeout=15)
        status["database"] = {
            "status": "healthy" if response.status_code == 200 else "unhealthy",
            "response_time_ms": response.elapsed.total_seconds() * 1000
        }
    except Exception as e:
        status["database"] = {"status": "error", "message": str(e)}
    
    # Check for recent errors in logs
    try:
        query = "source logs | filter $d.message contains 'error' OR $d.message contains 'Traceback' | limit 5"
        logs = query_cloud_logs(query, limit=5)
        has_errors = 'error' in logs.lower() or 'traceback' in logs.lower()
        status["recent_errors"] = {
            "has_errors": has_errors,
            "sample": logs[:500] if has_errors else None
        }
    except Exception as e:
        status["recent_errors"] = {"status": "error", "message": str(e)}
    
    # Determine overall status
    app_healthy = status["app"].get("status") == "healthy"
    db_healthy = status["database"].get("status") == "healthy"
    no_errors = not status["recent_errors"].get("has_errors", True)
    
    if app_healthy and db_healthy and no_errors:
        status["overall_status"] = "HEALTHY"
    elif app_healthy and db_healthy:
        status["overall_status"] = "DEGRADED - Recent errors detected"
    elif app_healthy:
        status["overall_status"] = "DEGRADED - Database issues"
    else:
        status["overall_status"] = "CRITICAL"
    
    return jsonify(status)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Starting MCP Server on port {port}")
    app.run(host='0.0.0.0', port=port)
