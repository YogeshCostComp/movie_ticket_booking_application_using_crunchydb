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


def get_app_instances(project_id, app_name):
    """Get running instances of a Code Engine app with CPU/memory/restart details"""
    token = get_bearer_token()
    url = f"https://api.{CODE_ENGINE_REGION}.codeengine.cloud.ibm.com/v2/projects/{project_id}/apps/{app_name}/instances"
    response = requests.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=30)
    if response.status_code == 200:
        instances = response.json().get('instances', [])
        instance_details = []
        for inst in instances:
            detail = {
                "name": inst.get('name', ''),
                "status": inst.get('status', 'unknown'),
                "revision": inst.get('revision_name', ''),
                "cpu_limit": inst.get('scale_cpu_limit', ''),
                "memory_limit": inst.get('scale_memory_limit', ''),
                "ephemeral_storage": inst.get('scale_ephemeral_storage_limit', ''),
                "created_at": inst.get('created_at', ''),
            }
            status_details = inst.get('status_details', {})
            if status_details:
                detail["restarts"] = status_details.get('restarts', 0)
                user_container = status_details.get('user_container', {})
                if user_container:
                    current = user_container.get('current_state', {})
                    detail["container_status"] = current.get('container_status', 'unknown')
                    detail["container_reason"] = current.get('reason', '')
                    detail["started_at"] = current.get('started_at', '')
                    if current.get('exit_code') is not None:
                        detail["exit_code"] = current['exit_code']
            instance_details.append(detail)
        return {"status": "success", "instance_count": len(instances), "instances": instance_details}
    else:
        return {"status": "error", "message": f"Failed to get instances: {response.text}"}


def get_app_revisions(project_id, app_name):
    """Get deployment revisions history for an app"""
    token = get_bearer_token()
    url = f"https://api.{CODE_ENGINE_REGION}.codeengine.cloud.ibm.com/v2/projects/{project_id}/apps/{app_name}/revisions"
    response = requests.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=30)
    if response.status_code == 200:
        revisions = response.json().get('revisions', [])
        rev_details = []
        for rev in revisions:
            rev_details.append({
                "name": rev.get('name', ''),
                "status": rev.get('status', 'unknown'),
                "created_at": rev.get('created_at', ''),
                "image": rev.get('image_reference', ''),
                "cpu_limit": rev.get('scale_cpu_limit', ''),
                "memory_limit": rev.get('scale_memory_limit', ''),
                "min_instances": rev.get('scale_min_instances', 0),
                "max_instances": rev.get('scale_max_instances', 0),
            })
        return {"status": "success", "revision_count": len(revisions), "revisions": rev_details}
    else:
        return {"status": "error", "message": f"Failed to get revisions: {response.text}"}


def get_build_status(project_id, limit=5):
    """Get recent build runs status for a project"""
    token = get_bearer_token()
    url = f"https://api.{CODE_ENGINE_REGION}.codeengine.cloud.ibm.com/v2/projects/{project_id}/build_runs?limit={limit}"
    response = requests.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=30)
    if response.status_code == 200:
        build_runs = response.json().get('build_runs', [])
        builds = []
        for br in build_runs:
            sd = br.get('status_details', {})
            builds.append({
                "name": br.get('name', ''),
                "status": br.get('status', 'unknown'),
                "created_at": br.get('created_at', ''),
                "start_time": sd.get('start_time', ''),
                "completion_time": sd.get('completion_time', ''),
                "git_commit": sd.get('git_commit_sha', '')[:8] if sd.get('git_commit_sha') else '',
                "build_name": br.get('build_name', ''),
            })
        return {"status": "success", "build_count": len(builds), "builds": builds}
    else:
        return {"status": "error", "message": f"Failed to get build runs: {response.text}"}


def measure_response_times(num_samples=5):
    """Measure response times for multiple app endpoints with percentile SLAs"""
    import time
    endpoints = {
        "homepage": APP_URL,
        "api_seats": f"{APP_URL}/get",
        "api_users": f"{APP_URL}/getUsersDetails",
    }
    results = {}
    all_times = []
    
    for name, url in endpoints.items():
        samples = []
        status_code = 0
        errors = 0
        for _ in range(num_samples):
            try:
                start = time.time()
                resp = requests.get(url, timeout=30)
                elapsed_ms = (time.time() - start) * 1000
                samples.append(elapsed_ms)
                status_code = resp.status_code
                if resp.status_code >= 400:
                    errors += 1
            except requests.exceptions.Timeout:
                samples.append(30000)
                errors += 1
            except Exception as e:
                errors += 1
        
        if samples:
            sorted_s = sorted(samples)
            p50 = sorted_s[len(sorted_s) // 2]
            p90 = sorted_s[int(len(sorted_s) * 0.9)] if len(sorted_s) >= 2 else sorted_s[-1]
            p95 = sorted_s[int(len(sorted_s) * 0.95)] if len(sorted_s) >= 2 else sorted_s[-1]
            p99 = sorted_s[-1]
            avg = sum(sorted_s) / len(sorted_s)
            all_times.extend(samples)
        else:
            p50 = p90 = p95 = p99 = avg = -1
        
        results[name] = {
            "url": url,
            "status_code": status_code,
            "samples": num_samples,
            "avg_ms": round(avg, 2),
            "p50_ms": round(p50, 2),
            "p90_ms": round(p90, 2),
            "p95_ms": round(p95, 2),
            "p99_ms": round(p99, 2),
            "min_ms": round(min(samples), 2) if samples else -1,
            "max_ms": round(max(samples), 2) if samples else -1,
            "error_count": errors,
            "sla_status": "OK" if p90 < 3000 else ("WARNING" if p90 < 10000 else "BREACH"),
        }
    
    # Global percentiles across all endpoints
    if all_times:
        all_sorted = sorted(all_times)
        global_p50 = all_sorted[len(all_sorted) // 2]
        global_p90 = all_sorted[int(len(all_sorted) * 0.9)]
        global_p95 = all_sorted[int(len(all_sorted) * 0.95)]
        global_p99 = all_sorted[-1]
        global_avg = sum(all_sorted) / len(all_sorted)
    else:
        global_p50 = global_p90 = global_p95 = global_p99 = global_avg = -1
    
    breaches = sum(1 for r in results.values() if r['sla_status'] == 'BREACH')
    
    return {
        "status": "success",
        "total_requests": len(all_times),
        "global_latency": {
            "avg_ms": round(global_avg, 2),
            "p50_ms": round(global_p50, 2),
            "p90_ms": round(global_p90, 2),
            "p95_ms": round(global_p95, 2),
            "p99_ms": round(global_p99, 2),
        },
        "sla_breaches": breaches,
        "sla_thresholds": {
            "p90_target": "< 3s (3000ms)",
            "p95_target": "< 5s (5000ms)",
            "OK": "P90 < 3s",
            "WARNING": "P90 3-10s",
            "BREACH": "P90 > 10s"
        },
        "sla_compliance": {
            "p90_under_3s": f"{sum(1 for t in all_times if t < 3000)}/{len(all_times)} requests" if all_times else "N/A",
            "p90_pct": round((sum(1 for t in all_times if t < 3000) / len(all_times)) * 100, 1) if all_times else 0,
            "p95_under_5s": f"{sum(1 for t in all_times if t < 5000)}/{len(all_times)} requests" if all_times else "N/A",
            "p95_pct": round((sum(1 for t in all_times if t < 5000) / len(all_times)) * 100, 1) if all_times else 0,
        },
        "endpoints": results
    }


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
    },
    {
        "name": "get_app_instances",
        "description": "Get detailed info about running app instances including CPU/memory utilization, container state, restart count, and OOMKilled events. Use this for instance-level resource monitoring.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_response_times",
        "description": "Measure response times (latency) for all app endpoints with percentile metrics (P50/P90/P95/P99) and SLA compliance. Takes multiple samples per endpoint. SLA targets: P90 < 3s, P95 < 5s.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_deployment_history",
        "description": "Get deployment revision history for the Movie Ticket App showing all past deployments, their status, image versions, and resource configurations.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_build_status",
        "description": "Get recent CI/CD build runs showing build success/failure status, git commits, and build times for the Movie Ticket App.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of recent builds to return",
                    "default": 5
                }
            },
            "required": []
        }
    },
    {
        "name": "get_failure_analysis",
        "description": "Analyze application failures from logs including exceptions, HTTP 500 errors, database errors, OOM kills, and timeouts. Provides a categorized failure summary.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "Number of hours to look back for failures",
                    "default": 24
                }
            },
            "required": []
        }
    },
    {
        "name": "get_sre_dashboard",
        "description": "Get a comprehensive SRE dashboard based on the 4 Golden Signals: Latency (P50/P90/P95/P99 with SLA compliance), Error Rate (error % and categories), Saturation (CPU, memory, instance count, OOM), and Traffic (request counts). Includes overall health score.",
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
        
        elif tool_name == 'get_app_instances':
            # Get detailed instance info for the movie ticket app
            app_info = find_app('movie-ticket')
            if not app_info:
                return {"status": "error", "message": "Could not find Movie Ticket App"}
            result = get_app_instances(app_info['project_id'], app_info['app_name'])
            result['app_name'] = app_info['app_name']
            result['project_name'] = app_info.get('project_name', '')
            return result
        
        elif tool_name == 'get_response_times':
            # Measure response times and check SLAs
            result = measure_response_times()
            return result
        
        elif tool_name == 'get_deployment_history':
            # Get revision history
            app_info = find_app('movie-ticket')
            if not app_info:
                return {"status": "error", "message": "Could not find Movie Ticket App"}
            result = get_app_revisions(app_info['project_id'], app_info['app_name'])
            result['app_name'] = app_info['app_name']
            return result
        
        elif tool_name == 'get_build_status':
            # Get recent builds
            app_info = find_app('movie-ticket')
            if not app_info:
                return {"status": "error", "message": "Could not find Movie Ticket App"}
            limit = args.get('limit', 5)
            result = get_build_status(app_info['project_id'], limit=limit)
            result['app_name'] = app_info['app_name']
            return result
        
        elif tool_name == 'get_failure_analysis':
            hours = args.get('hours', 24)
            start_date = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
            end_date = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
            
            # Query for different failure categories
            categories = {
                "exceptions": "source logs | filter $d.label.Project == 'movie-ticket-project' | filter $d.message.message ~ 'exception|Exception|EXCEPTION|Traceback' | limit 50",
                "http_errors": "source logs | filter $d.label.Project == 'movie-ticket-project' | filter $d.message.message ~ '500|502|503|504|Internal Server Error' | limit 50",
                "database_errors": "source logs | filter $d.label.Project == 'movie-ticket-project' | filter $d.message.message ~ 'psycopg2|DatabaseError|OperationalError|connection refused|timeout expired' | limit 50",
                "app_crashes": "source logs | filter $d.label.Project == 'movie-ticket-project' | filter $d.message.message ~ 'OOMKilled|killed|crash|segfault|memory' | limit 50",
            }
            
            failure_summary = {}
            total_failures = 0
            for category, query in categories.items():
                logs = query_cloud_logs(query, start_date=start_date, end_date=end_date, limit=50)
                count = len(logs) if isinstance(logs, list) else 0
                failure_summary[category] = {
                    "count": count,
                    "sample_logs": logs[:3] if isinstance(logs, list) and logs else []
                }
                total_failures += count
            
            return {
                "status": "success",
                "time_range": f"Last {hours} hour(s)",
                "total_failures": total_failures,
                "severity": "CRITICAL" if total_failures > 20 else ("WARNING" if total_failures > 5 else "HEALTHY"),
                "categories": failure_summary
            }
        
        elif tool_name == 'get_sre_dashboard':
            import time as time_module
            dashboard = {
                "timestamp": datetime.utcnow().isoformat(),
                "framework": "4 Golden Signals (Google SRE)"
            }
            issues = []
            
            # ========================================
            # SIGNAL 1: LATENCY
            # ========================================
            try:
                latency_data = measure_response_times(num_samples=5)
                gl = latency_data.get("global_latency", {})
                sla = latency_data.get("sla_compliance", {})
                p90_pct = sla.get("p90_pct", 0)
                p95_pct = sla.get("p95_pct", 0)
                
                # Determine latency health
                if gl.get("p90_ms", 0) < 3000:
                    latency_status = "HEALTHY"
                elif gl.get("p90_ms", 0) < 10000:
                    latency_status = "DEGRADED"
                    issues.append(f"P90 latency is {round(gl['p90_ms'])}ms (target: <3000ms)")
                else:
                    latency_status = "CRITICAL"
                    issues.append(f"P90 latency BREACH: {round(gl['p90_ms'])}ms (target: <3000ms)")
                
                dashboard["1_latency"] = {
                    "status": latency_status,
                    "global_percentiles": {
                        "p50_ms": gl.get("p50_ms", -1),
                        "p90_ms": gl.get("p90_ms", -1),
                        "p95_ms": gl.get("p95_ms", -1),
                        "p99_ms": gl.get("p99_ms", -1),
                        "avg_ms": gl.get("avg_ms", -1),
                    },
                    "sla_compliance": {
                        "p90_target": "90% of requests < 3s",
                        "p90_actual_pct": p90_pct,
                        "p90_met": p90_pct >= 90,
                        "p95_target": "95% of requests < 5s",
                        "p95_actual_pct": p95_pct,
                        "p95_met": p95_pct >= 95,
                    },
                    "per_endpoint": latency_data.get("endpoints", {}),
                }
            except Exception as e:
                dashboard["1_latency"] = {"status": "ERROR", "error": str(e)}
                issues.append(f"Could not measure latency: {e}")
            
            # ========================================
            # SIGNAL 2: ERRORS
            # ========================================
            try:
                # App health check
                app_resp = requests.get(APP_URL, timeout=30)
                app_healthy = app_resp.status_code == 200
                
                # DB health check
                db_resp = requests.get(f"{APP_URL}/get", timeout=30)
                db_healthy = db_resp.status_code == 200
                
                # Error logs from last 1 hour
                error_query = "source logs | filter $d.label.Project == 'movie-ticket-project' | filter $d.message.message ~ 'error|Error|ERROR|exception|Exception|failed|Failed|500|502|503' | limit 100"
                error_logs = query_cloud_logs(error_query, limit=100)
                error_count = len(error_logs) if isinstance(error_logs, list) else 0
                
                # Categorize errors
                http_errors = 0
                db_errors = 0
                app_exceptions = 0
                if isinstance(error_logs, list):
                    for log in error_logs:
                        msg = str(log).lower()
                        if '500' in msg or '502' in msg or '503' in msg or '504' in msg:
                            http_errors += 1
                        elif 'psycopg2' in msg or 'database' in msg or 'connection' in msg:
                            db_errors += 1
                        elif 'exception' in msg or 'traceback' in msg:
                            app_exceptions += 1
                
                # Calculate error rate from latency measurement
                total_requests_sampled = latency_data.get("total_requests", 0) if 'latency_data' in dir() else 0
                endpoint_errors = sum(r.get('error_count', 0) for r in latency_data.get("endpoints", {}).values()) if 'latency_data' in dir() else 0
                error_rate_pct = round((endpoint_errors / total_requests_sampled) * 100, 2) if total_requests_sampled > 0 else 0
                
                if not app_healthy:
                    issues.append("App is unhealthy (non-200 response)")
                if not db_healthy:
                    issues.append("Database is unhealthy (non-200 response)")
                if error_count > 10:
                    issues.append(f"High error rate: {error_count} errors in last hour")
                if error_rate_pct > 5:
                    issues.append(f"Request error rate: {error_rate_pct}%")
                
                error_status = "HEALTHY"
                if not app_healthy or not db_healthy or error_rate_pct > 5:
                    error_status = "CRITICAL"
                elif error_count > 10 or error_rate_pct > 1:
                    error_status = "DEGRADED"
                
                dashboard["2_errors"] = {
                    "status": error_status,
                    "app_health": "healthy" if app_healthy else "unhealthy",
                    "app_response_time_ms": round(app_resp.elapsed.total_seconds() * 1000, 2),
                    "db_health": "healthy" if db_healthy else "unhealthy",
                    "db_response_time_ms": round(db_resp.elapsed.total_seconds() * 1000, 2),
                    "error_rate_pct": error_rate_pct,
                    "errors_last_hour": error_count,
                    "error_breakdown": {
                        "http_5xx": http_errors,
                        "database_errors": db_errors,
                        "app_exceptions": app_exceptions,
                        "other": error_count - http_errors - db_errors - app_exceptions
                    },
                    "error_budget": {
                        "target_error_rate": "< 1%",
                        "current_rate": f"{error_rate_pct}%",
                        "budget_remaining": f"{max(0, 1.0 - error_rate_pct)}%" if error_rate_pct <= 1 else "EXHAUSTED"
                    }
                }
            except Exception as e:
                dashboard["2_errors"] = {"status": "ERROR", "error": str(e)}
                issues.append(f"Could not check errors: {e}")
            
            # ========================================
            # SIGNAL 3: SATURATION
            # ========================================
            try:
                app_info = find_app('movie-ticket')
                saturation_status = "HEALTHY"
                if app_info:
                    instances = get_app_instances(app_info['project_id'], app_info['app_name'])
                    instance_list = instances.get('instances', [])
                    instance_count = instances.get('instance_count', 0)
                    
                    total_restarts = sum(i.get('restarts', 0) for i in instance_list)
                    oom_killed = any(i.get('container_reason') == 'OOMKilled' for i in instance_list)
                    
                    # Parse CPU/memory limits
                    cpu_limit = app_info.get('cpu_limit', 'N/A')
                    memory_limit = app_info.get('memory_limit', 'N/A')
                    
                    if oom_killed:
                        saturation_status = "CRITICAL"
                        issues.append("OOMKilled detected - memory saturation")
                    elif total_restarts > 5:
                        saturation_status = "DEGRADED"
                        issues.append(f"High restart count: {total_restarts}")
                    elif instance_count == 0:
                        saturation_status = "CRITICAL"
                        issues.append("No running instances - app may be scaled to 0")
                    
                    dashboard["3_saturation"] = {
                        "status": saturation_status,
                        "instances": {
                            "running": instance_count,
                            "min_scale": app_info.get('min_scale', 'N/A'),
                            "max_scale": app_info.get('max_scale', 'N/A'),
                        },
                        "resource_limits": {
                            "cpu": cpu_limit,
                            "memory": memory_limit,
                        },
                        "health_indicators": {
                            "total_restarts": total_restarts,
                            "oom_killed": oom_killed,
                            "restart_severity": "CRITICAL" if total_restarts > 10 else ("WARNING" if total_restarts > 3 else "OK"),
                        },
                        "instance_details": instance_list,
                    }
                else:
                    dashboard["3_saturation"] = {"status": "UNKNOWN", "error": "Could not find Movie Ticket App via Code Engine API"}
                    issues.append("Cannot determine saturation - app not found in Code Engine")
            except Exception as e:
                dashboard["3_saturation"] = {"status": "ERROR", "error": str(e)}
                issues.append(f"Could not check saturation: {e}")
            
            # ========================================
            # SIGNAL 4: TRAFFIC
            # ========================================
            try:
                # Get recent traffic from logs
                traffic_query = "source logs | filter $d.label.Project == 'movie-ticket-project' | filter $d.message.message ~ 'GET|POST|request|Request' | limit 100"
                traffic_logs = query_cloud_logs(traffic_query, limit=100)
                traffic_count = len(traffic_logs) if isinstance(traffic_logs, list) else 0
                
                # Count booking-related traffic
                booking_query = "source logs | filter $d.label.Project == 'movie-ticket-project' | filter $d.message.message ~ 'update|INSERT|booking|reserve|Reservation' | limit 100"
                booking_logs = query_cloud_logs(booking_query, limit=100)
                booking_count = len(booking_logs) if isinstance(booking_logs, list) else 0
                
                # Get seat utilization for capacity insight
                try:
                    seats_resp = requests.get(f"{APP_URL}/get", timeout=15)
                    if seats_resp.status_code == 200:
                        seat_data = seats_resp.json()
                        total_seats = len(seat_data)
                        booked_seats = sum(1 for s in seat_data.values() if s == 'blocked')
                        available_seats = total_seats - booked_seats
                        occupancy_pct = round((booked_seats / total_seats) * 100, 1) if total_seats > 0 else 0
                    else:
                        total_seats = booked_seats = available_seats = 0
                        occupancy_pct = 0
                except:
                    total_seats = booked_seats = available_seats = 0
                    occupancy_pct = 0
                
                traffic_status = "HEALTHY"
                if traffic_count == 0:
                    traffic_status = "WARNING"
                    issues.append("No traffic detected in recent logs")
                
                dashboard["4_traffic"] = {
                    "status": traffic_status,
                    "requests_in_logs": traffic_count,
                    "booking_transactions": booking_count,
                    "seat_utilization": {
                        "total_seats": total_seats,
                        "booked": booked_seats,
                        "available": available_seats,
                        "occupancy_pct": occupancy_pct,
                    },
                    "note": "Traffic counts are sampled from recent Cloud Logs (up to 100 entries)"
                }
            except Exception as e:
                dashboard["4_traffic"] = {"status": "ERROR", "error": str(e)}
            
            # ========================================
            # OVERALL SRE HEALTH SCORE
            # ========================================
            signal_statuses = [
                dashboard.get("1_latency", {}).get("status", "UNKNOWN"),
                dashboard.get("2_errors", {}).get("status", "UNKNOWN"),
                dashboard.get("3_saturation", {}).get("status", "UNKNOWN"),
                dashboard.get("4_traffic", {}).get("status", "UNKNOWN"),
            ]
            critical_count = signal_statuses.count("CRITICAL")
            degraded_count = signal_statuses.count("DEGRADED")
            error_count_signals = signal_statuses.count("ERROR")
            
            if critical_count > 0 or error_count_signals > 1:
                overall = "CRITICAL"
            elif degraded_count > 0 or error_count_signals > 0:
                overall = "DEGRADED"
            else:
                overall = "HEALTHY"
            
            dashboard["overall_health"] = {
                "status": overall,
                "signal_summary": {
                    "latency": dashboard.get("1_latency", {}).get("status", "UNKNOWN"),
                    "errors": dashboard.get("2_errors", {}).get("status", "UNKNOWN"),
                    "saturation": dashboard.get("3_saturation", {}).get("status", "UNKNOWN"),
                    "traffic": dashboard.get("4_traffic", {}).get("status", "UNKNOWN"),
                },
                "issues": issues if issues else ["All signals healthy - no issues detected"],
                "recommendation": (
                    "Immediate attention required - critical signals detected" if overall == "CRITICAL"
                    else "Some signals degraded - investigate proactively" if overall == "DEGRADED"
                    else "All systems operating within SLA targets"
                )
            }
            
            return dashboard
        
        else:
            return {"error": f"Unknown tool: {tool_name}"}
            
    except Exception as e:
        return {"error": str(e)}


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Starting MCP Server on port {port}")
    app.run(host='0.0.0.0', port=port)
