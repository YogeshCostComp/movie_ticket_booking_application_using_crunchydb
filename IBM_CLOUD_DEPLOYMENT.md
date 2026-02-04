# IBM Cloud Deployment Guide for Movie Ticket Booking Application

This guide walks you through deploying the Movie Ticket Booking Application on IBM Cloud using Red Hat OpenShift and Crunchy PostgreSQL.

## Prerequisites

- IBM Cloud Account
- IBM Cloud CLI installed
- Docker installed locally
- Red Hat Marketplace Account

---

## Step 1: Create an OpenShift Cluster on IBM Cloud

1. **Login to IBM Cloud Console**: https://cloud.ibm.com

2. **Navigate to OpenShift**:
   - Go to **Navigation Menu** → **Kubernetes** → **Clusters**
   - Click **Create cluster**

3. **Configure the cluster**:
   - **Plan**: Choose Standard (for production) or try the free tier for testing
   - **Infrastructure**: Classic or VPC
   - **Location**: Select your preferred region
   - **OpenShift version**: 4.10 or higher (must be 4.3+)
   - **Worker pool**: 
     - Flavor: 4 vCPU, 16GB RAM (minimum recommended)
     - Worker nodes: 2-3 nodes
   - **Cluster name**: `movie-app-cluster`

4. **Create the cluster** (takes 20-30 minutes)

---

## Step 2: Install CLI Tools

```bash
# Install IBM Cloud CLI
curl -fsSL https://clis.cloud.ibm.com/install/linux | sh

# Install OpenShift CLI plugin
ibmcloud plugin install kubernetes-service

# Install Container Registry plugin
ibmcloud plugin install container-registry

# Login to IBM Cloud
ibmcloud login --sso

# Get cluster config
ibmcloud ks cluster config --cluster movie-app-cluster

# Verify connection
oc version
kubectl get nodes
```

---

## Step 3: Register Red Hat Marketplace & Install Crunchy PostgreSQL

### 3.1 Register with Red Hat Marketplace

1. Go to: https://marketplace.redhat.com/en-us/registration/om
2. Create an account or login
3. Link your OpenShift cluster to Red Hat Marketplace

### 3.2 Install Crunchy PostgreSQL Operator

1. **In OpenShift Console**:
   - Go to **Operators** → **OperatorHub**
   - Search for "Crunchy PostgreSQL for Kubernetes"
   - Click **Install**
   - Select namespace: `pgo` (create if needed)
   - Click **Subscribe**

2. **Wait for the operator to install** (check Installed Operators)

### 3.3 Create a PostgreSQL Cluster

1. **Create the pgo namespace** (if not exists):
```bash
oc create namespace pgo
```

2. **Create a PostgreSQL cluster** using this YAML:

```bash
cat <<EOF | oc apply -f -
apiVersion: postgres-operator.crunchydata.com/v1beta1
kind: PostgresCluster
metadata:
  name: hippo
  namespace: pgo
spec:
  image: registry.developers.crunchydata.com/crunchydata/crunchy-postgres:ubi8-14.5-1
  postgresVersion: 14
  instances:
    - name: instance1
      replicas: 1
      dataVolumeClaimSpec:
        accessModes:
          - "ReadWriteOnce"
        resources:
          requests:
            storage: 1Gi
  backups:
    pgbackrest:
      image: registry.developers.crunchydata.com/crunchydata/crunchy-pgbackrest:ubi8-2.40-1
      repos:
        - name: repo1
          volume:
            volumeClaimSpec:
              accessModes:
                - "ReadWriteOnce"
              resources:
                requests:
                  storage: 1Gi
EOF
```

3. **Wait for the cluster to be ready**:
```bash
oc get pods -n pgo -w
```

### 3.4 Get Database Credentials

```bash
# Get the password for the hippo user
oc get secret hippo-pguser-hippo -n pgo -o jsonpath='{.data.password}' | base64 -d
echo

# Get the database connection details
oc get secret hippo-pguser-hippo -n pgo -o jsonpath='{.data.host}' | base64 -d
echo

# The service name will be: hippo-primary.pgo.svc.cluster.local
```

**Save these values - you'll need them in Step 5!**

---

## Step 4: Build and Push Docker Image

### 4.1 Create a Container Registry Namespace

```bash
# Login to IBM Cloud
ibmcloud login --sso

# Target the container registry
ibmcloud cr region-set us-south  # or your region

# Create a namespace
ibmcloud cr namespace-add movie-app-ns

# Login to the registry
ibmcloud cr login
```

### 4.2 Build and Push the Image

```bash
# Navigate to your app directory
cd movie_ticket_booking_application_using_crunchydb

# Build the Docker image
docker build -t us.icr.io/movie-app-ns/movie-ticket-app:latest .

# Push to IBM Container Registry
docker push us.icr.io/movie-app-ns/movie-ticket-app:latest

# Verify the image
ibmcloud cr images
```

---

## Step 5: Deploy the Application

### 5.1 Update Configuration Files

1. **Edit `k8s/secret.yaml`** with your CrunchyDB credentials:
```yaml
stringData:
  DB_HOST: "hippo-primary.pgo.svc.cluster.local"  # From Step 3.4
  DB_PORT: "5432"
  DB_NAME: "hippo"
  DB_USER: "hippo"
  DB_PASSWORD: "YOUR_ACTUAL_PASSWORD"  # From Step 3.4
```

2. **Edit `k8s/deployment.yaml`** with your image:
```yaml
image: us.icr.io/movie-app-ns/movie-ticket-app:latest
```

### 5.2 Deploy to OpenShift

```bash
# Create namespace
oc apply -f k8s/namespace.yaml

# Create secret with DB credentials
oc apply -f k8s/secret.yaml

# Deploy the application
oc apply -f k8s/deployment.yaml

# Create the service
oc apply -f k8s/service.yaml

# Create the route (exposes the app externally)
oc apply -f k8s/route.yaml
```

### 5.3 Verify Deployment

```bash
# Check pods are running
oc get pods -n movie-app

# Check the route URL
oc get route -n movie-app
```

---

## Step 6: Initialize the Database

1. **Get the application URL**:
```bash
oc get route movie-ticket-app-route -n movie-app -o jsonpath='{.spec.host}'
```

2. **Open the URL in your browser** and append `/create`:
```
https://YOUR-APP-URL/create
```

This will create the required tables in the database.

3. **Access the application**:
```
https://YOUR-APP-URL/
```

---

## Troubleshooting

### Check Application Logs
```bash
oc logs -f deployment/movie-ticket-app -n movie-app
```

### Check Database Connection
```bash
# Port-forward to test DB locally
oc port-forward svc/hippo-primary 5432:5432 -n pgo
```

### Restart Deployment
```bash
oc rollout restart deployment/movie-ticket-app -n movie-app
```

### Check Events
```bash
oc get events -n movie-app --sort-by='.lastTimestamp'
```

---

## Environment Variables Reference

| Variable | Description | Default |
|----------|-------------|---------|
| DB_HOST | PostgreSQL host | 127.0.0.1 |
| DB_PORT | PostgreSQL port | 5432 |
| DB_NAME | Database name | hippo |
| DB_USER | Database user | hippo |
| DB_PASSWORD | Database password | datalake |

---

## Clean Up

To remove all resources:

```bash
# Delete the application
oc delete -f k8s/route.yaml
oc delete -f k8s/service.yaml
oc delete -f k8s/deployment.yaml
oc delete -f k8s/secret.yaml
oc delete -f k8s/namespace.yaml

# Delete the PostgreSQL cluster (optional)
oc delete postgrescluster hippo -n pgo
```

---

## Cost Considerations

- **OpenShift Cluster**: ~$0.10/hour per worker node (varies by size)
- **Container Registry**: Free for first 500MB
- **Crunchy PostgreSQL**: Licensed through Red Hat Marketplace

Consider using smaller worker nodes for development/testing.
