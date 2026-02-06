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
    """Query IBM Cloud Logs using DataPrime syntax. Returns a list of parsed log messages."""
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
    
    # Parse SSE response into clean log entries
    return parse_cloud_logs_response(response.text)


def parse_cloud_logs_response(raw_text):
    """Parse IBM Cloud Logs SSE response into clean, readable log entries."""
    import re
    logs = []
    
    # The SSE response contains lines like: data: {"result": {"results": [...]}}
    # Extract all JSON data lines
    for line in raw_text.split('\n'):
        line = line.strip()
        if not line.startswith('data:'):
            continue
        json_str = line[5:].strip()  # Remove 'data: ' prefix
        if not json_str:
            continue
        try:
            data = json.loads(json_str)
            # Skip query_id acknowledgements
            if 'query_id' in data:
                continue
            # Extract results
            results = data.get('result', {}).get('results', [])
            for entry in results:
                log_entry = {}
                
                # Extract metadata (timestamp, severity)
                for meta in entry.get('metadata', []):
                    if meta.get('key') == 'timestamp':
                        log_entry['timestamp'] = meta.get('value', '')
                    elif meta.get('key') == 'severity':
                        sev_val = meta.get('value', '3')
                        severity_map = {'1': 'DEBUG', '2': 'VERBOSE', '3': 'INFO', '4': 'WARNING', '5': 'ERROR', '6': 'CRITICAL'}
                        log_entry['severity'] = severity_map.get(str(sev_val), str(sev_val))
                
                # Extract the actual log message from user_data
                user_data_str = entry.get('user_data', '')
                if user_data_str:
                    try:
                        user_data = json.loads(user_data_str)
                        # Get the message
                        msg_obj = user_data.get('message', {})
                        if isinstance(msg_obj, dict):
                            log_entry['message'] = msg_obj.get('message', '')
                            log_entry['app_instance'] = msg_obj.get('_app', '')
                        elif isinstance(msg_obj, str):
                            log_entry['message'] = msg_obj
                        
                        # Get labels
                        labels = user_data.get('label', {})
                        log_entry['project'] = labels.get('Project', '')
                        log_entry['stream'] = labels.get('Stream', '')
                    except (json.JSONDecodeError, TypeError):
                        log_entry['message'] = user_data_str
                
                # Only add entries that have a message
                if log_entry.get('message'):
                    logs.append(log_entry)
        except (json.JSONDecodeError, TypeError):
            continue
    
    return logs


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
    total_errors = 0
    
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
        
        total_errors += errors
        # SLA: 95% of requests should complete under target
        sla_ok = "PASS" if p95 < 3000 else ("WARNING" if p95 < 5000 else "FAIL")
        
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
            "sla_status": sla_ok,
        }
    
    # Global percentiles across all endpoints
    if all_times:
        all_sorted = sorted(all_times)
        n = len(all_sorted)
        global_p50 = all_sorted[int(n * 0.50)]
        global_p90 = all_sorted[int(n * 0.90)] if n >= 2 else all_sorted[-1]
        global_p95 = all_sorted[int(n * 0.95)] if n >= 2 else all_sorted[-1]
        global_p99 = all_sorted[-1]
        global_avg = sum(all_sorted) / n
        pct_under_3s = round((sum(1 for t in all_times if t < 3000) / n) * 100, 1)
    else:
        global_p50 = global_p90 = global_p95 = global_p99 = global_avg = -1
        pct_under_3s = 0
    
    sla_met = pct_under_3s >= 95
    
    return {
        "status": "success",
        "total_requests": len(all_times),
        "total_errors": total_errors,
        "p50_ms": round(global_p50, 2),
        "p90_ms": round(global_p90, 2),
        "p95_ms": round(global_p95, 2),
        "p99_ms": round(global_p99, 2),
        "avg_ms": round(global_avg, 2),
        "pct_requests_under_3s": pct_under_3s,
        "sla_target": "95% of requests < 3s",
        "sla_met": sla_met,
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
        
        # Get recent logs from all apps (runtime + build)
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
        if request.method == 'GET':
            hours = request.args.get('hours', 1, type=int)
            limit = request.args.get('limit', 50, type=int)
        else:
            data = request.get_json(silent=True) or {}
            hours = data.get('hours', 1)
            limit = data.get('limit', 50)
        
        start_date = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        end_date = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        # Filter for error/exception logs from all apps (runtime + build)
        query = f"source logs | filter $d.message.message ~ 'error|Error|ERROR|exception|Exception|failed|Failed' | limit {limit}"
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
                },
                "hours": {
                    "type": "integer",
                    "description": "Number of hours to look back",
                    "default": 1
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
        "description": "Measure response times (latency) for all app endpoints with percentile metrics (P50/P90/P95/P99). SLA target: 95% of requests must complete under 3 seconds. Takes 5 samples per endpoint.",
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
        "description": "Get a comprehensive SRE dashboard based on Google's 4 Golden Signals. Returns a clean table of metrics with current values, SLA targets (95%), and pass/fail status. Signals: Latency (P50/P90/P95/P99), Errors (error rate, HTTP 5xx, DB errors), Saturation (CPU, memory, instances, OOMKills), Traffic (request volume, seat occupancy). Includes a health score out of 100 and actionable recommendations. Present the metrics array as a table.",
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
            hours = args.get('hours', 1)
            start_date = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
            end_date = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
            query = f"source logs | limit {limit}"
            logs = query_cloud_logs(query, start_date=start_date, end_date=end_date, limit=limit)
            return {
                "status": "success",
                "log_count": len(logs),
                "query": query,
                "logs": logs
            }
        
        elif tool_name == 'get_error_logs':
            hours = args.get('hours', 1)
            limit = args.get('limit', 50)
            start_date = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
            end_date = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
            query = f"source logs | filter $d.message.message ~ 'error|Error|ERROR|exception|Exception|failed|Failed' | limit {limit}"
            logs = query_cloud_logs(query, start_date=start_date, end_date=end_date, limit=limit)
            return {
                "status": "success",
                "error_count": len(logs),
                "time_range": f"Last {hours} hour(s)",
                "has_errors": len(logs) > 0,
                "severity_summary": {
                    "CRITICAL": sum(1 for l in logs if l.get('severity') == 'CRITICAL'),
                    "ERROR": sum(1 for l in logs if l.get('severity') == 'ERROR'),
                    "WARNING": sum(1 for l in logs if l.get('severity') == 'WARNING'),
                },
                "logs": logs
            }
        
        elif tool_name == 'query_logs':
            query = args.get('query', 'source logs | limit 10')
            hours = args.get('hours', 1)
            limit = args.get('limit', 100)
            start_date = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
            end_date = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
            logs = query_cloud_logs(query, start_date=start_date, end_date=end_date, limit=limit)
            return {
                "status": "success",
                "log_count": len(logs),
                "time_range": f"Last {hours} hour(s)",
                "query": query,
                "logs": logs
            }
        
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
            return {
                "status": "success",
                "log_type": "Application Logs (Movie Ticket App)",
                "log_count": len(logs),
                "logs": logs
            }
        
        elif tool_name == 'get_platform_logs':
            limit = args.get('limit', 20)
            # Platform logs - Code Engine builds, deployments, etc.
            query = f"source logs | filter $d.message.message ~ 'build|deploy|Dockerfile|docker|pushing|exporting' | limit {limit}"
            logs = query_cloud_logs(query, limit=limit)
            return {
                "status": "success",
                "log_type": "Platform Logs (Code Engine)",
                "log_count": len(logs),
                "logs": logs
            }
        
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
            issues = []
            
            # ---- Collect all raw data first ----
            # Latency
            latency = measure_response_times(num_samples=5)
            p50 = latency.get('p50_ms', -1)
            p90 = latency.get('p90_ms', -1)
            p95 = latency.get('p95_ms', -1)
            p99 = latency.get('p99_ms', -1)
            pct_under_3s = latency.get('pct_requests_under_3s', 0)
            latency_sla_met = latency.get('sla_met', False)
            
            # Error rate
            total_sampled = latency.get('total_requests', 0)
            total_errors = latency.get('total_errors', 0)
            error_rate = round((total_errors / total_sampled) * 100, 2) if total_sampled > 0 else 0
            
            # App & DB health
            try:
                app_resp = requests.get(APP_URL, timeout=30)
                app_status = 'UP' if app_resp.status_code == 200 else 'DOWN'
                app_latency_ms = round(app_resp.elapsed.total_seconds() * 1000)
            except:
                app_status = 'DOWN'
                app_latency_ms = -1
            
            try:
                db_resp = requests.get(f"{APP_URL}/get", timeout=30)
                db_status = 'UP' if db_resp.status_code == 200 else 'DOWN'
                db_latency_ms = round(db_resp.elapsed.total_seconds() * 1000)
            except:
                db_status = 'DOWN'
                db_latency_ms = -1
            
            # Error logs
            error_query = "source logs | filter $d.label.Project == 'movie-ticket-project' | filter $d.message.message ~ 'error|Error|ERROR|exception|Exception|500|502|503' | limit 100"
            error_logs = query_cloud_logs(error_query, limit=100)
            log_error_count = len(error_logs) if isinstance(error_logs, list) else 0
            
            # Categorize
            http_5xx = db_errs = exceptions = 0
            if isinstance(error_logs, list):
                for log in error_logs:
                    msg = str(log).lower()
                    if any(c in msg for c in ['500', '502', '503', '504']):
                        http_5xx += 1
                    elif any(c in msg for c in ['psycopg2', 'database', 'operationalerror']):
                        db_errs += 1
                    elif any(c in msg for c in ['exception', 'traceback']):
                        exceptions += 1
            
            # Saturation (instances, CPU, memory)
            instance_count = 0
            cpu_limit = 'N/A'
            memory_limit = 'N/A'
            total_restarts = 0
            oom_killed = False
            min_scale = 'N/A'
            max_scale = 'N/A'
            try:
                app_info = find_app('movie-ticket')
                if app_info:
                    inst_data = get_app_instances(app_info['project_id'], app_info['app_name'])
                    instance_list = inst_data.get('instances', [])
                    instance_count = inst_data.get('instance_count', 0)
                    total_restarts = sum(i.get('restarts', 0) for i in instance_list)
                    oom_killed = any(i.get('container_reason') == 'OOMKilled' for i in instance_list)
                    cpu_limit = app_info.get('cpu_limit', 'N/A')
                    memory_limit = app_info.get('memory_limit', 'N/A')
                    min_scale = app_info.get('min_scale', 'N/A')
                    max_scale = app_info.get('max_scale', 'N/A')
            except:
                pass
            
            # Traffic / seat occupancy
            total_seats = booked_seats = available_seats = 0
            occupancy_pct = 0.0
            try:
                seats_resp = requests.get(f"{APP_URL}/get", timeout=15)
                if seats_resp.status_code == 200:
                    seat_data = seats_resp.json()
                    total_seats = len(seat_data)
                    booked_seats = sum(1 for s in seat_data.values() if s == 'blocked')
                    available_seats = total_seats - booked_seats
                    occupancy_pct = round((booked_seats / total_seats) * 100, 1) if total_seats > 0 else 0
            except:
                pass
            
            # Traffic from logs
            traffic_query = "source logs | filter $d.label.Project == 'movie-ticket-project' | filter $d.message.message ~ 'GET|POST|request' | limit 100"
            traffic_logs = query_cloud_logs(traffic_query, limit=100)
            request_count = len(traffic_logs) if isinstance(traffic_logs, list) else 0
            
            booking_query = "source logs | filter $d.label.Project == 'movie-ticket-project' | filter $d.message.message ~ 'update|INSERT|Reservation' | limit 100"
            booking_logs = query_cloud_logs(booking_query, limit=100)
            booking_count = len(booking_logs) if isinstance(booking_logs, list) else 0
            
            # ---- Build issues list ----
            if not latency_sla_met:
                issues.append(f"Latency SLA not met: only {pct_under_3s}% requests under 3s (target: 95%)")
            if app_status == 'DOWN':
                issues.append("Application is DOWN")
            if db_status == 'DOWN':
                issues.append("Database is DOWN")
            if error_rate > 5:
                issues.append(f"High request error rate: {error_rate}%")
            if log_error_count > 10:
                issues.append(f"{log_error_count} errors found in recent logs")
            if oom_killed:
                issues.append("OOMKilled detected — memory limit exceeded")
            if total_restarts > 5:
                issues.append(f"High container restart count: {total_restarts}")
            if instance_count == 0:
                issues.append("No running instances — app may be scaled to zero")
            if request_count == 0:
                issues.append("No traffic detected in recent logs")
            
            # ---- Determine per-signal and overall status ----
            def signal_status(conditions_critical, conditions_degraded):
                if any(conditions_critical):
                    return 'CRITICAL'
                if any(conditions_degraded):
                    return 'DEGRADED'
                return 'HEALTHY'
            
            latency_status = signal_status(
                [p95 > 10000],
                [p95 > 3000 or not latency_sla_met]
            )
            error_status = signal_status(
                [app_status == 'DOWN', db_status == 'DOWN', error_rate > 5],
                [error_rate > 1, log_error_count > 10]
            )
            saturation_status = signal_status(
                [oom_killed, instance_count == 0],
                [total_restarts > 5]
            )
            traffic_status = signal_status(
                [],
                [request_count == 0]
            )
            
            statuses = [latency_status, error_status, saturation_status, traffic_status]
            if 'CRITICAL' in statuses:
                overall = 'CRITICAL'
            elif 'DEGRADED' in statuses:
                overall = 'DEGRADED'
            else:
                overall = 'HEALTHY'
            
            # ---- Health score (0-100) ----
            score = 100
            if app_status == 'DOWN': score -= 30
            if db_status == 'DOWN': score -= 25
            if not latency_sla_met: score -= 10
            if error_rate > 1: score -= 10
            if oom_killed: score -= 15
            if total_restarts > 5: score -= 5
            if instance_count == 0: score -= 20
            if log_error_count > 10: score -= 5
            score = max(0, score)
            
            # ---- Build clean, flat, table-friendly response ----
            return {
                "title": "SRE Dashboard - Movie Ticket Booking Application",
                "timestamp": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC'),
                "overall_health": overall,
                "health_score": f"{score}/100",
                "metrics": [
                    {
                        "signal": "Latency",
                        "status": latency_status,
                        "metric": "P50 / P90 / P95 / P99",
                        "current_value": f"{round(p50)} ms / {round(p90)} ms / {round(p95)} ms / {round(p99)} ms",
                        "target": "95% of requests complete in < 3 seconds",
                        "sla_met": latency_sla_met,
                        "sla_compliance": f"{pct_under_3s}% under 3s"
                    },
                    {
                        "signal": "Errors",
                        "status": error_status,
                        "metric": "Error Rate",
                        "current_value": f"{error_rate}% ({total_errors} failed out of {total_sampled} sampled requests)",
                        "target": "< 1% error rate (SLA: 95% success)",
                        "sla_met": error_rate <= 1,
                        "breakdown": f"HTTP 5xx: {http_5xx}, DB errors: {db_errs}, Exceptions: {exceptions}, Log errors (1hr): {log_error_count}"
                    },
                    {
                        "signal": "Errors",
                        "status": "UP" if app_status == 'UP' and db_status == 'UP' else "DOWN",
                        "metric": "App & DB Health",
                        "current_value": f"App: {app_status} ({app_latency_ms} ms), DB: {db_status} ({db_latency_ms} ms)",
                        "target": "Both UP with < 5s response",
                        "sla_met": app_status == 'UP' and db_status == 'UP'
                    },
                    {
                        "signal": "Saturation",
                        "status": saturation_status,
                        "metric": "CPU / Memory Limits",
                        "current_value": f"CPU: {cpu_limit}, Memory: {memory_limit}",
                        "target": "No OOMKills, restarts < 5",
                        "sla_met": not oom_killed and total_restarts <= 5
                    },
                    {
                        "signal": "Saturation",
                        "status": saturation_status,
                        "metric": "Instances & Restarts",
                        "current_value": f"{instance_count} running (scale: {min_scale}-{max_scale}), Restarts: {total_restarts}, OOMKilled: {'Yes' if oom_killed else 'No'}",
                        "target": "≥ 1 instance running, 0 OOMKills",
                        "sla_met": instance_count >= 1 and not oom_killed
                    },
                    {
                        "signal": "Traffic",
                        "status": traffic_status,
                        "metric": "Request Volume",
                        "current_value": f"{request_count} requests in recent logs, {booking_count} booking transactions",
                        "target": "Non-zero traffic",
                        "sla_met": request_count > 0
                    },
                    {
                        "signal": "Traffic",
                        "status": traffic_status,
                        "metric": "Seat Occupancy",
                        "current_value": f"{booked_seats}/{total_seats} seats booked ({occupancy_pct}%)",
                        "target": "Informational",
                        "sla_met": True
                    }
                ],
                "issues": issues if issues else ["No issues — all signals are healthy"],
                "recommendation": (
                    "CRITICAL: Immediate attention required" if overall == 'CRITICAL'
                    else "DEGRADED: Investigate proactively" if overall == 'DEGRADED'
                    else "All systems operating within SLA targets"
                ),
                "endpoint_details": latency.get('endpoints', {})
            }
        
        else:
            return {"error": f"Unknown tool: {tool_name}"}
            
    except Exception as e:
        return {"error": str(e)}


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Starting MCP Server on port {port}")
    app.run(host='0.0.0.0', port=port)
