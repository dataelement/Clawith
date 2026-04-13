# Clawith Helm Deployment Quick Start Guide

## 📋 Directory Structure

```
helm/
├── clawith/                    # Helm Chart Main Directory
│   ├── Chart.yaml             # Chart Metadata
│   ├── values.yaml            # Configuration File
│   ├── README.md              # Detailed Documentation
│   └── templates/             # Kubernetes Resource Templates
│       ├── _helpers.tpl       # Template Helper Functions
│       ├── namespace.yaml     # Namespace
│       ├── secrets.yaml       # Secrets
│       ├── backend.yaml       # Backend Service
│       ├── frontend.yaml      # Frontend Service
│       ├── ingress.yaml       # Ingress Configuration
│       ├── postgresql.yaml    # PostgreSQL Database
│       ├── redis.yaml         # Redis Cache
│       └── storageclass.yaml  # Storage Class (Optional)
└── QUICKSTART.md              # This Document
```

## ⚠️ Important Notice

**K8s Deployment Limitations:**
- ✅ Current Helm Chart **only supports Native Agent deployment mode**
- ❌ **Does NOT support OpenClaw Agent hosting mode**
- If you need to use OpenClaw Agent hosting, please use Docker Compose or other deployment methods

Native Agent is the built-in proxy mode of Clawith, suitable for Kubernetes environments. OpenClaw Agent hosting mode is currently only supported in Docker Compose environments.

## 🚀 Quick Start

### 1. Edit Configuration File

Edit `helm/clawith/values.yaml` and modify it according to your environment:

```bash
vi helm/clawith/values.yaml
```

**Required Configuration Items:**

```yaml
# 1. Configure Image Registry
global:
  imageRegistry: docker.io/yourusername  # Change to your image registry

# 2. Configure Image Tags
backend:
  image:
    tag: latest  # Recommended to use specific version, e.g., v1.0.0

frontend:
  image:
    tag: latest  # Recommended to use specific version, e.g., v1.0.0

# 3. Configure Storage
backend:
  persistence:
    existingClaim: ""  # If using existing PVC, enter PVC name
    storageClass: ""  # If creating new, change to your StorageClass name
    size: 10Gi

postgresql:
  image:
    registry: docker.io/bitnami  # Change to your image registry
  auth:
    password: "clawith123456"  # Strongly recommended to change to a strong password!
  primary:
    persistence:
      existingClaim: ""  # If using existing PVC, enter PVC name
      storageClass: ""  # If creating new, change to your StorageClass name
      size: 8Gi

redis:
  image:
    registry: docker.io  # Change to your image registry
  persistence:
    existingClaim: ""  # If using existing PVC, enter PVC name
    storageClass: ""  # If creating new, change to your StorageClass name
    size: 2Gi

# 4. Configure Domain
frontend:
  ingress:
    host: "clawith.example.com"  # Change to your domain

# 5. Modify Application Secrets (Important!)
backend:
  secrets:
    secretKey: "your-secret-key-at-least-50-characters-long"
    jwtSecretKey: "your-jwt-secret-key-at-least-32-characters"

# 6. Enable hostCerts if private certificate signing support is needed
backend:
  hostCerts:
    enabled: false  # Set to true if needed
```

### 2. Install

```bash
helm install clawith ./helm/clawith -n clawith --create-namespace
```

### 3. Verify Deployment

```bash
# Check Pod status
kubectl get pods -n clawith

# Expected output:
# NAME                                  READY   STATUS    RESTARTS   AGE
# clawith-backend-xxx                   1/1     Running   0          2m
# clawith-frontend-xxx                  1/1     Running   0          2m
# clawith-postgresql-0                  1/1     Running   0          2m
# clawith-redis-xxx                     1/1     Running   0          2m

# Check Services and Ingress
kubectl get svc,ingress -n clawith
```

## 🔧 Common Configuration Scenarios

### Scenario 1: Using Existing PVC (Existing Storage)

```yaml
backend:
  persistence:
    enabled: true
    existingClaim: "clawith-agent-data-pvc"  # Your PVC name
    # No need to specify storageClass and size

postgresql:
  primary:
    persistence:
      enabled: true
      existingClaim: "clawith-postgresql-data"  # Your PVC name

redis:
  persistence:
    enabled: true
    existingClaim: "redisdata"  # Your PVC name
```

### Scenario 2: Creating New PVC (Dynamic Storage)

```yaml
backend:
  persistence:
    enabled: true
    existingClaim: ""  # Leave empty
    storageClass: "nfs-client"  # Your StorageClass name
    size: 10Gi

postgresql:
  primary:
    persistence:
      enabled: true
      existingClaim: ""
      storageClass: "nfs-client"
      size: 8Gi

redis:
  persistence:
    enabled: true
    existingClaim: ""
    storageClass: "nfs-client"
    size: 2Gi
```

### Scenario 3: Configure Image Registry

If using private image registry or different image sources:

```yaml
global:
  imageRegistry: registry.example.com/myproject  # Private registry

backend:
  image:
    repository: clawith-backend
    tag: v1.0.0  # Use specific version

frontend:
  image:
    repository: clawith-frontend
    tag: v1.0.0

postgresql:
  image:
    registry: registry.example.com/bitnami
    repository: bitnami/postgresql
    tag: 15.3.0-debian-11-r7

redis:
  image:
    registry: registry.example.com
    repository: redis
    tag: 7-alpine
```

### Scenario 4: Enable Private Certificate Support

If your environment requires custom CA certificates (e.g., corporate intranet):

```yaml
backend:
  hostCerts:
    enabled: true
    paths:
      certs: /etc/ssl/certs
      shareCA: /usr/local/share/ca-certificates
    containerPaths:
      sslCertFile: /app/cacert.pem
      requestsCaBundle: /app/cacert.pem
      curlCaBundle: /app/cacert.pem
```

### Scenario 5: Using External Database

If you have independent PostgreSQL and Redis services:

```yaml
postgresql:
  enabled: false
  external:
    host: "postgresql.example.com"
    port: 5432
    database: clawith
    username: postgres
    password: "your-password"

redis:
  enabled: false
  external:
    host: "redis.example.com"
    port: 6379
    database: 0
    password: ""  # If password is required
```

### Scenario 6: Production Environment Configuration

```yaml
global:
  imageRegistry: registry.yourcompany.com/clawith

backend:
  replicaCount: 2  # Multiple replicas
  image:
    tag: v1.0.0  # Use fixed version
  resources:
    limits:
      cpu: 2000m
      memory: 4Gi
    requests:
      cpu: 500m
      memory: 1Gi
  persistence:
    storageClass: "ssd-storage"  # High-performance storage
    size: 50Gi

frontend:
  replicaCount: 2
  image:
    tag: v1.0.0
  ingress:
    enabled: true
    annotations:
      cert-manager.io/cluster-issuer: "letsencrypt-prod"
      nginx.ingress.kubernetes.io/ssl-redirect: "true"
    host: "clawith.yourcompany.com"
    tls:
      enabled: true
      secretName: clawith-tls-secret

postgresql:
  auth:
    password: "STRONG_PASSWORD_HERE"  # Must use strong password
  primary:
    persistence:
      storageClass: "ssd-storage"
      size: 20Gi
    resources:
      limits:
        cpu: 2000m
        memory: 2Gi
      requests:
        cpu: 500m
        memory: 512Mi

redis:
  persistence:
    storageClass: "ssd-storage"
    size: 5Gi
  resources:
    limits:
      cpu: 1000m
      memory: 1Gi
    requests:
      cpu: 250m
      memory: 256Mi
```

## 📝 Common Commands

### Check Status

```bash
# View all resources
kubectl get all -n clawith

# Check Pod status
kubectl get pods -n clawith

# Check PVCs
kubectl get pvc -n clawith

# Check Helm release status
helm status clawith -n clawith

# Check Helm deployment values
helm get values clawith -n clawith
```

### View Logs

```bash
# Backend logs
kubectl logs -n clawith -l app.kubernetes.io/component=backend -f

# Frontend logs
kubectl logs -n clawith -l app.kubernetes.io/component=frontend -f

# PostgreSQL logs
kubectl logs -n clawith -l app.kubernetes.io/component=postgresql -f

# Redis logs
kubectl logs -n clawith -l app.kubernetes.io/component=redis -f
```

### Upgrade

```bash
# Upgrade after modifying values.yaml
helm upgrade clawith ./helm/clawith -n clawith

# Or use --set to override specific values
helm upgrade clawith ./helm/clawith -n clawith \
  --set backend.image.tag=v1.0.1 \
  --set frontend.image.tag=v1.0.1

# Upgrade image registry
helm upgrade clawith ./helm/clawith -n clawith \
  --set global.imageRegistry=registry.example.com/newproject
```

### Rollback

```bash
# View revision history
helm history clawith -n clawith

# Rollback to previous revision
helm rollback clawith -n clawith

# Rollback to specific revision
helm rollback clawith 1 -n clawith
```

### Uninstall

```bash
# Uninstall application (keep PVCs)
helm uninstall clawith -n clawith

# If you need to delete PVCs
kubectl delete pvc -n clawith --all

# Delete namespace
kubectl delete namespace clawith
```

## 🔍 Access Application

### Access via Ingress (Recommended)

If Ingress is configured, access directly via domain:
```
http://clawith.example.com  # Or your configured domain
```

### Access via Port Forward

If Ingress is not configured, use port forwarding:

```bash
# Forward frontend service
kubectl port-forward -n clawith svc/clawith-frontend 8080:80

# Then access http://localhost:8080
```

```bash
# Forward backend service (for API debugging)
kubectl port-forward -n clawith svc/clawith-backend 8000:8000

# Then access http://localhost:8000
```

## 🛠️ Troubleshooting

### Pod Cannot Start

```bash
# Check Pod details
kubectl describe pod <pod-name> -n clawith

# Check logs
kubectl logs <pod-name> -n clawith

# Check events
kubectl get events -n clawith --sort-by='.lastTimestamp'
```

### PVC Binding Failure

```bash
# Check PVC status
kubectl get pvc -n clawith
kubectl describe pvc <pvc-name> -n clawith

# Check StorageClass
kubectl get storageclass

# Check PV
kubectl get pv
```

### Image Pull Failure

```bash
# Check image configuration
helm get values clawith -n clawith | grep -A 3 image

# Check Pod events
kubectl describe pod <pod-name> -n clawith | grep -A 10 Events

# Manually test image pull
docker pull your-registry/clawith-backend:latest
```

### Database Connection Issues

```bash
# Check PostgreSQL service
kubectl get svc -n clawith | grep postgresql

# Check database password
kubectl get secret -n clawith -o yaml | grep postgres-password

# Enter backend Pod to test connection
kubectl exec -it -n clawith deployment/clawith-backend -- /bin/bash
# Test inside Pod
nc -zv clawith-postgresql 5432
```

## 🔐 Security Recommendations

### 1. Change Default Passwords

⚠️ **Important**: Must change all default passwords before deployment!

```yaml
backend:
  secrets:
    secretKey: "Generate a random string of at least 50 characters"
    jwtSecretKey: "Generate a random string of at least 32 characters"

postgresql:
  auth:
    password: "Generate a strong password"  # Do not use default clawith123456
```

Methods to generate random passwords:
```bash
# Generate 50-character random string
openssl rand -base64 36

# Or use Python
python3 -c "import secrets; print(secrets.token_urlsafe(50))"

# Generate 32-character random string
openssl rand -base64 24
```

### 2. Use External Secrets

In production environments, external Secret management is recommended:

```bash
# Create Secret
kubectl create secret generic clawith-secrets \
  --from-literal=secret-key='your-secret-key' \
  --from-literal=jwt-secret-key='your-jwt-secret' \
  -n clawith

# Configure in values.yaml
secrets:
  create: false
  existingSecret: "clawith-secrets"
```

### 3. Enable HTTPS

```yaml
frontend:
  ingress:
    enabled: true
    annotations:
      cert-manager.io/cluster-issuer: "letsencrypt-prod"
      nginx.ingress.kubernetes.io/ssl-redirect: "true"
    host: "clawith.yourcompany.com"
    tls:
      enabled: true
      secretName: clawith-tls-secret
```

### 4. Configure Resource Limits

```yaml
backend:
  resources:
    limits:
      cpu: 2000m
      memory: 4Gi
    requests:
      cpu: 500m
      memory: 1Gi
```

### 5. Use Private Image Registry

```yaml
global:
  imageRegistry: registry.yourcompany.com/clawith

# If authentication is required, create imagePullSecret
# kubectl create secret docker-registry regcred \
#   --docker-server=registry.yourcompany.com \
#   --docker-username=user \
#   --docker-password=password \
#   -n clawith
```

## 💡 Practical Tips

### Preview Deployment Content

Before actual deployment, preview the generated YAML:

```bash
# Render template without installing
helm template clawith ./helm/clawith -n clawith > preview.yaml

# Or use --dry-run
helm install clawith ./helm/clawith -n clawith --dry-run --debug
```

### Compare Configuration Differences

Install helm-diff plugin to compare configuration changes:

```bash
# Install plugin
helm plugin install https://github.com/databus23/helm-diff

# View upgrade differences
helm diff upgrade clawith ./helm/clawith -n clawith
```

### Export Current Configuration

```bash
# Export currently used values
helm get values clawith -n clawith > current-values.yaml

# Export complete manifest
helm get manifest clawith -n clawith > current-manifest.yaml
```

### Update Specific Components Only

```bash
# Update backend image version only
helm upgrade clawith ./helm/clawith -n clawith \
  --set backend.image.tag=v1.0.1 \
  --reuse-values

# Update frontend configuration only
helm upgrade clawith ./helm/clawith -n clawith \
  --set frontend.ingress.host=new.example.com \
  --reuse-values

# Update image registry
helm upgrade clawith ./helm/clawith -n clawith \
  --set global.imageRegistry=new-registry.com/project \
  --reuse-values
```

## 📊 Monitoring and Maintenance

### Check Resource Usage

```bash
# Check Pod resource usage
kubectl top pods -n clawith

# Check Node resource usage
kubectl top nodes

# Check PVC usage
kubectl get pvc -n clawith
```

### Regular Backups

**Backup PostgreSQL Data:**

```bash
# Export database
kubectl exec -n clawith clawith-postgresql-0 -- \
  pg_dump -U postgres clawith > backup-$(date +%Y%m%d).sql

# Restore database
kubectl exec -i -n clawith clawith-postgresql-0 -- \
  psql -U postgres clawith < backup-20260402.sql
```

**Backup Helm Configuration:**

```bash
# Backup current configuration
helm get values clawith -n clawith > backup-values-$(date +%Y%m%d).yaml
```

## 🎯 Comparison with Original K8s Deployment

| Feature | Original K8s YAML | Helm Chart |
|---------|-------------------|------------|
| Configuration Management | Scattered in multiple files | Centralized in values.yaml |
| Version Control | Manual management | Automatically tracked by Helm |
| Upgrade | Apply one by one | `helm upgrade` single command |
| Rollback | Difficult | `helm rollback` single command |
| Parameterization | Manual replacement | Automatic template rendering |
| Environment Management | Copy multiple YAML files | One template + values.yaml |
| Dependency Management | Manual sequence | Automatically handled by Helm |
| Maintainability | Low | High |

## 📚 More Information

- **Detailed Configuration**: `helm/clawith/README.md`
- **Helm Official Documentation**: https://helm.sh/docs/
- **Kubernetes Documentation**: https://kubernetes.io/docs/

## ❓ FAQ

**Q: How to check current configuration?**
```bash
helm get values clawith -n clawith
```

**Q: How to update only one configuration item without affecting others?**
```bash
helm upgrade clawith ./helm/clawith -n clawith --reuse-values --set backend.image.tag=v1.0.1
```

**Q: How to preserve data after uninstallation?**
```bash
# Helm uninstall does not delete PVCs by default, data is preserved
helm uninstall clawith -n clawith
# PVC still exists and can be reused during next installation
```

**Q: How to check why deployment failed?**
```bash
kubectl get events -n clawith --sort-by='.lastTimestamp'
kubectl logs -n clawith <pod-name>
helm status clawith -n clawith
```

**Q: How to change image registry?**
```bash
# Method 1: Modify global.imageRegistry in values.yaml
# Method 2: Use --set parameter
helm upgrade clawith ./helm/clawith -n clawith \
  --set global.imageRegistry=new-registry.com/project
```

**Q: How to verify if StorageClass is available?**
```bash
# Check available StorageClasses
kubectl get storageclass

# Check StorageClass details
kubectl describe storageclass nfs-client
```

---

**Happy deploying!** 🎉
