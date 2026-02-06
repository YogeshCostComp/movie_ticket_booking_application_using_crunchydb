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

# Enable CORS for all routes
@app.after_request
def after_request(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-API-Key'
    return response

# Configuration from environment variables
IBM_API_KEY = os.environ.get('IBM_API_KEY', '')
CLOUD_LOGS_INSTANCE_ID = os.environ.get('CLOUD_LOGS_INSTANCE_ID', '0e3d840c-d8fd-40bc-a27c-c35d762ec2d7')
CLOUD_LOGS_REGION = os.environ.get('CLOUD_LOGS_REGION', 'us-south')
APP_URL = os.environ.get('APP_URL', 'https://movie-ticket-app.260duz8s94f7.us-south.codeengine.appdomain.cloud')
DB_HOST = os.environ.get('DB_HOST', 'ep-dry-breeze-aig3i25p-pooler.c-4.us-east-1.aws.neon.tech')
MCP_API_KEY = os.environ.get('MCP_API_KEY', 'sre-mcp-secret-key-2026')

# Code Engine configuration
CODE_ENGINE_REGION = os.environ.get('CODE_ENGINE_REGION', 'us-south')

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


def discover_code_engine_apps():
    """Discover all Code Engine projects and apps dynamically"""
    token = get_bearer_token()
    base_url = f"https://api.{CODE_ENGINE_REGION}.codeengine.cloud.ibm.com/v2"
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    
    # Step 1: List all projects
    projects_response = requests.get(f"{base_url}/projects", headers=headers, timeout=30)
    if projects_response.status_code != 200:
        return {"status": "error", "message": f"Failed to list projects: {projects_response.text}"}
    
    projects = projects_response.json().get('projects', [])
    
    # Step 2: For each project, list apps
    all_apps = []
    for project in projects:
        project_id = project.get('id', '')
        project_name = project.get('name', '')
        
        apps_response = requests.get(
            f"{base_url}/projects/{project_id}/apps",
            headers=headers, timeout=30
        )
        if apps_response.status_code == 200:
            apps = apps_response.json().get('apps', [])
            for app_info in apps:
                all_apps.append({
                    "project_id": project_id,
                    "project_name": project_name,
                    "app_name": app_info.get('name', ''),
                    "app_status": app_info.get('status', 'unknown'),
                    "min_instances": app_info.get('scale_min_instances', 0),
                    "max_instances": app_info.get('scale_max_instances', 0),
                    "endpoint": app_info.get('endpoint', 'N/A'),
                    "image": app_info.get('image_reference', '')
                })
    
    return {"status": "success", "apps": all_apps}


def find_app(app_name_filter=None):
    """Find a specific app across all projects. If no filter, find the movie ticket app."""
    result = discover_code_engine_apps()
    if result.get('status') != 'success':
        return None
    
    apps = result.get('apps', [])
    
    if app_name_filter:
        # Find by name filter
        for app_info in apps:
            if app_name_filter.lower() in app_info['app_name'].lower():
                return app_info
    else:
        # Find movie-ticket app by checking the APP_URL or name containing 'movie' or 'ticket'
        for app_info in apps:
            name = app_info['app_name'].lower()
            if 'movie' in name or 'ticket' in name:
                return app_info
    
    return None


def scale_code_engine_app(project_id, app_name, min_scale, max_scale=None):
    """Scale a Code Engine application (0 = stop, 1+ = start)"""
    token = get_bearer_token()
    
    if max_scale is None:
        max_scale = max(min_scale, 1)
    
    url = f"https://api.{CODE_ENGINE_REGION}.codeengine.cloud.ibm.com/v2/projects/{project_id}/apps/{app_name}"
    
    # First, get the current app configuration for ETag
    get_response = requests.get(
        url,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        },
        timeout=30
    )
    
    if get_response.status_code != 200:
        return {"status": "error", "message": f"Failed to get app info: {get_response.text}"}
    
    etag = get_response.headers.get('ETag', '')
    
    # Update the scale settings
    patch_data = {
        "scale_min_instances": min_scale,
        "scale_max_instances": max_scale
    }
    
    patch_response = requests.patch(
        url,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/merge-patch+json',
            'If-Match': etag
        },
        json=patch_data,
        timeout=30
    )
    
    if patch_response.status_code in [200, 201, 202]:
        return {
            "status": "success",
            "app_name": app_name,
            "project_id": project_id,
            "action": "stopped" if min_scale == 0 else "started",
            "min_instances": min_scale,
            "max_instances": max_scale,
            "message": f"App '{app_name}' {'stopped (scaled to 0)' if min_scale == 0 else f'started (scaled to {min_scale}-{max_scale})'}"
        }
    else:
        return {"status": "error", "message": f"Failed to scale app: {patch_response.text}"}


def get_code_engine_app_status(project_id, app_name):
    """Get the current status of a Code Engine application"""
    token = get_bearer_token()
    
    url = f"https://api.{CODE_ENGINE_REGION}.codeengine.cloud.ibm.com/v2/projects/{project_id}/apps/{app_name}"
    
    response = requests.get(
        url,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        },
        timeout=30
    )
    
    if response.status_code == 200:
        app_data = response.json()
        return {
            "status": "success",
            "app_name": app_name,
            "project_id": project_id,
            "app_status": app_data.get('status', 'unknown'),
            "min_instances": app_data.get('scale_min_instances', 0),
            "max_instances": app_data.get('scale_max_instances', 10),
            "url": app_data.get('endpoint', 'N/A'),
            "image": app_data.get('image_reference', '')
        }
    else:
        return {"status": "error", "message": f"Failed to get app status: {response.text}"}


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
        if request.method == 'GET':
            limit = request.args.get('limit', 20, type=int)
        else:
            data = request.get_json(silent=True) or {}
            limit = data.get('limit', 20)
        
        # Get recent logs from all Code Engine apps (movie-ticket and sre-mcp-server)
        query = f"source logs | filter $d.app == 'codeengine' | limit {limit}"
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
        if request.method == 'GET':
            hours = request.args.get('hours', 1, type=int)
            limit = request.args.get('limit', 50, type=int)
        else:
            data = request.get_json(silent=True) or {}
            hours = data.get('hours', 1)
            limit = data.get('limit', 50)
        
        start_date = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        end_date = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        # Filter for error/exception logs from all Code Engine apps
        query = f"source logs | filter $d.app == 'codeengine' | filter $d.message.message ~ 'error|Error|ERROR|exception|Exception|failed|Failed' | limit {limit}"
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


@app.route('/tools/query_logs', methods=['GET', 'POST'])
def query_logs():
    """Query logs with custom DataPrime query"""
    try:
        if request.method == 'GET':
            query = request.args.get('query', 'source logs | limit 10')
            hours = request.args.get('hours', 1, type=int)
            limit = request.args.get('limit', 100, type=int)
        else:
            data = request.get_json(silent=True) or {}
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


# ============== MCP Protocol Support ==============
# MCP uses JSON-RPC 2.0 over HTTP/SSE

MCP_TOOLS = [
    {
        "name": "check_app_health",
        "description": "Check the health status of the Movie Ticket Booking application",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "check_database_health",
        "description": "Check the database connectivity and health status",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_recent_logs",
        "description": "Get recent application logs from IBM Cloud Logs",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of logs to return",
                    "default": 20
                }
            },
            "required": []
        }
    },
    {
        "name": "get_error_logs",
        "description": "Get error logs from the application",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "Number of hours to look back",
                    "default": 1
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of logs to return",
                    "default": 50
                }
            },
            "required": []
        }
    },
    {
        "name": "query_logs",
        "description": "Query logs with custom DataPrime query",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "DataPrime query string",
                    "default": "source logs | limit 10"
                },
                "hours": {
                    "type": "integer",
                    "description": "Number of hours to look back",
                    "default": 1
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results",
                    "default": 100
                }
            },
            "required": []
        }
    },
    {
        "name": "get_system_status",
        "description": "Get comprehensive system status including app health, database, and recent errors",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_seat_bookings",
        "description": "Get the current seat booking status - shows available seats, blocked/booked seats, and who booked them with their contact details",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_app_logs",
        "description": "Get application logs from the Movie Ticket Booking app only",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of logs to return",
                    "default": 20
                }
            },
            "required": []
        }
    },
    {
        "name": "get_platform_logs",
        "description": "Get IBM Code Engine platform logs (builds, deployments, infrastructure)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of logs to return",
                    "default": 20
                }
            },
            "required": []
        }
    },
    {
        "name": "stop_app",
        "description": "Stop the Movie Ticket Booking application by scaling it to 0 instances",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "start_app",
        "description": "Start the Movie Ticket Booking application by scaling it to 1 instance",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "restart_app",
        "description": "Restart the Movie Ticket Booking application by stopping and starting it",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_app_status",
        "description": "Get the current status of the Movie Ticket Booking application including running instances",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]

@app.route('/mcp', methods=['GET', 'POST', 'OPTIONS'])
def mcp_endpoint():
    """MCP JSON-RPC endpoint for watsonx Orchestrate"""
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-API-Key'
        return response
    
    # Handle GET request (health check / discovery)
    if request.method == 'GET':
        return jsonify({
            "jsonrpc": "2.0",
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {
                    "name": "sre-mcp-server",
                    "version": "1.0.0"
                }
            }
        })
    
    # Verify API key for POST requests (optional - check header)
    auth_header = request.headers.get('Authorization', '')
    api_key_header = request.headers.get('X-API-Key', '')
    
    # Accept requests with valid API key or no auth (for testing)
    # In production, uncomment the following to enforce auth:
    # if api_key_header != MCP_API_KEY and not auth_header.endswith(MCP_API_KEY):
    #     return jsonify({"error": "Unauthorized"}), 401
    
    try:
        data = request.get_json()
        method = data.get('method', '')
        params = data.get('params', {})
        request_id = data.get('id', 1)
        
        logger.info(f"MCP Request: method={method}, params={params}")
        
        if method == 'initialize':
            return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "sre-mcp-server",
                        "version": "1.0.0"
                    }
                }
            })
        
        elif method == 'tools/list':
            return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": MCP_TOOLS
                }
            })
        
        elif method == 'tools/call':
            tool_name = params.get('name', '')
            tool_args = params.get('arguments', {})
            
            # Execute the tool
            result = execute_mcp_tool(tool_name, tool_args)
            
            return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, indent=2)
                        }
                    ]
                }
            })
        
        else:
            return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            })
            
    except Exception as e:
        logger.error(f"MCP Error: {e}")
        return jsonify({
            "jsonrpc": "2.0",
            "id": request_id if 'request_id' in dir() else 1,
            "error": {
                "code": -32603,
                "message": str(e)
            }
        })


def execute_mcp_tool(tool_name, args):
    """Execute an MCP tool and return the result"""
    try:
        if tool_name == 'check_app_health':
            response = requests.get(APP_URL, timeout=30)
            return {
                "status": "healthy" if response.status_code == 200 else "unhealthy",
                "app_url": APP_URL,
                "response_time_ms": response.elapsed.total_seconds() * 1000,
                "message": "Movie Ticket App is running and healthy" if response.status_code == 200 else f"App returned status {response.status_code}"
            }
        
        elif tool_name == 'check_database_health':
            try:
                response = requests.get(f"{APP_URL}/get", timeout=30)
                return {
                    "status": "healthy" if response.status_code == 200 else "unhealthy",
                    "message": "Database connection is working" if response.status_code == 200 else "Database connection issue detected"
                }
            except Exception as e:
                return {"status": "unhealthy", "message": str(e)}
        
        elif tool_name == 'get_recent_logs':
            limit = args.get('limit', 20)
            query = f"source logs | filter $d.app == 'codeengine' | limit {limit}"
            logs = query_cloud_logs(query, limit=limit)
            return {"status": "success", "query": query, "logs": logs}
        
        elif tool_name == 'get_error_logs':
            hours = args.get('hours', 1)
            limit = args.get('limit', 50)
            start_date = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
            end_date = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
            query = f"source logs | filter $d.app == 'codeengine' | filter $d.message.message ~ 'error|Error|ERROR|exception|Exception|failed|Failed' | limit {limit}"
            logs = query_cloud_logs(query, start_date=start_date, end_date=end_date, limit=limit)
            return {"status": "success", "query": query, "time_range": f"Last {hours} hour(s)", "logs": logs}
        
        elif tool_name == 'query_logs':
            query = args.get('query', 'source logs | limit 10')
            hours = args.get('hours', 1)
            limit = args.get('limit', 100)
            start_date = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
            end_date = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
            logs = query_cloud_logs(query, start_date=start_date, end_date=end_date, limit=limit)
            return {"status": "success", "query": query, "time_range": f"Last {hours} hour(s)", "logs": logs}
        
        elif tool_name == 'get_system_status':
            # Check app health
            try:
                app_response = requests.get(APP_URL, timeout=30)
                app_status = {"status": "healthy" if app_response.status_code == 200 else "unhealthy"}
            except Exception as e:
                app_status = {"status": "unhealthy", "error": str(e)}
            
            # Check database
            try:
                db_response = requests.get(f"{APP_URL}/get", timeout=30)
                db_status = {"status": "healthy" if db_response.status_code == 200 else "unhealthy"}
            except Exception as e:
                db_status = {"status": "unhealthy", "error": str(e)}
            
            overall = "HEALTHY" if app_status["status"] == "healthy" and db_status["status"] == "healthy" else "DEGRADED"
            
            return {
                "overall_status": overall,
                "app": app_status,
                "database": db_status,
                "timestamp": datetime.utcnow().isoformat()
            }
        
        elif tool_name == 'get_seat_bookings':
            try:
                # Get seat status
                seats_response = requests.get(f"{APP_URL}/get", timeout=30)
                # Get booking details (who booked)
                bookings_response = requests.get(f"{APP_URL}/getUsersDetails", timeout=30)
                
                if seats_response.status_code == 200:
                    seats = seats_response.json()
                    available_seats = [seat for seat, status in seats.items() if status == "available"]
                    blocked_seats = [seat for seat, status in seats.items() if status == "blocked"]
                    reserved_seats = [seat for seat, status in seats.items() if status == "reserved"]
                    total = len(seats)
                    
                    result = {
                        "status": "success",
                        "summary": {
                            "total_seats": total,
                            "available_count": len(available_seats),
                            "booked_count": len(blocked_seats),
                            "reserved_count": len(reserved_seats)
                        },
                        "available_seats": sorted(available_seats),
                        "booked_seats": sorted(blocked_seats),
                        "message": f"Out of {total} seats: {len(available_seats)} available, {len(blocked_seats)} booked"
                    }
                    
                    # Add booking details if available
                    if bookings_response.status_code == 200:
                        bookings_data = bookings_response.json()
                        bookings = bookings_data if isinstance(bookings_data, list) else bookings_data.get('value', [])
                        # Filter out empty bookings
                        valid_bookings = [b for b in bookings if b.get('name') and b.get('seats')]
                        result["bookings"] = valid_bookings
                        result["booking_details"] = [
                            f"{b['name']} ({b['phone_no']}): seats {b['seats']}" 
                            for b in valid_bookings
                        ]
                    
                    return result
                else:
                    return {"status": "error", "message": f"Failed to get seat data: HTTP {seats_response.status_code}"}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        
        elif tool_name == 'get_app_logs':
            limit = args.get('limit', 20)
            # App logs - filter for movie-ticket-project only
            query = f"source logs | filter $d.label.Project == 'movie-ticket-project' | limit {limit}"
            logs = query_cloud_logs(query, limit=limit)
            return {"status": "success", "log_type": "Application Logs (Movie Ticket App)", "query": query, "logs": logs}
        
        elif tool_name == 'get_platform_logs':
            limit = args.get('limit', 20)
            # Platform logs - Code Engine builds, deployments, etc. (exclude movie-ticket-project app logs)
            query = f"source logs | filter $d.app == 'codeengine' | filter $d.label.Project != 'movie-ticket-project' | limit {limit}"
            logs = query_cloud_logs(query, limit=limit)
            return {"status": "success", "log_type": "Platform Logs (Code Engine)", "query": query, "logs": logs}
        
        elif tool_name == 'stop_app':
            # Dynamically find the movie ticket app
            app_info = find_app('movie-ticket')
            if not app_info:
                # Fallback: try listing all apps
                discovery = discover_code_engine_apps()
                return {"status": "error", "message": "Could not find Movie Ticket App", "discovered_apps": discovery.get('apps', [])}
            
            result = scale_code_engine_app(app_info['project_id'], app_info['app_name'], min_scale=0, max_scale=0)
            return result
        
        elif tool_name == 'start_app':
            # Dynamically find the movie ticket app
            app_info = find_app('movie-ticket')
            if not app_info:
                discovery = discover_code_engine_apps()
                return {"status": "error", "message": "Could not find Movie Ticket App", "discovered_apps": discovery.get('apps', [])}
            
            result = scale_code_engine_app(app_info['project_id'], app_info['app_name'], min_scale=1, max_scale=10)
            return result
        
        elif tool_name == 'restart_app':
            # Dynamically find the movie ticket app
            app_info = find_app('movie-ticket')
            if not app_info:
                discovery = discover_code_engine_apps()
                return {"status": "error", "message": "Could not find Movie Ticket App", "discovered_apps": discovery.get('apps', [])}
            
            # Stop
            stop_result = scale_code_engine_app(app_info['project_id'], app_info['app_name'], min_scale=0, max_scale=0)
            if stop_result.get('status') != 'success':
                return {"status": "error", "message": f"Failed to stop app: {stop_result.get('message')}"}
            
            import time
            time.sleep(3)
            
            # Start
            start_result = scale_code_engine_app(app_info['project_id'], app_info['app_name'], min_scale=1, max_scale=10)
            if start_result.get('status') != 'success':
                return {"status": "error", "message": f"Stopped but failed to start: {start_result.get('message')}"}
            
            return {
                "status": "success",
                "app_name": app_info['app_name'],
                "project_id": app_info['project_id'],
                "action": "restarted",
                "message": f"App '{app_info['app_name']}' has been restarted successfully"
            }
        
        elif tool_name == 'get_app_status':
            # Dynamically find and get status for all Code Engine apps
            discovery = discover_code_engine_apps()
            if discovery.get('status') != 'success':
                return discovery
            
            return {
                "status": "success",
                "message": f"Found {len(discovery['apps'])} app(s) across Code Engine projects",
                "apps": discovery['apps']
            }
        
        else:
            return {"error": f"Unknown tool: {tool_name}"}
            
    except Exception as e:
        return {"error": str(e)}


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Starting MCP Server on port {port}")
    app.run(host='0.0.0.0', port=port)
