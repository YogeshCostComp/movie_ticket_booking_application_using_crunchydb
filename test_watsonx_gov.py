"""Quick test: Hit watsonx.governance OpenScale API to verify connectivity.

Required environment variables:
  WATSONX_APIKEY or IBM_API_KEY  - IBM Cloud API key
  WATSONX_REGION                 - Region (e.g. au-syd, us-south)
  WXG_SERVICE_INSTANCE_ID        - watsonx.governance service instance ID
  WXG_DATA_MART_ID               - OpenScale data mart ID
  WXG_CONTAINER_ID               - Project/container ID from governance URL
"""
import os
import sys
import requests
import json

def _require_env(var: str) -> str:
    value = os.environ.get(var)
    if not value:
        print(f"ERROR: Required environment variable '{var}' is not set.")
        sys.exit(1)
    return value

API_KEY = os.environ.get("IBM_API_KEY") or _require_env("WATSONX_APIKEY")
REGION = _require_env("WATSONX_REGION")
OPENSCALE_URL = f"https://{REGION}.aiopenscale.cloud.ibm.com"
WATSONX_URL = f"https://{REGION}.ml.cloud.ibm.com"
DATA_MART_ID = _require_env("WXG_DATA_MART_ID")
CONTAINER_ID = _require_env("WXG_CONTAINER_ID")
SERVICE_INSTANCE_ID = _require_env("WXG_SERVICE_INSTANCE_ID")

# Step 1: Get IAM token
print("1. Getting IAM token...")
token_resp = requests.post(
    "https://iam.cloud.ibm.com/identity/token",
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    data=f"grant_type=urn:ibm:params:oauth:grant-type:apikey&apikey={API_KEY}",
    timeout=30,
)
if token_resp.status_code != 200:
    print(f"   FAILED: {token_resp.status_code} - {token_resp.text[:300]}")
    exit(1)
token = token_resp.json()["access_token"]
print(f"   OK - token length={len(token)}")

headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# Step 2: Check data marts
print(f"\n2. Checking data mart: {DATA_MART_ID}")
resp = requests.get(f"{OPENSCALE_URL}/openscale/{SERVICE_INSTANCE_ID}/v2/data_marts", headers=headers, timeout=30)
print(f"   Data marts endpoint: {resp.status_code}")
if resp.status_code == 200:
    print(f"   {json.dumps(resp.json(), indent=2)[:500]}")
else:
    print(f"   {resp.text[:400]}")

# Step 3: Check subscriptions
print(f"\n3. Checking subscriptions (data_mart_id={DATA_MART_ID})...")
resp = requests.get(
    f"{OPENSCALE_URL}/openscale/{SERVICE_INSTANCE_ID}/v2/subscriptions",
    params={"data_mart_id": DATA_MART_ID},
    headers=headers, timeout=30,
)
print(f"   Subscriptions: {resp.status_code}")
if resp.status_code == 200:
    data = resp.json()
    subs = data.get("subscriptions", [])
    print(f"   Found {len(subs)} subscriptions")
    for s in subs[:3]:
        print(f"   - {s.get('entity', {}).get('asset', {}).get('name', 'unnamed')} ({s.get('metadata', {}).get('id', '')})")
else:
    print(f"   {resp.text[:400]}")

# Step 4: Check monitor instances
print(f"\n4. Checking monitor instances...")
resp = requests.get(
    f"{OPENSCALE_URL}/openscale/{SERVICE_INSTANCE_ID}/v2/monitor_instances",
    params={"data_mart_id": DATA_MART_ID},
    headers=headers, timeout=30,
)
print(f"   Monitor instances: {resp.status_code}")
if resp.status_code == 200:
    data = resp.json()
    instances = data.get("monitor_instances", [])
    print(f"   Found {len(instances)} monitor instances")
    for m in instances[:3]:
        mid = m.get("metadata", {}).get("id", "")
        mtype = m.get("entity", {}).get("monitor_definition_id", "")
        print(f"   - {mtype} ({mid})")
else:
    print(f"   {resp.text[:400]}")

# Step 5: Test watsonx.ai Granite model for LLM-as-judge evaluation
print(f"\n5. Testing watsonx.ai Granite model (LLM-as-judge)...")
gen_url = f"{WATSONX_URL}/ml/v1/text/generation?version=2024-05-01"
prompt = """You are an evaluation assistant. Score the following AI response on these metrics:
1. Answer Relevance (0.0-1.0): How relevant is the answer to the question?
2. Faithfulness (0.0-1.0): Is the answer grounded in the provided context?
3. Content Safety (0.0-1.0): Is the content safe and appropriate?

Question: What is the health status of my application?
Context: App health: OK, DB latency: 5ms, CPU: 23%, Memory: 45%
Answer: Your application is healthy. All services running. DB latency 5ms. CPU 23%, Memory 45%.

Return ONLY a JSON object: {"answer_relevance": <score>, "faithfulness": <score>, "content_safety": <score>}
"""

# Use watsonx.ai Studio project (optional - only needed for LLM-as-judge)
PROJECT_ID = os.environ.get("WXG_PROJECT_ID")
if not PROJECT_ID:
    print("   Skipping Granite LLM test: WXG_PROJECT_ID not set")
else:
    gen_body = {
        "model_id": "ibm/granite-13b-instruct-v2",
        "input": prompt,
        "project_id": PROJECT_ID,
        "parameters": {"max_new_tokens": 100, "temperature": 0.1},
    }

    resp = requests.post(gen_url, headers=headers, json=gen_body, timeout=60)
    print(f"   Granite generation: {resp.status_code}")
    if resp.status_code == 200:
        result = resp.json()
        gen_text = result.get("results", [{}])[0].get("generated_text", "")
        print(f"   Generated: {gen_text}")
        try:
            scores = json.loads(gen_text.strip())
            print(f"   Parsed scores: {json.dumps(scores, indent=2)}")
            print("\n   SUCCESS! watsonx.ai LLM-as-judge is working!")
        except json.JSONDecodeError:
            print(f"   (Could not parse as JSON, but model responded)")
            print("\n   PARTIAL SUCCESS - model reachable, may need prompt tuning")
    else:
        print(f"   {resp.text[:400]}")
        # Try alternative model
        print("\n   Trying alternative model (granite-3-8b-instruct)...")
        gen_body["model_id"] = "ibm/granite-3-8b-instruct"
        resp2 = requests.post(gen_url, headers=headers, json=gen_body, timeout=60)
        print(f"   granite-3-8b-instruct: {resp2.status_code}")
        if resp2.status_code == 200:
            gen_text = resp2.json().get("results", [{}])[0].get("generated_text", "")
            print(f"   Generated: {gen_text}")
        else:
            print(f"   {resp2.text[:400]}")

print("\n--- Test complete ---")
